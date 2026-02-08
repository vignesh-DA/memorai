"""Storage service for memory persistence."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from redis import asyncio as aioredis
from sqlalchemy import and_, delete, func, select, update, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.database import db_manager
from app.models.memory import (
    Memory,
    MemoryCreate,
    MemoryMetadata,
    MemoryStats,
    MemoryType,
    MemoryUpdate,
)
from app.utils.embeddings import EmbeddingGenerator
from app.utils.metrics import metrics, track_time

logger = logging.getLogger(__name__)
settings = get_settings()


class MemoryStorage:
    """Handle storage and retrieval of memories across multiple backends."""

    def __init__(
        self,
        session: AsyncSession,
        redis_client: aioredis.Redis,
        embedding_generator: EmbeddingGenerator,
    ):
        """Initialize memory storage.
        
        Args:
            session: Database session
            redis_client: Redis client for caching
            embedding_generator: Embedding generator instance
        """
        self.session = session
        self.redis = redis_client
        self.embedder = embedding_generator
        self.pinecone_index = db_manager.pinecone_index

    def _cache_key(self, memory_id: UUID) -> str:
        """Generate cache key for memory."""
        return f"memory:{str(memory_id)}"

    def _user_cache_key(self, user_id: str) -> str:
        """Generate cache key for user memory list."""
        return f"user_memories:{user_id}"

    async def _cache_memory(self, memory: Memory) -> None:
        """Cache memory in Redis."""
        try:
            cache_key = self._cache_key(memory.memory_id)
            cache_value = memory.model_dump_json()
            await self.redis.setex(
                cache_key,
                settings.redis_cache_ttl,
                cache_value,
            )
            metrics.record_cache_hit("memory")
        except Exception as e:
            logger.warning(f"Failed to cache memory: {e}")

    async def _get_cached_memory(self, memory_id: UUID) -> Optional[Memory]:
        """Retrieve memory from cache."""
        try:
            cache_key = self._cache_key(memory_id)
            cached = await self.redis.get(cache_key)
            if cached:
                metrics.record_cache_hit("memory")
                return Memory.model_validate_json(cached)
            metrics.record_cache_miss("memory")
        except Exception as e:
            logger.warning(f"Failed to get cached memory: {e}")
        return None

    async def _invalidate_user_cache(self, user_id: str) -> None:
        """Invalidate user's memory list cache."""
        try:
            cache_key = self._user_cache_key(user_id)
            await self.redis.delete(cache_key)
        except Exception as e:
            logger.warning(f"Failed to invalidate user cache: {e}")

    @track_time("create_memory")
    async def create_memory(self, memory_create: MemoryCreate) -> Memory:
        """Create a new memory.
        
        Args:
            memory_create: Memory creation data
            
        Returns:
            Created memory
            
        Raises:
            Exception: If creation fails
        """
        try:
            # Generate embedding
            embedding = await self.embedder.generate(memory_create.content)

            # Create memory object
            metadata = MemoryMetadata(
                source_turn=memory_create.source_turn,
                confidence=memory_create.confidence,
                tags=memory_create.tags,
                entities=memory_create.entities,
                context=memory_create.context,
            )

            memory = Memory(
                user_id=memory_create.user_id,
                type=memory_create.type,
                content=memory_create.content,
                embedding=embedding,
                metadata=metadata,
            )

            # Store in PostgreSQL with pgvector
            # Convert embedding list to string format for pgvector
            embedding_str = '[' + ','.join(map(str, embedding)) + ']'
            
            query = """
                INSERT INTO memories (
                    memory_id, user_id, type, content, embedding,
                    source_turn, created_at, confidence, tags, entities
                ) VALUES (
                    :memory_id, :user_id, :type, :content, :embedding,
                    :source_turn, :created_at, :confidence, :tags, :entities
                )
            """
            
            from sqlalchemy import text
            await self.session.execute(
                text(query),
                {
                    "memory_id": str(memory.memory_id),
                    "user_id": memory.user_id,
                    "type": memory.type.value,
                    "content": memory.content,
                    "embedding": embedding_str,
                    "source_turn": metadata.source_turn,
                    "created_at": metadata.created_at,
                    "confidence": metadata.confidence,
                    "tags": json.dumps(metadata.tags),
                    "entities": json.dumps(metadata.entities),
                },
            )

            # Store in Pinecone for vector search
            self.pinecone_index.upsert(
                vectors=[
                    {
                        "id": str(memory.memory_id),
                        "values": embedding,
                        "metadata": {
                            "user_id": memory.user_id,
                            "type": memory.type.value,
                            "content": memory.content[:1000],  # Pinecone metadata limit
                            "source_turn": metadata.source_turn,
                            "confidence": metadata.confidence,
                            "created_at": metadata.created_at.isoformat(),
                        },
                    }
                ]
            )

            # Cache the memory
            await self._cache_memory(memory)
            await self._invalidate_user_cache(memory.user_id)

            logger.info(f"Created memory {memory.memory_id} for user {memory.user_id}")
            return memory

        except Exception as e:
            logger.error(f"Failed to create memory: {e}")
            raise

    @track_time("get_memory")
    async def get_memory(self, memory_id: UUID) -> Optional[Memory]:
        """Retrieve a memory by ID.
        
        Args:
            memory_id: Memory UUID
            
        Returns:
            Memory if found, None otherwise
        """
        # Check cache first
        cached = await self._get_cached_memory(memory_id)
        if cached:
            return cached

        try:
            # Query from database
            query = text("""
                SELECT 
                    memory_id, user_id, type, content, embedding::text,
                    source_turn, created_at, last_accessed, access_count,
                    confidence, decay_score, tags, entities
                FROM memories
                WHERE memory_id = :memory_id
            """)
            
            result = await self.session.execute(query, {"memory_id": str(memory_id)})
            row = result.fetchone()

            if not row:
                return None

            # Reconstruct memory object
            embedding = [float(x) for x in row[4].strip('[]').split(',')]
            
            metadata = MemoryMetadata(
                source_turn=row[5],
                created_at=row[6],
                last_accessed=row[7] or row[6],
                access_count=row[8] or 0,
                confidence=row[9],
                decay_score=row[10] or 1.0,
                tags=json.loads(row[11]) if row[11] else [],
                entities=json.loads(row[12]) if row[12] else [],
            )

            memory = Memory(
                memory_id=UUID(row[0]),
                user_id=row[1],
                type=MemoryType(row[2]),
                content=row[3],
                embedding=embedding,
                metadata=metadata,
            )

            # Cache it
            await self._cache_memory(memory)

            return memory

        except Exception as e:
            logger.error(f"Failed to get memory {memory_id}: {e}")
            return None

    @track_time("update_memory")
    async def update_memory(
        self,
        memory_id: UUID,
        memory_update: MemoryUpdate,
    ) -> Optional[Memory]:
        """Update an existing memory.
        
        Args:
            memory_id: Memory UUID
            memory_update: Update data
            
        Returns:
            Updated memory if found, None otherwise
        """
        memory = await self.get_memory(memory_id)
        if not memory:
            return None

        try:
            update_fields = {}
            
            if memory_update.content is not None:
                memory.content = memory_update.content
                # Regenerate embedding
                memory.embedding = await self.embedder.generate(memory_update.content)
                update_fields["content"] = memory.content
                update_fields["embedding"] = str(memory.embedding)

            if memory_update.confidence is not None:
                memory.metadata.confidence = memory_update.confidence
                update_fields["confidence"] = memory_update.confidence

            if memory_update.tags is not None:
                memory.metadata.tags = memory_update.tags
                update_fields["tags"] = json.dumps(memory_update.tags)

            if memory_update.entities is not None:
                memory.metadata.entities = memory_update.entities
                update_fields["entities"] = json.dumps(memory_update.entities)

            if not update_fields:
                return memory

            # Update in PostgreSQL
            set_clause = ", ".join([f"{k} = :{k}" for k in update_fields.keys()])
            query = text(f"""
                UPDATE memories
                SET {set_clause}
                WHERE memory_id = :memory_id
            """)
            
            params = {**update_fields, "memory_id": str(memory_id)}
            await self.session.execute(query, params)

            # Update in Pinecone if embedding changed
            if "embedding" in update_fields:
                self.pinecone_index.upsert(
                    vectors=[
                        {
                            "id": str(memory.memory_id),
                            "values": memory.embedding,
                            "metadata": {
                                "user_id": memory.user_id,
                                "type": memory.type.value,
                                "content": memory.content[:1000],
                                "source_turn": memory.metadata.source_turn,
                                "confidence": memory.metadata.confidence,
                            },
                        }
                    ]
                )

            # Invalidate cache
            await self.redis.delete(self._cache_key(memory_id))
            await self._invalidate_user_cache(memory.user_id)

            logger.info(f"Updated memory {memory_id}")
            return memory

        except Exception as e:
            logger.error(f"Failed to update memory {memory_id}: {e}")
            raise

    @track_time("delete_memory")
    async def delete_memory(self, memory_id: UUID) -> bool:
        """Delete a memory.
        
        Args:
            memory_id: Memory UUID
            
        Returns:
            True if deleted, False if not found
        """
        memory = await self.get_memory(memory_id)
        if not memory:
            return False

        try:
            # Delete from PostgreSQL
            query = text("DELETE FROM memories WHERE memory_id = :memory_id")
            await self.session.execute(query, {"memory_id": str(memory_id)})

            # Delete from Pinecone
            self.pinecone_index.delete(ids=[str(memory_id)])

            # Remove from cache
            await self.redis.delete(self._cache_key(memory_id))
            await self._invalidate_user_cache(memory.user_id)

            logger.info(f"Deleted memory {memory_id}")
            return True

        except Exception as e:
            logger.error(f"Failed to delete memory {memory_id}: {e}")
            raise

    @track_time("get_user_memories")
    async def get_user_memories(
        self,
        user_id: str,
        memory_type: Optional[MemoryType] = None,
        limit: int = 100,
    ) -> list[Memory]:
        """Get all memories for a user.
        
        Args:
            user_id: User ID
            memory_type: Optional filter by type
            limit: Maximum number of memories to return
            
        Returns:
            List of memories
        """
        try:
            query_str = """
                SELECT 
                    memory_id, user_id, type, content, embedding::text,
                    source_turn, created_at, last_accessed, access_count,
                    confidence, decay_score, tags, entities
                FROM memories
                WHERE user_id = :user_id
            """
            
            params = {"user_id": user_id}
            
            if memory_type:
                query_str += " AND type = :type"
                params["type"] = memory_type.value

            query_str += " ORDER BY created_at DESC LIMIT :limit"
            params["limit"] = limit

            query = text(query_str)
            result = await self.session.execute(query, params)
            rows = result.fetchall()

            memories = []
            for row in rows:
                embedding = [float(x) for x in row[4].strip('[]').split(',')]
                
                metadata = MemoryMetadata(
                    source_turn=row[5],
                    created_at=row[6],
                    last_accessed=row[7] or row[6],
                    access_count=row[8] or 0,
                    confidence=row[9],
                    decay_score=row[10] or 1.0,
                    tags=json.loads(row[11]) if row[11] else [],
                    entities=json.loads(row[12]) if row[12] else [],
                )

                memory = Memory(
                    memory_id=UUID(row[0]),
                    user_id=row[1],
                    type=MemoryType(row[2]),
                    content=row[3],
                    embedding=embedding,
                    metadata=metadata,
                )
                memories.append(memory)

            return memories

        except Exception as e:
            logger.error(f"Failed to get user memories: {e}")
            raise

    async def get_user_stats(self, user_id: str) -> MemoryStats:
        """Get statistics about user's memories.
        
        Args:
            user_id: User ID
            
        Returns:
            Memory statistics
        """
        try:
            # Get counts by type
            query = text("""
                SELECT 
                    type,
                    COUNT(*) as count,
                    AVG(confidence) as avg_confidence,
                    MIN(source_turn) as oldest_turn,
                    MAX(source_turn) as newest_turn,
                    SUM(access_count) as total_accesses,
                    SUM(CASE WHEN access_count >= :hot_threshold THEN 1 ELSE 0 END) as hot_count
                FROM memories
                WHERE user_id = :user_id
                GROUP BY type
            """)
            
            result = await self.session.execute(
                query,
                {"user_id": user_id, "hot_threshold": settings.memory_cache_hot_threshold},
            )
            rows = result.fetchall()

            total_memories = 0
            memories_by_type = {}
            total_confidence = 0
            oldest_turn = float('inf')
            newest_turn = 0
            total_accesses = 0
            hot_memories = 0

            for row in rows:
                mem_type = MemoryType(row[0] )
                count = row[1]
                memories_by_type[mem_type] = count
                total_memories += count
                total_confidence += row[2] * count
                oldest_turn = min(oldest_turn, row[3] or oldest_turn)
                newest_turn = max(newest_turn, row[4] or newest_turn)
                total_accesses += row[5] or 0
                hot_memories += row[6] or 0

            avg_confidence = total_confidence / total_memories if total_memories > 0 else 0

            return MemoryStats(
                user_id=user_id,
                total_memories=total_memories,
                memories_by_type=memories_by_type,
                avg_confidence=avg_confidence,
                oldest_memory_turn=oldest_turn if oldest_turn != float('inf') else 0,
                newest_memory_turn=newest_turn,
                total_access_count=total_accesses,
                hot_memories=hot_memories,
            )

        except Exception as e:
            logger.error(f"Failed to get user stats: {e}")
            raise
