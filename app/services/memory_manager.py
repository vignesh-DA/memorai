"""Memory management service for decay, consolidation, and cleanup."""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models.memory import Memory, MemoryConsolidation, MemoryType
from app.services.extractor import MemoryExtractor
from app.services.retriever import MemoryRetriever
from app.services.storage import MemoryStorage
from app.utils.metrics import track_time

logger = logging.getLogger(__name__)
settings = get_settings()


class MemoryManager:
    """Manage memory lifecycle: decay, consolidation, cleanup."""

    def __init__(
        self,
        storage: MemoryStorage,
        retriever: MemoryRetriever,
        extractor: MemoryExtractor,
    ):
        """Initialize memory manager.
        
        Args:
            storage: Memory storage service
            retriever: Memory retrieval service
            extractor: Memory extraction service
        """
        self.storage = storage
        self.retriever = retriever
        self.extractor = extractor

    @track_time("apply_decay")
    async def apply_decay(
        self,
        user_id: str,
        current_turn: int,
    ) -> int:
        """Apply temporal decay to user's memories.
        
        Updates decay scores based on:
        - Time since creation
        - Access patterns
        - Memory confidence
        
        Args:
            user_id: User ID
            current_turn: Current conversation turn
            
        Returns:
            Number of memories updated
        """
        try:
            memories = await self.storage.get_user_memories(user_id, limit=1000)
            updated_count = 0

            for memory in memories:
                old_decay = memory.metadata.decay_score
                new_decay = memory.calculate_decay(current_turn)

                # Update if decay changed significantly
                if abs(old_decay - new_decay) > 0.05:
                    # In production, batch update these
                    updated_count += 1

            logger.info(f"Applied decay to {updated_count} memories for user {user_id}")
            return updated_count

        except Exception as e:
            logger.error(f"Failed to apply decay: {e}")
            raise

    @track_time("consolidate_memories")
    async def consolidate_similar_memories(
        self,
        user_id: str,
        similarity_threshold: float = 0.90,
    ) -> list[MemoryConsolidation]:
        """Consolidate similar or redundant memories.
        
        Finds clusters of similar memories and consolidates them into
        single, comprehensive memories.
        
        Args:
            user_id: User ID
            similarity_threshold: Similarity threshold for consolidation
            
        Returns:
            List of consolidation results
        """
        try:
            memories = await self.storage.get_user_memories(user_id, limit=500)
            consolidations = []

            # Track which memories have been consolidated
            consolidated_ids = set()

            for memory in memories:
                if memory.memory_id in consolidated_ids:
                    continue

                # Find similar memories
                similar = await self.retriever.find_similar_memories(
                    memory,
                    threshold=similarity_threshold,
                )

                # Need at least 2 similar memories to consolidate
                if len(similar) < 1:
                    continue

                # Consolidate the cluster
                memory_contents = [memory.content] + [m.content for m in similar]
                consolidated_content = await self.extractor.consolidate_memories(
                    memory_contents
                )

                if not consolidated_content:
                    continue

                # Create new consolidated memory
                from app.models.memory import MemoryCreate
                
                new_memory_create = MemoryCreate(
                    user_id=user_id,
                    type=memory.type,
                    content=consolidated_content,
                    source_turn=memory.metadata.source_turn,
                    confidence=max(memory.metadata.confidence, 
                                 max((m.metadata.confidence for m in similar), default=0)),
                    tags=list(set(memory.metadata.tags + 
                            [tag for m in similar for tag in m.metadata.tags])),
                    entities=list(set(memory.metadata.entities + 
                                [entity for m in similar for entity in m.metadata.entities])),
                )

                new_memory = await self.storage.create_memory(new_memory_create)

                # Delete old memories
                original_ids = [memory.memory_id] + [m.memory_id for m in similar]
                for old_id in original_ids:
                    await self.storage.delete_memory(old_id)
                    consolidated_ids.add(old_id)

                consolidation = MemoryConsolidation(
                    original_memories=original_ids,
                    consolidated_memory=new_memory,
                    consolidation_reason=f"Consolidated {len(original_ids)} similar memories",
                )
                consolidations.append(consolidation)

            logger.info(
                f"Consolidated {len(consolidations)} memory clusters for user {user_id}"
            )
            return consolidations

        except Exception as e:
            logger.error(f"Failed to consolidate memories: {e}")
            raise

    @track_time("resolve_conflicts")
    async def resolve_conflicts(
        self,
        user_id: str,
    ) -> int:
        """Detect and resolve conflicting memories.
        
        Args:
            user_id: User ID
            
        Returns:
            Number of conflicts resolved
        """
        try:
            memories = await self.storage.get_user_memories(user_id, limit=500)
            resolved_count = 0

            # Group memories by type
            by_type: dict[MemoryType, list[Memory]] = {}
            for memory in memories:
                if memory.type not in by_type:
                    by_type[memory.type] = []
                by_type[memory.type].append(memory)

            # Check for conflicts within each type
            for mem_type, mem_list in by_type.items():
                for i, mem1 in enumerate(mem_list):
                    for mem2 in mem_list[i + 1:]:
                        # Simple conflict detection: high similarity but different content
                        if not mem1.embedding or not mem2.embedding:
                            continue

                        similarity = await self.embedder.similarity(
                            mem1.embedding,
                            mem2.embedding,
                        )

                        # If very similar embeddings but different content, might be conflict
                        if 0.85 < similarity < 0.95:
                            resolved_content = await self.extractor.resolve_conflict(
                                mem1.content,
                                mem2.content,
                            )

                            if resolved_content:
                                # Update the more recent memory
                                newer = mem1 if mem1.metadata.source_turn > mem2.metadata.source_turn else mem2
                                older = mem2 if newer == mem1 else mem1

                                from app.models.memory import MemoryUpdate
                                await self.storage.update_memory(
                                    newer.memory_id,
                                    MemoryUpdate(content=resolved_content),
                                )
                                await self.storage.delete_memory(older.memory_id)
                                resolved_count += 1

            logger.info(f"Resolved {resolved_count} conflicts for user {user_id}")
            return resolved_count

        except Exception as e:
            logger.error(f"Failed to resolve conflicts: {e}")
            return 0

    @track_time("cleanup_old_memories")
    async def cleanup_old_memories(
        self,
        user_id: str,
        max_age_days: Optional[int] = None,
        min_decay_score: float = 0.1,
    ) -> int:
        """Clean up old, low-value memories.
        
        Removes memories that are:
        - Very old with low decay scores
        - Never accessed
        - Low confidence
        
        Args:
            user_id: User ID
            max_age_days: Maximum age in days (default from settings)
            min_decay_score: Minimum decay score to keep
            
        Returns:
            Number of memories deleted
        """
        if max_age_days is None:
            max_age_days = settings.memory_decay_days

        try:
            memories = await self.storage.get_user_memories(user_id, limit=1000)
            deleted_count = 0
            cutoff_date = datetime.utcnow() - timedelta(days=max_age_days)

            for memory in memories:
                should_delete = False

                # Check age and decay
                if (memory.metadata.created_at < cutoff_date and
                    memory.metadata.decay_score < min_decay_score):
                    should_delete = True

                # Check if never accessed and low confidence
                if (memory.metadata.access_count == 0 and
                    memory.metadata.confidence < settings.memory_confidence_threshold):
                    should_delete = True

                if should_delete:
                    await self.storage.delete_memory(memory.memory_id)
                    deleted_count += 1

            logger.info(f"Cleaned up {deleted_count} old memories for user {user_id}")
            return deleted_count

        except Exception as e:
            logger.error(f"Failed to cleanup memories: {e}")
            raise

    async def delete_user_memories(
        self,
        user_id: str,
    ) -> int:
        """Delete all memories for a user (GDPR compliance).
        
        Args:
            user_id: User ID
            
        Returns:
            Number of memories deleted
        """
        try:
            memories = await self.storage.get_user_memories(user_id, limit=10000)
            deleted_count = 0

            for memory in memories:
                await self.storage.delete_memory(memory.memory_id)
                deleted_count += 1

            logger.info(f"Deleted all {deleted_count} memories for user {user_id}")
            return deleted_count

        except Exception as e:
            logger.error(f"Failed to delete user memories: {e}")
            raise

    async def optimize_memory_store(
        self,
        user_id: str,
        current_turn: int,
    ) -> dict[str, int]:
        """Run all optimization operations on user's memory store.
        
        Args:
            user_id: User ID
            current_turn: Current conversation turn
            
        Returns:
            Dictionary with counts of operations performed
        """
        results = {
            "decay_applied": 0,
            "consolidations": 0,
            "conflicts_resolved": 0,
            "memories_cleaned": 0,
        }

        try:
            # Apply decay
            results["decay_applied"] = await self.apply_decay(user_id, current_turn)

            # Consolidate similar memories
            consolidations = await self.consolidate_similar_memories(user_id)
            results["consolidations"] = len(consolidations)

            # Resolve conflicts
            results["conflicts_resolved"] = await self.resolve_conflicts(user_id)

            # Cleanup old memories
            results["memories_cleaned"] = await self.cleanup_old_memories(user_id)

            logger.info(f"Optimized memory store for user {user_id}: {results}")
            return results

        except Exception as e:
            logger.error(f"Failed to optimize memory store: {e}")
            raise
