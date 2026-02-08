"""Celery worker configuration for async tasks."""

import logging
from celery import Celery

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# Create Celery app
celery_app = Celery(
    "long_form_memory",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

# Celery configuration
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,  # 5 minutes
    task_soft_time_limit=240,  # 4 minutes
    worker_prefetch_multiplier=4,
    worker_max_tasks_per_child=1000,
)


@celery_app.task(name="extract_memories")
def extract_memories_task(
    user_id: str,
    turn_number: int,
    user_message: str,
    assistant_message: str,
):
    """Background task to extract and store memories.
    
    Args:
        user_id: User ID
        turn_number: Conversation turn number
        user_message: User's message
        assistant_message: Assistant's response
    """
    import asyncio
    from app.database import db_manager
    from app.services.extractor import MemoryExtractor
    from app.services.storage import MemoryStorage
    from app.utils.embeddings import EmbeddingGenerator

    async def _extract():
        await db_manager.initialize()
        
        try:
            extractor = MemoryExtractor()
            embedder = EmbeddingGenerator(db_manager.redis)
            
            async with db_manager.get_session() as session:
                storage = MemoryStorage(session, db_manager.redis, embedder)
                
                memories = await extractor.extract_from_turn(
                    user_id=user_id,
                    turn_number=turn_number,
                    user_message=user_message,
                    assistant_message=assistant_message,
                )
                
                for memory_create in memories:
                    await storage.create_memory(memory_create)
                
                logger.info(f"Extracted {len(memories)} memories for turn {turn_number}")
                return len(memories)
        finally:
            await db_manager.close()

    return asyncio.run(_extract())


@celery_app.task(name="consolidate_user_memories")
def consolidate_user_memories_task(user_id: str):
    """Background task to consolidate user's memories.
    
    Args:
        user_id: User ID
    """
    import asyncio
    from app.database import db_manager
    from app.services.extractor import MemoryExtractor
    from app.services.memory_manager import MemoryManager
    from app.services.retriever import MemoryRetriever
    from app.services.storage import MemoryStorage
    from app.utils.embeddings import EmbeddingGenerator

    async def _consolidate():
        await db_manager.initialize()
        
        try:
            embedder = EmbeddingGenerator(db_manager.redis)
            extractor = MemoryExtractor()
            retriever = MemoryRetriever(db_manager.redis, embedder)
            
            async with db_manager.get_session() as session:
                storage = MemoryStorage(session, db_manager.redis, embedder)
                manager = MemoryManager(storage, retriever, extractor)
                
                consolidations = await manager.consolidate_similar_memories(user_id)
                logger.info(f"Consolidated {len(consolidations)} memory clusters")
                return len(consolidations)
        finally:
            await db_manager.close()

    return asyncio.run(_consolidate())


@celery_app.task(name="optimize_user_memories")
def optimize_user_memories_task(user_id: str, current_turn: int):
    """Background task to optimize user's memory store.
    
    Args:
        user_id: User ID
        current_turn: Current conversation turn
    """
    import asyncio
    from app.database import db_manager
    from app.services.extractor import MemoryExtractor
    from app.services.memory_manager import MemoryManager
    from app.services.retriever import MemoryRetriever
    from app.services.storage import MemoryStorage
    from app.utils.embeddings import EmbeddingGenerator

    async def _optimize():
        await db_manager.initialize()
        
        try:
            embedder = EmbeddingGenerator(db_manager.redis)
            extractor = MemoryExtractor()
            retriever = MemoryRetriever(db_manager.redis, embedder)
            
            async with db_manager.get_session() as session:
                storage = MemoryStorage(session, db_manager.redis, embedder)
                manager = MemoryManager(storage, retriever, extractor)
                
                results = await manager.optimize_memory_store(user_id, current_turn)
                logger.info(f"Optimized memory store: {results}")
                return results
        finally:
            await db_manager.close()

    return asyncio.run(_optimize())


@celery_app.task(name="cleanup_old_memories")
def cleanup_old_memories_task():
    """Periodic task to cleanup old memories across all users."""
    import asyncio
    from sqlalchemy import text
    from app.database import db_manager

    async def _cleanup():
        await db_manager.initialize()
        
        try:
            # Get list of active users
            async with db_manager.get_session() as session:
                result = await session.execute(
                    text("SELECT DISTINCT user_id FROM memories")
                )
                user_ids = [row[0] for row in result.fetchall()]
            
            # Cleanup each user (in production, use task queue)
            total_cleaned = 0
            for user_id in user_ids:
                # Call optimize task for each user
                optimize_user_memories_task.delay(user_id, 0)
            
            logger.info(f"Scheduled cleanup for {len(user_ids)} users")
            return len(user_ids)
        finally:
            await db_manager.close()

    return asyncio.run(_cleanup())


# Periodic task schedule
celery_app.conf.beat_schedule = {
    "cleanup-old-memories-daily": {
        "task": "cleanup_old_memories",
        "schedule": 86400.0,  # Every 24 hours
    },
}


if __name__ == "__main__":
    celery_app.start()
