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
            if (request.turn_number == 1 or is_greeting) and not is_broad_query:
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
                system_prompt = f"""You are an advanced AI assistant with LONG-TERM MEMORY capabilities.

## CURRENT CONTEXT
- Turn Number: {request.turn_number}
- User ID: {current_user.user_id}
- Memories Retrieved: {len(search_results) if request.include_memories else 0}

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
            user_id=current_user.user_id,
            turn_number=request.turn_number,
            user_message=request.message,
            assistant_message=assistant_message,
            memories_retrieved=memories_used,
            metadata=request.metadata,
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

        processing_time = (time.time() - start_time) * 1000

        return ConversationResponse(
            turn_id=turn_id,
            user_id=current_user.user_id,
            turn_number=request.turn_number,
            response=assistant_message,
            memories_used=memories_used,
            memories_extracted=0,  # Will be updated async
            processing_time_ms=processing_time,
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


# Search endpoints
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
    return await storage.get_user_stats(current_user.user_id)


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
    days: int = Query(90, ge=1, description="Delete memories older than N days"),
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


