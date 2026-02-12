"""API routes for memory system."""

import asyncio
import logging
import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.config import get_settings
from app.database import db_manager, get_db_session
from app.models.auth import User
from app.llm_client import get_llm_client
from app.prompts import get_system_prompt  # NEW: Production-grade prompts
from app.models.conversation import ConversationRequest, ConversationResponse
from app.models.memory import (
    Memory,
    MemoryCreate,
    MemorySearchQuery,
    MemorySearchResult,
    MemoryStats,
    MemoryType,
    MemoryUpdate,
)
from app.services.conversation_storage import ConversationStorage
from app.services.conversation_manager import ConversationManager
from app.services.extractor import MemoryExtractor
from app.services.memory_manager import MemoryManager
from app.services.retriever import MemoryRetriever
from app.services.storage import MemoryStorage
from app.utils.embeddings import EmbeddingGenerator
from app.utils.metrics import metrics
from app.utils.temporal import format_relative_time

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1", tags=["memory"])


# Cached embedding generator instance
_embedding_generator_cache = None

# Dependency injection
async def get_embedding_generator():
    """Get embedding generator instance (cached)."""
    global _embedding_generator_cache
    if _embedding_generator_cache is None:
        try:
            _embedding_generator_cache = EmbeddingGenerator(redis_client=db_manager.redis)
        except ImportError as e:
            logger.error(f"Failed to initialize EmbeddingGenerator: {e}")
            logger.error("Make sure sentence-transformers is installed: pip install sentence-transformers")
            raise HTTPException(
                status_code=503,
                detail="Embedding service unavailable. sentence-transformers not installed."
            )
    return _embedding_generator_cache


async def get_memory_storage(
    session: AsyncSession = Depends(get_db_session),
    embedder: EmbeddingGenerator = Depends(get_embedding_generator),
):
    """Get memory storage instance."""
    return MemoryStorage(
        session=session,
        redis_client=db_manager.redis,
        embedding_generator=embedder,
    )


async def get_memory_retriever(
    embedder: EmbeddingGenerator = Depends(get_embedding_generator),
):
    """Get memory retriever instance."""
    return MemoryRetriever(
        redis_client=db_manager.redis,
        embedding_generator=embedder,
    )


async def get_memory_extractor():
    """Get memory extractor instance."""
    return MemoryExtractor()


async def get_conversation_storage(
    session: AsyncSession = Depends(get_db_session),
):
    """Get conversation storage instance."""
    return ConversationStorage(session=session)


async def get_conversation_manager(
    session: AsyncSession = Depends(get_db_session),
):
    """Get conversation manager instance."""
    return ConversationManager(session=session)


async def get_memory_manager(
    storage: MemoryStorage = Depends(get_memory_storage),
    retriever: MemoryRetriever = Depends(get_memory_retriever),
    extractor: MemoryExtractor = Depends(get_memory_extractor),
):
    """Get memory manager instance."""
    return MemoryManager(
        storage=storage,
        retriever=retriever,
        extractor=extractor,
    )


# Helper function for generating conversation title
async def _generate_conversation_title(
    conversation_id: UUID,
    user_id: str,
    first_message: str,
):
    """Background task to generate conversation title from first message."""
    try:
        from app.services.title_generator import title_generator
        from app.services.conversation_manager import ConversationManager
        
        # Generate title
        title = title_generator.generate_title(first_message)
        
        # Update conversation with new title
        async with db_manager._session_factory() as session:
            manager = ConversationManager(session=session)
            await manager.update_conversation(
                conversation_id=conversation_id,
                user_id=user_id,
                title=title,
            )
        
        logger.info(f"Generated title for conversation {conversation_id}: {title}")
    except Exception as e:
        logger.error(f"Failed to generate conversation title: {e}")


# Helper function to check duplicate content
async def _check_duplicate_content(
    storage,
    user_id: str,
    content: str,
    similarity_threshold: float = 0.95
) -> bool:
    """Check if similar content already exists for user.
    
    Args:
        storage: Memory storage instance
        user_id: User ID
        content: Content to check
        similarity_threshold: Cosine similarity threshold (0.95 = 95% similar)
        
    Returns:
        True if duplicate exists
    """
    try:
        # Get recent memories for user (last 50)
        recent_memories = await storage.get_user_memories(user_id=user_id, limit=50)
        
        # Generate embedding for new content
        from app.utils.embeddings import EmbeddingGenerator
        import numpy as np
        
        embedder = EmbeddingGenerator(redis_client=storage.redis)  # ✅ FIX: storage.redis not redis_client
        new_embedding = await embedder.generate(content)  # ✅ FIX: async method is generate() not generate_embedding()
        
        # Check similarity against recent memories
        for existing in recent_memories:
            if existing.embedding:
                # Cosine similarity
                similarity = np.dot(new_embedding, existing.embedding) / (
                    np.linalg.norm(new_embedding) * np.linalg.norm(existing.embedding)
                )
                
                if similarity >= similarity_threshold:
                    logger.info(
                        f"Duplicate detected: '{content[:50]}...' matches "
                        f"'{existing.content[:50]}...' (similarity: {similarity:.2f})"
                    )
                    return True
        
        return False
        
    except Exception as e:
        logger.warning(f"Duplicate check failed: {e}")
        return False  # If check fails, allow creation (safe default)


