"""API routes for memory system."""

import asyncio
import logging
import time
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import db_manager, get_db_session
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
from app.services.extractor import MemoryExtractor
from app.services.memory_manager import MemoryManager
from app.services.retriever import MemoryRetriever
from app.services.storage import MemoryStorage
from app.utils.embeddings import EmbeddingGenerator
from app.utils.metrics import metrics

logger = logging.getLogger(__name__)
settings = get_settings()

router = APIRouter(prefix="/api/v1", tags=["memory"])


# Dependency injection
async def get_embedding_generator():
    """Get embedding generator instance."""
    return EmbeddingGenerator(redis_client=db_manager.redis)


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

            # Store memories
            for memory in memories:
                await storage.create_memory(memory)
            
            await session.commit()

            logger.info(f"Stored {len(memories)} memories for turn {turn_number}")
    except Exception as e:
        logger.error(f"Background memory extraction failed: {e}", exc_info=True)


# Conversation endpoint with memory integration
@router.post("/conversation", response_model=ConversationResponse)
async def process_conversation(
    request: ConversationRequest,
    storage: MemoryStorage = Depends(get_memory_storage),
    retriever: MemoryRetriever = Depends(get_memory_retriever),
    extractor: MemoryExtractor = Depends(get_memory_extractor),
):
    """Process a conversation turn with memory integration.
    
    1. Retrieves relevant memories
    2. Builds context with memories
    3. Calls LLM for response
    4. Extracts new memories (async)
    """
    start_time = time.time()

    try:
        from uuid import uuid4
        turn_id = uuid4()
        memories_used = []

        # Step 1: Retrieve relevant memories
        if request.include_memories:
            search_query = MemorySearchQuery(
                user_id=request.user_id,
                query=request.message,
                top_k=settings.memory_retrieval_top_k,
                current_turn=request.turn_number,
            )
            search_results = await retriever.search(search_query)
            memories_used = [result.memory.memory_id for result in search_results]

            # Format memories for context
            memory_context = "\n".join([
                f"- {result.memory.content}"
                for result in search_results[:5]  # Top 5 for context
            ])
        else:
            memory_context = ""

        # Step 2: Build prompt with memories
        system_prompt = """You are a helpful AI assistant with memory of past conversations.
Use relevant memories to provide personalized, context-aware responses.
Respond naturally without explicitly mentioning that you're using memories."""

        messages = [
            {"role": "system", "content": system_prompt}
        ]

        if memory_context:
            messages.append({
                "role": "system",
                "content": f"RELEVANT MEMORIES:\n{memory_context}"
            })

        messages.append({
            "role": "user",
            "content": request.message
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

        # Step 4: Extract new memories asynchronously
        # Note: We need to pass session-independent parameters and create new session
        asyncio.create_task(
            _extract_and_store_memories_independent(
                user_id=request.user_id,
                turn_number=request.turn_number,
                user_message=request.message,
                assistant_message=assistant_message,
            )
        )

        processing_time = (time.time() - start_time) * 1000

        return ConversationResponse(
            turn_id=turn_id,
            user_id=request.user_id,
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
@router.post("/memories/{user_id}/search", response_model=list[MemorySearchResult])
async def search_memories(
    user_id: str,
    query: str = Query(..., description="Search query"),
    top_k: int = Query(10, ge=1, le=100, description="Number of results"),
    retriever: MemoryRetriever = Depends(get_memory_retriever),
):
    """Search memories for a user."""
    search_query = MemorySearchQuery(
        user_id=user_id,
        query=query,
        top_k=top_k,
    )
    return await retriever.search(search_query)


@router.get("/memories/{user_id}/list", response_model=list[Memory])
async def list_memories(
    user_id: str,
    memory_type: Optional[MemoryType] = None,
    limit: int = Query(50, ge=1, le=200),
    storage: MemoryStorage = Depends(get_memory_storage),
):
    """List memories for a user."""
    return await storage.list_memories(user_id, memory_type=memory_type, limit=limit)


@router.get("/memories/{user_id}/stats", response_model=MemoryStats)
async def get_memory_stats(
    user_id: str,
    storage: MemoryStorage = Depends(get_memory_storage),
):
    """Get memory statistics for a user."""
    return await storage.get_user_stats(user_id)


# Management endpoints
@router.post("/memories/{user_id}/consolidate")
async def consolidate_memories(
    user_id: str,
    manager: MemoryManager = Depends(get_memory_manager),
):
    """Consolidate similar memories for a user."""
    count = await manager.consolidate_similar_memories(user_id)
    return {"consolidated_count": count}


@router.post("/memories/{user_id}/cleanup")
async def cleanup_old_memories(
    user_id: str,
    days: int = Query(90, ge=1, description="Delete memories older than N days"),
    manager: MemoryManager = Depends(get_memory_manager),
):
    """Clean up old memories for a user."""
    count = await manager.cleanup_old_memories(user_id, days=days)
    return {"deleted_count": count}


@router.post("/memories/decay")
async def apply_memory_decay(
    manager: MemoryManager = Depends(get_memory_manager),
):
    """Apply time-based decay to all memories."""
    count = await manager.apply_decay()
    return {"updated_count": count}


# Health check
@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "provider": settings.llm_provider,
        "database": "connected" if db_manager._engine else "disconnected",
        "redis": "connected" if db_manager._redis_client else "disconnected",
        "pinecone": "connected" if db_manager._pinecone_index else "disconnected",
    }
