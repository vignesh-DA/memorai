"""Embedding generation utilities."""

import asyncio
import hashlib
import json
import logging
from functools import lru_cache
from typing import Optional

import openai
from redis import asyncio as aioredis

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()

# Try to import sentence transformers
try:
    from sentence_transformers import SentenceTransformer
    SENTENCE_TRANSFORMERS_AVAILABLE = True
except ImportError:
    SENTENCE_TRANSFORMERS_AVAILABLE = False
    logger.warning("sentence-transformers not available")


@lru_cache(maxsize=1)
def _get_sentence_transformer(model_name: str):
    """Cached loader for SentenceTransformer model.
    
    Args:
        model_name: Name of the model to load
        
    Returns:
        Loaded SentenceTransformer model
    """
    logger.info(f"Loading Sentence Transformer model: {model_name}")
    model = SentenceTransformer(model_name)
    logger.info(f"✅ Sentence Transformers ready. Dimension: {model.get_sentence_embedding_dimension()}")
    return model


class EmbeddingGenerator:
    """Generate and cache embeddings using OpenAI or Sentence Transformers."""

    def __init__(
        self,
        redis_client: Optional[aioredis.Redis] = None,
    ):
        """Initialize embedding generator.
        
        Args:
            redis_client: Optional Redis client for caching
        """
        self.redis = redis_client
        self.cache_ttl = settings.redis_cache_ttl
        
        # Initialize based on provider
        if settings.embedding_provider == "sentence-transformers":
            if not SENTENCE_TRANSFORMERS_AVAILABLE:
                raise ImportError("sentence-transformers not installed. Run: pip install sentence-transformers")
            self.st_model = _get_sentence_transformer(settings.embedding_model)
            self.dimension = self.st_model.get_sentence_embedding_dimension()
            self.use_openai = False
            self.model = settings.embedding_model
        else:
            self.client = openai.AsyncOpenAI(api_key=settings.openai_api_key)
            self.model = settings.openai_embedding_model
            self.dimension = settings.memory_embedding_dimension
            self.use_openai = True
            logger.info(f"OpenAI embeddings ready. Model: {self.model}")

    def _cache_key(self, text: str) -> str:
        """Generate cache key for text."""
        text_hash = hashlib.sha256(text.encode()).hexdigest()
        provider = "openai" if self.use_openai else "st"
        return f"embedding:{provider}:{self.model}:{text_hash}"

    async def generate(self, text: str) -> list[float]:
        """Generate embedding for a single text.
        
        Args:
            text: Text to embed
            
        Returns:
            Embedding vector
            
        Raises:
            Exception: If embedding generation fails
        """
        # Check cache first
        if self.redis:
            cache_key = self._cache_key(text)
            try:
                cached = await self.redis.get(cache_key)
                if cached:
                    logger.debug(f"Embedding cache hit for text length {len(text)}")
                    return json.loads(cached)
            except Exception as e:
                logger.warning(f"Cache read error: {e}")

        # Generate embedding based on provider
        if self.use_openai:
            embedding = await self._generate_openai(text)
        else:
            embedding = self._generate_sentence_transformer(text)

        # Cache the result
        if self.redis:
            try:
                await self.redis.setex(
                    cache_key,
                    self.cache_ttl,
                    json.dumps(embedding),
                )
            except Exception as e:
                logger.warning(f"Cache write error: {e}")

        logger.debug(f"Generated embedding for text length {len(text)}")
        return embedding

    async def _generate_openai(self, text: str) -> list[float]:
        """Generate embedding using OpenAI API."""
        try:
            response = await self.client.embeddings.create(
                input=text,
                model=self.model,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"OpenAI embedding generation failed: {e}")
            raise

    def _generate_sentence_transformer(self, text: str) -> list[float]:
        """Generate embedding using Sentence Transformers (local, synchronous)."""
        embedding = self.st_model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    async def generate_batch(
        self,
        texts: list[str],
        batch_size: Optional[int] = None,
    ) -> list[list[float]]:
        """Generate embeddings for multiple texts efficiently.
        
        Args:
            texts: List of texts to embed
            batch_size: Batch size for API calls (default from settings)
            
        Returns:
            List of embedding vectors
        """
        if not texts:
            return []

        if batch_size is None:
            batch_size = settings.batch_embedding_size

        embeddings = []
        
        # Process in batches
        for i in range(0, len(texts), batch_size):
            batch = texts[i:i + batch_size]
            
            # Check cache for each text in batch
            batch_embeddings = []
            uncached_indices = []
            uncached_texts = []
            
            for idx, text in enumerate(batch):
                if self.redis:
                    cache_key = self._cache_key(text)
                    try:
                        cached = await self.redis.get(cache_key)
                        if cached:
                            batch_embeddings.append(
                                [float(x) for x in cached.decode().split(",")]
                            )
                            continue
                    except Exception:
                        pass
                
                # Not in cache
                uncached_indices.append(len(batch_embeddings))
                uncached_texts.append(text)
                batch_embeddings.append(None)  # Placeholder

            # Generate embeddings for uncached texts
            if uncached_texts:
                try:
                    response = await self.client.embeddings.create(
                        input=uncached_texts,
                        model=self.model,
                    )
                    
                    # Insert generated embeddings
                    for idx, embedding_data in enumerate(response.data):
                        embedding = embedding_data.embedding
                        batch_embeddings[uncached_indices[idx]] = embedding
                        
                        # Cache the result
                        if self.redis:
                            try:
                                cache_key = self._cache_key(uncached_texts[idx])
                                cache_value = ",".join(str(x) for x in embedding)
                                await self.redis.setex(
                                    cache_key,
                                    self.cache_ttl,
                                    cache_value,
                                )
                            except Exception as e:
                                logger.warning(f"Cache write error: {e}")

                except Exception as e:
                    logger.error(f"Batch embedding generation failed: {e}")
                    raise

            embeddings.extend(batch_embeddings)
            
            # Small delay between batches to avoid rate limits
            if i + batch_size < len(texts):
                await asyncio.sleep(0.1)

        logger.info(f"Generated {len(embeddings)} embeddings in batches")
        return embeddings

    async def similarity(
        self,
        embedding1: list[float],
        embedding2: list[float],
    ) -> float:
        """Calculate cosine similarity between two embeddings.
        
        Args:
            embedding1: First embedding vector
            embedding2: Second embedding vector
            
        Returns:
            Similarity score between 0 and 1
        """
        dot_product = sum(a * b for a, b in zip(embedding1, embedding2))
        magnitude1 = sum(a * a for a in embedding1) ** 0.5
        magnitude2 = sum(b * b for b in embedding2) ** 0.5
        
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        
        similarity = dot_product / (magnitude1 * magnitude2)
        # Normalize to 0-1 range (cosine similarity is -1 to 1)
        return (similarity + 1) / 2