# Helper function for background memory extraction
async def _extract_and_store_memories_independent(
    user_id: str,
    turn_number: int,
    user_message: str,
    assistant_message: str,
):
    """Background task that creates its own session."""
    logger.info(f"🔍 Background task started for turn {turn_number}")
    try:
        # Create fresh instances with new session
        logger.info(f"📦 Creating new session for memory extraction...")
        async with db_manager._session_factory() as session:
            embedder = EmbeddingGenerator(redis_client=db_manager.redis)
            storage = MemoryStorage(
                session=session,
                redis_client=db_manager.redis,
                embedding_generator=embedder,
            )
            extractor = MemoryExtractor()
            
            # Extract memories
            logger.info(f"🧠 Extracting memories from turn {turn_number}...")
            memories = await extractor.extract_from_turn(
                user_id=user_id,
                turn_number=turn_number,
                user_message=user_message,
                assistant_message=assistant_message,
            )
            
            logger.info(f"✅ Extracted {len(memories)} memories")

            # Get existing user memories for conflict detection
            existing_memories = await storage.get_user_memories(user_id=user_id, limit=200)

            # Store memories, update profile, and check conflicts
            from app.services.profile_manager import profile_manager
            from app.utils.conflict_resolver import MemoryConflictResolver
            from app.services.canonicalizer import CanonicalMemoryResolver
            
            # 🚀 ELITE: Canonical preference resolver (prevents memory duplication)
            canonicalizer = CanonicalMemoryResolver(session)
            
            for memory in memories:
                # 🚀 ELITE: Check if this should update existing canonical memory
                is_canonical_update, existing_id = await canonicalizer.resolve_preference(
                    user_id=user_id,
                    new_content=memory.content,
                    memory_type=memory.type,
                    confidence=memory.confidence,
                    turn_number=turn_number,
                )
                
                if is_canonical_update:
                    # Memory was updated in place - skip creation
                    logger.info(
                        f"🔄 Canonical update: {memory.content[:50]}... "
                        f"(updated memory {existing_id})"
                    )
                    continue  # Don't create duplicate
                
                # ✅ FIX #3: Content-based deduplication check (prevents "hamidafreen84" x100)
                is_duplicate = await _check_duplicate_content(
                    storage=storage,
                    user_id=user_id,
                    content=memory.content,
                    similarity_threshold=0.95  # 95% similarity = duplicate
                )
                
                if is_duplicate:
                    logger.info(f"⏭️ Skipping duplicate memory: {memory.content[:50]}...")
                    continue  # Don't create duplicate
                
                # Not canonical or duplicate - create new memory
                stored_memory = await storage.create_memory(memory)
                
                # Auto-update user profile from memory
                try:
                    await profile_manager.update_profile_from_memory(
                        user_id=user_id,
                        memory_content=memory.content,
                        memory_type=memory.type.value,
                        entities=memory.entities,
                        context=memory.context
                    )
                except Exception as e:
                    logger.warning(f"Profile update failed for memory: {e}")
                
                # ✅ FIX #4: Disable expensive conflict resolution for demo (saves 5+ LLM calls per memory)
                # Each conflict check (_are_conflicting) makes 1 LLM call, checked 5 times per memory
                # This was causing 20+ Groq calls per turn extraction
                # Re-enable for production: Uncomment below
                
                # Check for conflicts with existing memories
                # try:
                #     resolution = await MemoryConflictResolver.detect_and_resolve(
                #         new_memory=stored_memory,
                #         user_memories=existing_memories,
                #         storage=storage
                #     )
                #     if resolution:
                #         logger.info(f"Conflict resolved using strategy: {resolution}")
                # except Exception as e:
                #     logger.warning(f"Conflict resolution failed: {e}")
                
                logger.debug(f"Conflict resolution disabled for performance (demo mode)")

            
            await session.commit()

            logger.info(f"Stored {len(memories)} memories and updated profile for turn {turn_number}")
    except Exception as e:
        logger.error(f"Background memory extraction failed: {e}", exc_info=True)


