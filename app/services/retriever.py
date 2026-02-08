"""Memory retrieval service with hybrid search."""

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import Optional

from redis import asyncio as aioredis

from app.config import get_settings
from app.database import db_manager
from app.models.memory import Memory, MemorySearchQuery, MemorySearchResult, MemoryType
from app.utils.embeddings import EmbeddingGenerator
from app.utils.metrics import metrics, track_latency

logger = logging.getLogger(__name__)
settings = get_settings()


class MemoryRetriever:
    """Retrieve relevant memories using hybrid search."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        embedding_generator: EmbeddingGenerator,
    ):
        """Initialize memory retriever.
        
        Args:
            redis_client: Redis client for caching
            embedding_generator: Embedding generator instance
        """
        self.redis = redis_client
        self.embedder = embedding_generator
        self.pinecone_index = db_manager.pinecone_index

    async def search(
        self,
        query: MemorySearchQuery,
    ) -> list[MemorySearchResult]:
        """Search for relevant memories using hybrid approach.
        
        Combines:
        - Semantic similarity (vector search)
        - Recency score
        - Access frequency
        - Confidence score
        
        Args:
            query: Search query parameters
            
        Returns:
            List of memory search results sorted by relevance
        """
        async with track_latency("memory_retrieval") as timing:
            try:
                # Generate query embedding
                query_embedding = await self.embedder.generate(query.query)

                # Perform vector search in Pinecone
                search_results = self.pinecone_index.query(
                    vector=query_embedding,
                    filter={
                        "user_id": {"$eq": query.user_id},
                    },
                    top_k=min(query.top_k * 3, 50),  # Get more candidates for reranking
                    include_metadata=True,
                )

                # Process and rank results
                results = []
                current_turn = query.current_turn or 0

                for match in search_results.matches:
                    try:
                        # Extract metadata
                        metadata = match.metadata
                        memory_type = MemoryType(metadata.get('type', 'fact'))

                        # Filter by memory type if specified
                        if query.memory_types and memory_type not in query.memory_types:
                            continue

                        # Filter by confidence
                        confidence = float(metadata.get('confidence', 0.7))
                        if confidence < query.min_confidence:
                            continue

                        # Calculate similarity score (already from vector search)
                        similarity_score = float(match.score)

                        # Calculate recency score
                        source_turn = int(metadata.get('source_turn', 0))
                        recency_score = self._calculate_recency_score(
                            source_turn,
                            current_turn,
                        )

                        # Calculate access frequency score (simulated from confidence)
                        # In production, track actual access patterns
                        access_score = confidence

                        # Calculate composite relevance score
                        relevance_score = self._calculate_relevance_score(
                            similarity_score,
                            recency_score,
                            access_score,
                            confidence,
                        )

                        # Create memory object from Pinecone metadata
                        from uuid import UUID
                        from app.models.memory import MemoryMetadata
                        
                        created_at = datetime.fromisoformat(
                            metadata.get('created_at', datetime.utcnow().isoformat())
                        )

                        memory_metadata = MemoryMetadata(
                            source_turn=source_turn,
                            created_at=created_at,
                            last_accessed=created_at,
                            access_count=0,
                            confidence=confidence,
                            decay_score=recency_score,
                        )

                        memory = Memory(
                            memory_id=UUID(match.id),
                            user_id=query.user_id,
                            type=memory_type,
                            content=metadata.get('content', ''),
                            embedding=None,  # Don't include full embedding in results
                            metadata=memory_metadata,
                        )

                        result = MemorySearchResult(
                            memory=memory,
                            relevance_score=relevance_score,
                            similarity_score=similarity_score,
                            recency_score=recency_score,
                            access_score=access_score,
                        )
                        results.append(result)

                    except Exception as e:
                        logger.warning(f"Error processing search result: {e}")
                        continue

                # Sort by relevance and limit to top_k
                results.sort(key=lambda x: x.relevance_score, reverse=True)
                results = results[:query.top_k]

                logger.info(f"Retrieved {len(results)} memories for user {query.user_id}")

                return results

            except Exception as e:
                logger.error(f"Memory search failed: {e}")
                return []

    def _calculate_recency_score(
        self,
        source_turn: int,
        current_turn: int,
    ) -> float:
        """Calculate recency score based on turn distance.
        
        Args:
            source_turn: Turn when memory was created
            current_turn: Current conversation turn
            
        Returns:
            Recency score between 0 and 1
        """
        if current_turn <= 0:
            return 1.0

        turn_distance = current_turn - source_turn
        if turn_distance <= 0:
            return 1.0

        # Exponential decay with half-life of 100 turns
        decay_rate = 0.993  # 0.5^(1/100)
        recency_score = decay_rate ** turn_distance

        return max(recency_score, 0.1)  # Minimum score of 0.1

    def _calculate_relevance_score(
        self,
        similarity: float,
        recency: float,
        access: float,
        confidence: float,
    ) -> float:
        """Calculate composite relevance score.
        
        Weighted combination:
        - 40% semantic similarity
        - 30% recency
        - 20% access frequency
        - 10% confidence
        
        Args:
            similarity: Vector similarity score
            recency: Recency score
            access: Access frequency score
            confidence: Confidence score
            
        Returns:
            Composite relevance score between 0 and 1
        """
        score = (
            0.4 * similarity +
            0.3 * recency +
            0.2 * access +
            0.1 * confidence
        )
        return min(max(score, 0.0), 1.0)

    async def get_recent_memories(
        self,
        user_id: str,
        limit: int = 10,
        memory_type: Optional[MemoryType] = None,
    ) -> list[Memory]:
        """Get most recent memories for a user.
        
        Args:
            user_id: User ID
            limit: Maximum number of memories
            memory_type: Optional filter by type
            
        Returns:
            List of recent memories
        """
        try:
            # Query Pinecone with filter
            filter_dict = {"user_id": {"$eq": user_id}}
            if memory_type:
                filter_dict["type"] = {"$eq": memory_type.value}

            # Use a dummy vector to get all matching
            # In production, maintain a sorted index by timestamp
            results = self.pinecone_index.query(
                vector=[0.0] * settings.memory_embedding_dimension,
                filter=filter_dict,
                top_k=limit,
                include_metadata=True,
            )

            memories = []
            for match in results.matches:
                try:
                    metadata = match.metadata
                    from uuid import UUID

                    memory = Memory(
                        memory_id=UUID(match.id),
                        user_id=user_id,
                        type=MemoryType(metadata.get('type', 'fact')),
                        content=metadata.get('content', ''),
                        embedding=None,
                        metadata=MemoryMetadata(
                            source_turn=int(metadata.get('source_turn', 0)),
                            created_at=datetime.fromisoformat(
                                metadata.get('created_at', datetime.utcnow().isoformat())
                            ),
                            confidence=float(metadata.get('confidence', 0.7)),
                        ),
                    )
                    memories.append(memory)
                except Exception as e:
                    logger.warning(f"Error processing recent memory: {e}")
                    continue

            return memories

        except Exception as e:
            logger.error(f"Failed to get recent memories: {e}")
            return []

    async def get_hot_memories(
        self,
        user_id: str,
        limit: int = 10,
    ) -> list[Memory]:
        """Get frequently accessed (hot) memories for a user.
        
        Args:
            user_id: User ID
            limit: Maximum number of memories
            
        Returns:
            List of hot memories
        """
        # Check cache first
        cache_key = f"hot_memories:{user_id}"
        try:
            cached = await self.redis.get(cache_key)
            if cached:
                metrics.record_cache_hit("hot_memories")
                # Parse and return cached memories
                # For simplicity, return empty list here
                # In production, deserialize properly
        except Exception:
            pass

        # For now, return recent memories as a proxy
        # In production, track actual access patterns
        return await self.get_recent_memories(user_id, limit)

    async def find_similar_memories(
        self,
        memory: Memory,
        threshold: float = 0.85,
    ) -> list[Memory]:
        """Find memories similar to a given memory.
        
        Useful for:
        - Detecting duplicates
        - Finding related memories
        - Consolidation candidates
        
        Args:
            memory: Memory to find similar to
            threshold: Similarity threshold (0-1)
            
        Returns:
            List of similar memories
        """
        if not memory.embedding:
            return []

        try:
            results = self.pinecone_index.query(
                vector=memory.embedding,
                filter={
                    "user_id": {"$eq": memory.user_id},
                },
                top_k=20,
                include_metadata=True,
            )

            similar_memories = []
            for match in results.matches:
                # Skip the memory itself
                if match.id == str(memory.memory_id):
                    continue

                # Filter by similarity threshold
                if match.score < threshold:
                    continue

                try:
                    metadata = match.metadata
                    from uuid import UUID

                    similar_memory = Memory(
                        memory_id=UUID(match.id),
                        user_id=memory.user_id,
                        type=MemoryType(metadata.get('type', 'fact')),
                        content=metadata.get('content', ''),
                        embedding=None,
                        metadata=MemoryMetadata(
                            source_turn=int(metadata.get('source_turn', 0)),
                            created_at=datetime.fromisoformat(
                                metadata.get('created_at', datetime.utcnow().isoformat())
                            ),
                            confidence=float(metadata.get('confidence', 0.7)),
                        ),
                    )
                    similar_memories.append(similar_memory)
                except Exception as e:
                    logger.warning(f"Error processing similar memory: {e}")
                    continue

            return similar_memories

        except Exception as e:
            logger.error(f"Failed to find similar memories: {e}")
            return []
