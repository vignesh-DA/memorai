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
from app.llm_client import llm_client
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
            
            for memory in memories:
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
                
                # Check for conflicts with existing memories
                try:
                    resolution = await MemoryConflictResolver.detect_and_resolve(
                        new_memory=stored_memory,
                        user_memories=existing_memories,
                        storage=storage
                    )
                    if resolution:
                        logger.info(f"Conflict resolved using strategy: {resolution}")
                except Exception as e:
                    logger.warning(f"Conflict resolution failed: {e}")

            
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
        
        if is_schedule_query and search_results:
            # Schedule-specific prompt - only show schedule info
            system_prompt = f"""You are an advanced AI assistant with LONG-TERM MEMORY capabilities.

## CURRENT CONTEXT
- Turn Number: {request.turn_number}
- User ID: {current_user.user_id}
- Query Type: SCHEDULE/MEETING REQUEST
- Memories Retrieved: {len(search_results)} schedule-related memories

## INSTRUCTIONS FOR SCHEDULE QUERIES
1. The user is asking about their SCHEDULE/MEETINGS only
2. Show ONLY schedule-related information (meetings, appointments, commitments)
3. Include the DATE and TIME for each item
4. Do NOT mention unrelated info like relationships, skills, or other facts
5. Format: "You have [event] on [date] at [time]"
6. If no schedule found, say "I don't have any scheduled meetings or appointments"""
        else:
            # Check if user wants EVERYTHING
            query_text = request.message.lower()
            is_everything = any(phrase in query_text for phrase in [
                "each and every", "everything", "all details", "comprehensive",
                "full details", "complete information", "tell me everything"
            ])
            
            # General prompt
            if is_everything:
                system_prompt = f"""You are an advanced AI assistant with LONG-TERM MEMORY capabilities.

## CURRENT CONTEXT
- Turn Number: {request.turn_number}
- User ID: {current_user.user_id}
- Memories Retrieved: {len(search_results) if request.include_memories else 0}

## 🚨 COMPREHENSIVE INFORMATION REQUEST 🚨
The user asked for "EACH AND EVERY THING" or "EVERYTHING" - this means:

1. **LIST EVERY SINGLE MEMORY PROVIDED BELOW**
2. **DO NOT SUMMARIZE** - show full details from each memory
3. **Organize by categories:**
   - 👤 Personal Information (name, age, location)
   - 💼 Professional Details (job, experience, skills)
   - 👥 Relationships (fiancé, family, friends)
   - 🍽️ Preferences (food, hobbies, interests)
   - 📅 Commitments (meetings, appointments, schedules)
   - 💭 Other Facts

4. **For each category, list ALL relevant details** - don't skip anything
5. **Include dates/times for schedules**
6. **Be thorough and complete** - the user wants EVERYTHING

⚠️ FAILURE TO LIST ALL MEMORIES WILL DISAPPOINT THE USER ⚠️"""
            else:
                # Add personalized greeting instruction if returning user
                greeting_instruction = ""
                if is_greeting and user_name:
                    greeting_instruction = f"""\n\n## 🎉 RETURNING USER GREETING
This is a RETURNING USER starting a new conversation!
- User's name: {user_name}
- They have {len(search_results)} existing memories

**GREETING STYLE (like Leo Das example):**
- Use format: "{user_name} returns!" or "Welcome back, {user_name}!"
- Be warm and enthusiastic with emoji ✨
- Keep it SHORT (1-2 sentences max)
- Briefly mention 1 interesting fact you remember
- Ask "How can I help you today?" to prompt conversation

❌ DO NOT MENTION: user IDs, email addresses, technical details
✅ DO MENTION: Their name, something personal, warm welcome\n"""
                elif is_greeting and search_results:
                    greeting_instruction = f"""\n\n## 👋 RETURNING USER (Name Unknown)
This is a returning user with {len(search_results)} existing memories.
- Greet them warmly: "Welcome back!"
- Mention you remember them
- Keep it brief and friendly
- Ask how you can help\n"""
                
                system_prompt = f"""You are an advanced AI assistant with LONG-TERM MEMORY capabilities.

## CURRENT CONTEXT
- Turn Number: {request.turn_number}
- User ID: {current_user.user_id}
- Memories Retrieved: {len(search_results) if request.include_memories else 0}{greeting_instruction}

## CRITICAL INSTRUCTIONS
1. Focus on what the user is ACTUALLY asking about
2. Use memories that are RELEVANT to their specific question
3. For broad questions ("what do you know about me"), include all details
4. For specific questions, focus only on that topic
5. Be concise unless asked for comprehensive information"""

        messages = [
            {"role": "system", "content": system_prompt}
        ]

        if memory_context:
            messages.append({
                "role": "system",
                "content": f"\n{memory_context}\n"
            })

        messages.append({
            "role": "user",
            "content": f"USER'S MESSAGE (Turn {request.turn_number}):\n{request.message}"
        })

        # Step 3: Call LLM using unified client
        llm_start = time.time()
        
        # Use unified LLM client (supports OpenAI, Claude, Groq)
        assistant_message = await asyncio.to_thread(
            llm_client.chat_completion,
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
            user_id=current_user.user_id,
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