# Conversation endpoint with memory integration
@router.post("/conversation", response_model=ConversationResponse)
async def process_conversation(
    request: ConversationRequest,
    current_user: User = Depends(get_current_user),
    storage: MemoryStorage = Depends(get_memory_storage),
    retriever: MemoryRetriever = Depends(get_memory_retriever),
    extractor: MemoryExtractor = Depends(get_memory_extractor),
    conversation_storage: ConversationStorage = Depends(get_conversation_storage),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Process a conversation turn with memory integration.
    
    1. Retrieves relevant memories
    2. Builds context with memories
    3. Calls LLM for response
    4. Stores full conversation turn
    5. Extracts new memories (async)
    """
    start_time = time.time()

    try:
        from uuid import uuid4
        turn_id = uuid4()
        memories_used = []
        search_results = []  # Initialize to empty list

        # Handle conversation creation or selection
        conversation_id = request.conversation_id
        if conversation_id is None:
            # Create new conversation
            conversation = await conversation_manager.create_conversation(
                user_id=current_user.user_id,
                title="New Conversation",  # Will be updated with auto-title later
            )
            conversation_id = conversation.conversation_id
            logger.info(f"Created new conversation {conversation_id} for user {current_user.user_id}")
        else:
            # Verify conversation exists and belongs to user
            conversation = await conversation_manager.get_conversation(
                conversation_id=conversation_id,
                user_id=current_user.user_id,
            )
            if not conversation:
                raise HTTPException(status_code=404, detail="Conversation not found")

        # Step 1: Retrieve relevant memories
        if request.include_memories:
            query_text = request.message.lower()
            
            # Detect if this is a new conversation start (generic greeting)
            is_greeting = any(phrase in query_text for phrase in [
                "hi", "hello", "hey", "greetings", "good morning", "good afternoon",
                "good evening", "what's up", "howdy", "sup"
            ]) and len(query_text.split()) <= 5  # Short greeting
            
            # Detect query type for smart filtering
            is_schedule_query = any(phrase in query_text for phrase in [
                "schedule", "meeting", "appointment", "calendar", 
                "tomorrow", "today", "next week", "committed to",
                "plans for", "busy", "what's on", "agenda"
            ])
            
            is_broad_query = any(phrase in query_text for phrase in [
                "about me", "about myself", "do you know about me", 
                "what do you know", "tell me everything", "remember about me",
                "my details", "each and every", "all details", "everything about",
                "my fiancee", "my finacee", "details what", "know about",
                "comprehensive", "full details", "complete information"
            ])
            
            is_everything_query = any(phrase in query_text for phrase in [
                "each and every", "everything", "all details", "comprehensive",
                "full details", "complete information", "tell me everything"
            ])
            
            # Priority order: greetings (for turn 1) → schedule → broad → normal
            if (request.turn_number == 0 or request.turn_number == 1 or is_greeting) and not is_broad_query:
                # For first turn or generic greetings, load user profile automatically
                logger.info(f"🎯 New conversation/greeting detected (turn {request.turn_number}) - loading user profile")
                search_query = MemorySearchQuery(
                    user_id=current_user.user_id,
                    query=f"{current_user.user_id} user name facts preferences important information",
                    top_k=15,  # Load top 15 most important memories
                    current_turn=request.turn_number,
                )
                search_results = await retriever.search(search_query)
            
            elif is_schedule_query:
                # Schedule-specific search - only COMMITMENT and EPISODIC memories
                search_query = MemorySearchQuery(
                    user_id=current_user.user_id,
                    query=f"{request.message} schedule meeting appointment commitment plans",
                    top_k=20,
                    current_turn=request.turn_number,
                )
                search_results = await retriever.search(search_query)
                
                # Filter to only COMMITMENT and EPISODIC types for schedules
                search_results = [
                    result for result in search_results 
                    if result.memory.type in [MemoryType.COMMITMENT, MemoryType.EPISODIC]
                ]
                
            elif is_broad_query:
                # Expand search for comprehensive user info
                search_query = MemorySearchQuery(
                    user_id=current_user.user_id,
                    query=f"{current_user.user_id} user information facts details preferences commitments relationships",
                    top_k=50 if is_everything_query else 30,
                    current_turn=request.turn_number,
                )
                search_results = await retriever.search(search_query)
            else:
                # Normal contextual search
                search_query = MemorySearchQuery(
                    user_id=current_user.user_id,
                    query=request.message,
                    top_k=settings.memory_retrieval_top_k,
                    current_turn=request.turn_number,
                )
                search_results = await retriever.search(search_query)
            
            memories_used = [result.memory.memory_id for result in search_results]

            # Format memories for context with full details
            if search_results:
                # Check if this is a schedule query for cleaner formatting
                query_text = request.message.lower()
                is_schedule_display = any(phrase in query_text for phrase in [
                    "schedule", "meeting", "appointment", "calendar", "plans"
                ])
                
                if is_schedule_display:
                    # Clean format for schedule queries - no "Memory #X"
                    memory_lines = [
                        f"## YOUR SCHEDULED MEETINGS & COMMITMENTS\n"
                    ]
                    for result in search_results[:10]:
                        mem = result.memory
                        content = format_relative_time(mem.content, mem.metadata.created_at)
                        memory_lines.append(f"• {content}")
                else:
                    # Standard format for other queries
                    query_text = request.message.lower()
                    is_everything = any(phrase in query_text for phrase in [
                        "each and every", "everything", "all details"
                    ])
                    display_count = 30 if is_everything else 15
                    
                    memory_lines = [
                        f"## RELEVANT MEMORIES ({len(search_results[:display_count])} found)\n"
                    ]
                    for i, result in enumerate(search_results[:display_count], 1):
                        mem = result.memory
                        content = format_relative_time(mem.content, mem.metadata.created_at)
                        memory_lines.append(f"{i}. {content} (Type: {mem.type.value})")
                
                memory_context = "\n".join(memory_lines)
            else:
                memory_context = "No relevant memories found from previous turns."
        else:
            memory_context = ""

        # Step 2: Build prompt with memories
        query_text = request.message.lower()
        is_schedule_query = any(phrase in query_text for phrase in [
            "schedule", "meeting", "appointment", "calendar", "tomorrow", "today"
        ])
        
        # Check if user wants EVERYTHING
        is_comprehensive = any(phrase in query_text for phrase in [
            "each and every", "everything", "all details", "comprehensive",
            "full details", "complete information", "tell me everything"
        ])
        
        # 🔥 NEW: Detect knowledge/summary requests - should use general knowledge
        is_knowledge_query = any(phrase in query_text for phrase in [
            "summarize", "summarise", "summary", "tell me about", "what is",
            "explain", "describe", "book"
        ])
        
        # Check if this is a greeting and user is returning (has existing memories)
        is_greeting = any(phrase in query_text for phrase in [
            "hi", "hello", "hey", "greetings", "good morning", "good afternoon",
            "good evening", "what's up", "howdy", "sup"
        ]) and len(query_text.split()) <= 5
        
        # Extract user's name from memories for personalized greeting
        user_name = None
        if is_greeting and search_results:
            for result in search_results:
                content_lower = result.memory.content.lower()
                if "user's name is" in content_lower or "name is" in content_lower:
                    # Extract name after "name is"
                    parts = result.memory.content.split("name is")[-1].split(",")[0].strip()
                    if parts and len(parts.split()) <= 2:  # Name shouldn't be too long
                        user_name = parts.replace("'", "").replace('"', "")
                        logger.info(f"🎉 Returning user detected: {user_name}")
                        break
        
        # 🔥 PRODUCTION FEATURE: Memory Silence Detection
        # If max relevance score < 0.30, don't inject long-term memory (lowered for demo/testing)
        max_relevance = max([r.relevance_score for r in search_results], default=0.0)
        # ✅ FIX #3: Protect knowledge queries from silence mode
        silence_mode = (
            max_relevance < 0.30
            and not is_comprehensive
            and not is_knowledge_query
        )
        
        if silence_mode:
            logger.info(f"🤫 Memory silence mode activated (max_relevance={max_relevance:.3f})")
            memory_context = ""  # No long-term memory injection
            search_results = []  # Clear results
            # NOTE: Short-term context should still be preserved via conversation history
        
        # Generate system prompt using production template
        # 🔥 ARCHITECTURE: System = Rules + Memory, User = Message (proper role separation)
        system_prompt = get_system_prompt(
            turn_number=request.turn_number,
            user_id=current_user.user_id,
            memory_count=len(search_results),
            memory_context=memory_context,
            silence_mode=silence_mode,
            is_greeting=is_greeting,
            is_schedule_query=is_schedule_query,
            is_comprehensive=is_comprehensive,
            is_knowledge_query=is_knowledge_query,  # 🔥 NEW: Knowledge query flag
            user_name=user_name,
        )
        
        # Build messages array for LLM
        # PRODUCTION STRUCTURE:
        # 1. System message = Architecture rules + Memory context
        # 2. Conversation history = Short-term context (last 3-5 turns)
        # 3. User message = User's actual message (separate role)
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        
        # ✅ FIXED: Add conversation history for short-term context
        # Get recent conversation turns (exclude current turn)
        try:
            recent_turns = await conversation_storage.get_recent_turns(
                user_id=current_user.user_id,  # ✅ FIX: Pass user_id to eliminate "user None" logs
                conversation_id=conversation_id, 
                limit=5,
                before_turn=request.turn_number  # Exclude current turn being processed
            )
            
            # ✅ FIX #4: Ensure chronological order
            recent_turns = sorted(recent_turns, key=lambda x: x.turn_number)
            
            # Convert turns to message format for LLM context
            for turn in recent_turns:
                if turn.user_message:
                    messages.append({"role": "user", "content": turn.user_message})
                if turn.assistant_message:
                    messages.append({"role": "assistant", "content": turn.assistant_message})
                    
        except Exception as e:
            logger.warning(f"Failed to retrieve conversation history: {e}")
            # Continue without history if there's an error
        
        # Add current user message
        messages.append({"role": "user", "content": request.message})

        # Step 3: Call LLM using unified client
        llm_start = time.time()
        
        # Use unified LLM client (supports OpenAI, Claude, Groq)
        assistant_message = await asyncio.to_thread(
            get_llm_client().chat_completion,
            messages=messages,
            temperature=0.7,
            max_tokens=1000,
        )

        llm_duration = time.time() - llm_start

        # Record LLM metrics
        metrics.record_llm_call(
            model=settings.llm_provider,
            operation="conversation",
            duration=llm_duration,
            prompt_tokens=0,
            completion_tokens=0,
        )

        # Step 4: Store full conversation turn in database
        await conversation_storage.store_turn(
            conversation_id=conversation_id,
            user_id=current_user.user_id,
            turn_number=request.turn_number,
            user_message=request.message,
            assistant_message=assistant_message,
            memories_retrieved=memories_used,
            metadata=request.metadata,
        )

        # Increment conversation turn count and update timestamp
        await conversation_manager.increment_turn_count(
            conversation_id=conversation_id,
            user_id=current_user.user_id,
        )

        # Step 5: Extract new memories asynchronously
        # Note: We need to pass session-independent parameters and create new session
        asyncio.create_task(
            _extract_and_store_memories_independent(
                user_id=current_user.user_id,
                turn_number=request.turn_number,
                user_message=request.message,
                assistant_message=assistant_message,
            )
        )

        # Step 6: Generate title for new conversations (first turn only)
        if request.turn_number == 0:
            asyncio.create_task(
                _generate_conversation_title(
                    conversation_id=conversation_id,
                    user_id=current_user.user_id,
                    first_message=request.message,
                )
            )

        processing_time = (time.time() - start_time) * 1000

        # Build active_memories list for response (required by hackathon problem statement)
        from app.models.conversation import ActiveMemory
        active_memories = []
        memory_ids_to_update = []
        
        for result in search_results[:10]:  # Show top 10 memories that influenced response
            active_memories.append(
                ActiveMemory(
                    memory_id=str(result.memory.memory_id),
                    content=result.memory.content,
                    type=result.memory.type.value,
                    origin_turn=result.memory.metadata.source_turn if result.memory.metadata else 0,
                    last_used_turn=request.turn_number,
                    confidence=result.memory.metadata.confidence if result.memory.metadata else 0.5,
                    relevance_score=result.relevance_score,
                )
            )
            memory_ids_to_update.append(result.memory.memory_id)
        
        # Update last_used_turn in database for retrieved memories
        if memory_ids_to_update:
            try:
                async with db_manager.get_session() as update_session:
                    from sqlalchemy import text
                    # Build IN clause for PostgreSQL
                    memory_id_strs = [str(mid) for mid in memory_ids_to_update]
                    placeholders = ','.join([f":id{i}" for i in range(len(memory_id_strs))])
                    query = f"""
                        UPDATE memories 
                        SET last_used_turn = :turn_number 
                        WHERE memory_id::text IN ({placeholders})
                    """
                    params = {"turn_number": request.turn_number}
                    params.update({f"id{i}": mid for i, mid in enumerate(memory_id_strs)})
                    
                    await update_session.execute(text(query), params)
                    await update_session.commit()
                    logger.info(f"Updated last_used_turn for {len(memory_ids_to_update)} memories")
            except Exception as e:
                logger.warning(f"Failed to update last_used_turn: {e}")

        return ConversationResponse(
            turn_id=turn_id,
            conversation_id=conversation_id,
            user_id=current_user.user_id,
            turn_number=request.turn_number,
            response=assistant_message,
            active_memories=active_memories,  # NEW: Show which memories influenced response
            memories_used=memories_used,
            memories_extracted=0,  # Will be updated async
            processing_time_ms=processing_time,
            retrieval_time_ms=None,  # TODO: Add timing
            injection_time_ms=None,  # TODO: Add timing
            response_generated=True,
            metadata=request.metadata,
        )

    except Exception as e:
        logger.error(f"Conversation processing failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))


async def _extract_and_store_memories(
    user_id: str,
    turn_number: int,
    user_message: str,
    assistant_message: str,
    storage: MemoryStorage,
    extractor: MemoryExtractor,
):
    """Background task to extract and store memories."""
    try:
        memories = await extractor.extract_from_turn(
            user_id=user_id,
            turn_number=turn_number,
            user_message=user_message,
            assistant_message=assistant_message,
        )

        for memory in memories:
            await storage.create_memory(memory)

        logger.info(f"Stored {len(memories)} memories for turn {turn_number}")
    except Exception as e:
        logger.error(f"Background memory extraction failed: {e}")


# Memory CRUD endpoints
@router.post("/memories", response_model=Memory)
async def create_memory(
    memory: MemoryCreate,
    storage: MemoryStorage = Depends(get_memory_storage),
):
    """Create a new memory."""
    return await storage.create_memory(memory)


# Search and list endpoints (MUST come before {memory_id} routes!)
@router.post("/memories/search", response_model=list[MemorySearchResult])
async def search_memories(
    query: str = Query(..., description="Search query"),
    top_k: int = Query(10, ge=1, le=100, description="Number of results"),
    current_user: User = Depends(get_current_user),
    retriever: MemoryRetriever = Depends(get_memory_retriever),
):
    """Search memories for authenticated user."""
    search_query = MemorySearchQuery(
        user_id=current_user.user_id,
        query=query,
        top_k=top_k,
    )
    return await retriever.search(search_query)


@router.get("/memories/list", response_model=list[Memory])
async def list_memories(
    current_user: User = Depends(get_current_user),
    memory_type: Optional[MemoryType] = None,
    limit: int = Query(50, ge=1, le=200),
    storage: MemoryStorage = Depends(get_memory_storage),
):
    """List memories for authenticated user."""
    return await storage.list_memories(current_user.user_id, memory_type=memory_type, limit=limit)


@router.get("/memories/stats", response_model=MemoryStats)
async def get_memory_stats(
    current_user: User = Depends(get_current_user),
    storage: MemoryStorage = Depends(get_memory_storage),
):
    """Get memory statistics for authenticated user."""
    try:
        stats = await storage.get_user_stats(current_user.user_id)
        logger.info(f"✅ Stats retrieved successfully: {stats.dict()}")
        return stats
    except Exception as e:
        logger.error(f"❌ Stats error for {current_user.user_id}: {type(e).__name__}: {e}", exc_info=True)
        # Return empty stats instead of raising error
        return MemoryStats(
            user_id=current_user.user_id,
            total_memories=0,
            memories_by_type={},
            avg_confidence=0.0,
            oldest_memory_turn=0,
            newest_memory_turn=0,
            total_access_count=0,
            hot_memories=0
        )


# Management endpoints
@router.post("/memories/consolidate")
async def consolidate_memories(
    current_user: User = Depends(get_current_user),
    manager: MemoryManager = Depends(get_memory_manager),
):
    """Consolidate similar memories for authenticated user."""
    count = await manager.consolidate_similar_memories(current_user.user_id)
    return {"consolidated_count": count}


@router.post("/memories/cleanup")
async def cleanup_old_memories(
    days: int = Query(90, ge=1, le=100, description="Delete memories older than N days"),
    current_user: User = Depends(get_current_user),
    manager: MemoryManager = Depends(get_memory_manager),
):
    """Clean up old memories for authenticated user."""
    count = await manager.cleanup_old_memories(current_user.user_id, days=days)
    return {"deleted_count": count}


@router.post("/memories/decay")
async def apply_memory_decay(
    manager: MemoryManager = Depends(get_memory_manager),
):
    """Apply time-based decay to all memories."""
    count = await manager.apply_decay()
    return {"updated_count": count}


# Generic memory CRUD (MUST come after specific routes!)
@router.get("/memories/{memory_id}", response_model=Memory)
async def get_memory(
    memory_id: UUID,
    storage: MemoryStorage = Depends(get_memory_storage),
):
    """Get a memory by ID."""
    memory = await storage.get_memory(memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.put("/memories/{memory_id}", response_model=Memory)
async def update_memory(
    memory_id: UUID,
    update: MemoryUpdate,
    storage: MemoryStorage = Depends(get_memory_storage),
):
    """Update a memory."""
    memory = await storage.update_memory(memory_id, update)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: UUID,
    storage: MemoryStorage = Depends(get_memory_storage),
):
    """Delete a memory."""
    success = await storage.delete_memory(memory_id)
    if not success:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"status": "deleted"}


# Conversation history endpoints
@router.get("/conversations/history")
async def get_conversation_history(
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=10, ge=1, le=100),
    before_turn: Optional[int] = Query(default=None),
    conversation_storage: ConversationStorage = Depends(get_conversation_storage),
):
    """Get conversation history for authenticated user.
    
    Args:
        limit: Maximum number of turns to retrieve  
        before_turn: Only get turns before this number
        
    Returns:
        List of conversation turns
    """
    try:
        turns = await conversation_storage.get_recent_turns(
            user_id=current_user.user_id,  # ✅ Already correct - user_id passed
            limit=limit,
            before_turn=before_turn,
        )
        return turns
    except Exception as e:
        logger.error(f"Failed to retrieve conversation history: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# User Profile Endpoints
@router.get("/profile")
async def get_user_profile(current_user: User = Depends(get_current_user)):
    """Get user profile with auto-generated summary.
    
    Returns comprehensive user profile built from conversation memories.
    """
    try:
        from app.services.profile_manager import profile_manager
        profile = await profile_manager.get_or_create_profile(current_user.user_id)
        return profile
    except Exception as e:
        logger.error(f"Failed to retrieve profile: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/profile/summary")
async def get_profile_summary(current_user: User = Depends(get_current_user)):
    """Get compact profile summary for LLM context.
    
    Returns condensed profile suitable for adding to LLM prompts.
    """
    try:
        from app.services.profile_manager import profile_manager
        summary = await profile_manager.get_profile_summary(current_user.user_id)
        return summary
    except Exception as e:
        logger.error(f"Failed to retrieve profile summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# Conversation Management Endpoints
@router.post("/conversations")
async def create_conversation(
    current_user: User = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Create a new conversation."""
    try:
        conversation = await conversation_manager.create_conversation(
            user_id=current_user.user_id
        )
        return conversation
    except Exception as e:
        logger.error(f"Failed to create conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations")
async def list_conversations(
    include_archived: bool = Query(default=False),
    limit: int = Query(default=50, le=100),
    offset: int = Query(default=0, ge=0),
    current_user: User = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """List all conversations for the current user."""
    try:
        conversations = await conversation_manager.list_conversations(
            user_id=current_user.user_id,
            include_archived=include_archived,
            limit=limit,
            offset=offset,
        )
        
        # Get counts
        total_count = await conversation_manager.get_conversation_count(
            user_id=current_user.user_id
        )
        archived_count = await conversation_manager.get_conversation_count(
            user_id=current_user.user_id,
            archived_only=True,
        )
        
        from app.models.conversation import ConversationListResponse
        return ConversationListResponse(
            conversations=conversations,
            total_count=total_count,
            archived_count=archived_count,
        )
    except Exception as e:
        logger.error(f"Failed to list conversations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Get a specific conversation by ID."""
    try:
        conversation = await conversation_manager.get_conversation(
            conversation_id=conversation_id,
            user_id=current_user.user_id,
        )
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return conversation
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.patch("/conversations/{conversation_id}")
async def update_conversation(
    conversation_id: UUID,
    title: Optional[str] = None,
    is_archived: Optional[bool] = None,
    current_user: User = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Update conversation metadata (title, archive status)."""
    try:
        conversation = await conversation_manager.update_conversation(
            conversation_id=conversation_id,
            user_id=current_user.user_id,
            title=title,
            is_archived=is_archived,
        )
        
        if not conversation:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return conversation
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Delete a conversation and all its turns."""
    try:
        deleted = await conversation_manager.delete_conversation(
            conversation_id=conversation_id,
            user_id=current_user.user_id,
        )
        
        if not deleted:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return {"success": True, "message": "Conversation deleted"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to delete conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations/search")
async def search_conversations(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=20, le=50),
    current_user: User = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Search conversations by title or content."""
    try:
        conversations = await conversation_manager.search_conversations(
            user_id=current_user.user_id,
            query=q,
            limit=limit,
        )
        return {"conversations": conversations}
    except Exception as e:
        logger.error(f"Failed to search conversations: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/conversations/{conversation_id}/export")
async def export_conversation(
    conversation_id: UUID,
    current_user: User = Depends(get_current_user),
    conversation_manager: ConversationManager = Depends(get_conversation_manager),
):
    """Export a conversation with all turns as JSON."""
    try:
        export = await conversation_manager.export_conversation(
            conversation_id=conversation_id,
            user_id=current_user.user_id,
        )
        
        if not export:
            raise HTTPException(status_code=404, detail="Conversation not found")
        
        return export
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to export conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# VISION MODEL ROUTES - Image Analysis with Groq Llama 3.2 Vision
# ============================================================================

from fastapi import UploadFile, File, Form
from app.services.vision_service import vision_service


@router.post("/vision/analyze", tags=["Vision"])
async def analyze_image(
    file: UploadFile = File(...),
    prompt: str = Form("Describe this image in detail and extract any important information."),
    save_to_memory: bool = Form(True),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db_session),
):
    """
    Analyze an image or document using AI.
    
    - **file**: Image (PNG, JPEG, WEBP, GIF) or Document (PDF, DOCX, PPTX)
    - **prompt**: Analysis prompt (default: detailed description)
    - **save_to_memory**: Whether to save analysis as a memory (default: true)
    
    Returns analysis text and optionally stores as memory.
    """
    try:
        logger.info(f"📷 Vision analysis request from {current_user.user_id}: {file.filename}")
        
        # Read file bytes
        file_bytes = await file.read()
        
        # Determine if it's an image or document (check both extension AND MIME type)
        file_ext = file.filename.lower().rsplit('.', 1)[-1] if '.' in file.filename else ''
        mime_type = file.content_type or ''
        
        # Document detection (either by extension or MIME type)
        is_document = (
            file_ext in ['pdf', 'docx', 'doc', 'pptx', 'ppt'] or
            'pdf' in mime_type or
            'document' in mime_type or
            'presentation' in mime_type
        )
        
        logger.info(f"🔍 File type detection: ext={file_ext}, mime={mime_type}, is_document={is_document}")
        
        if is_document:
            # Process document
            logger.info("🔄 Processing document...")
            result = await vision_service.analyze_document(
                file_bytes=file_bytes,
                filename=file.filename,
                prompt=prompt,
                user_id=current_user.user_id
            )
        else:
            # Process image
            # Validate image
            is_valid, error_msg = vision_service.validate_image(file_bytes, file.filename)
            if not is_valid:
                raise HTTPException(status_code=400, detail=error_msg)
            
            # Process image (resize, optimize, encode)
            logger.info("🔄 Processing image...")
            image_base64 = vision_service.process_image(file_bytes)
            
            # Analyze with vision model
            logger.info("🤖 Analyzing with Groq Vision...")
            result = await vision_service.analyze_image(
                image_base64=image_base64,
                prompt=prompt,
                user_id=current_user.user_id
            )
        
        if not result["success"]:
            raise HTTPException(
                status_code=500,
                detail=f"Vision analysis failed: {result.get('error', 'Unknown error')}"
            )
        
        analysis_text = result["analysis"]
        
        # Optionally save to memory
        memory_id = None
        if save_to_memory and analysis_text:
            logger.info("💾 Saving analysis to memory...")
            
            # Get embedding generator
            embedding_gen = await get_embedding_generator()
            storage = MemoryStorage(
                session=session,
                redis_client=db_manager.redis,
                embedding_generator=embedding_gen
            )
            
            # Prepare content (max 5000 chars for MemoryCreate)
            prefix = f"Image Analysis ({file.filename}): "
            max_content_len = 5000 - len(prefix) - 20  # Reserve space for prefix and truncation marker
            
            if len(analysis_text) > max_content_len:
                # Truncate but keep first part (usually contains key info)
                truncated_analysis = analysis_text[:max_content_len] + "... [truncated]"
                memory_content = prefix + truncated_analysis
                logger.warning(f"Image analysis truncated from {len(analysis_text)} to {max_content_len} chars")
            else:
                memory_content = prefix + analysis_text
            
            # Create memory from analysis
            memory = MemoryCreate(
                user_id=current_user.user_id,
                content=memory_content,
                type=MemoryType.FACT,
                source_turn=0,  # Standalone memory
                tags=["vision", "image-analysis", file.filename],
                confidence=0.95,
                entities=[]
            )
            
            created_memory = await storage.create_memory(memory)
            memory_id = str(created_memory.memory_id)
            logger.info(f"✅ Saved analysis as memory: {memory_id}")
        
        return {
            "success": True,
            "analysis": analysis_text,
            "filename": file.filename,
            "model": result["model"],
            "prompt": prompt,
            "memory_id": memory_id,
            "saved_to_memory": save_to_memory and memory_id is not None
        }
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Vision analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")
