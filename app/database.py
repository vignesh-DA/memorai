"""Database connection and session management."""

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from pinecone import Pinecone, ServerlessSpec
from redis import asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class DatabaseManager:
    """Manage database connections and lifecycle."""

    def __init__(self):
        """Initialize database manager."""
        self._engine = None
        self._session_factory = None
        self._redis_client: Optional[aioredis.Redis] = None
        self._pinecone_client: Optional[Pinecone] = None
        self._pinecone_index = None

    async def initialize(self) -> None:
        """Initialize all database connections."""
        await self._init_postgres()
        await self._init_redis()
        await self._init_pinecone()
        logger.info("All database connections initialized")

    async def _init_postgres(self) -> None:
        """Initialize PostgreSQL connection with connection pooling."""
        try:
            self._engine = create_async_engine(
                settings.postgres_async_url,
                pool_size=settings.connection_pool_size,
                max_overflow=settings.connection_pool_size * 2,
                pool_pre_ping=True,
                echo=settings.log_level == "DEBUG",
            )

            self._session_factory = async_sessionmaker(
                self._engine,
                class_=AsyncSession,
                expire_on_commit=False,
            )

            # Test connection and create extension
            async with self._engine.begin() as conn:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
                logger.info("PostgreSQL with pgvector initialized")

        except Exception as e:
            logger.error(f"Failed to initialize PostgreSQL: {e}")
            raise

    async def _init_redis(self) -> None:
        """Initialize Redis connection."""
        try:
            self._redis_client = aioredis.from_url(
                settings.redis_url,
                encoding="utf-8",
                decode_responses=False,
                max_connections=settings.connection_pool_size,
            )

            # Test connection
            await self._redis_client.ping()
            logger.info("Redis connection initialized")

        except Exception as e:
            logger.error(f"Failed to initialize Redis: {e}")
            raise

    async def _init_pinecone(self) -> None:
        """Initialize Pinecone vector database."""
        try:
            self._pinecone_client = Pinecone(api_key=settings.pinecone_api_key)

            # Check if index exists, create if not
            existing_indexes = [idx.name for idx in self._pinecone_client.list_indexes()]
            
            if settings.pinecone_index_name not in existing_indexes:
                logger.info(f"Creating Pinecone index: {settings.pinecone_index_name}")
                self._pinecone_client.create_index(
                    name=settings.pinecone_index_name,
                    dimension=settings.memory_embedding_dimension,
                    metric="cosine",
                    spec=ServerlessSpec(
                        cloud="aws",
                        region=settings.pinecone_environment,
                    ),
                )

            self._pinecone_index = self._pinecone_client.Index(settings.pinecone_index_name)
            logger.info(f"Pinecone index '{settings.pinecone_index_name}' initialized")

        except Exception as e:
            logger.error(f"Failed to initialize Pinecone: {e}")
            raise

    @asynccontextmanager
    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        """Get async database session.
        
        Yields:
            AsyncSession instance
        """
        if not self._session_factory:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @property
    def redis(self) -> aioredis.Redis:
        """Get Redis client."""
        if not self._redis_client:
            raise RuntimeError("Redis not initialized. Call initialize() first.")
        return self._redis_client

    @property
    def pinecone_index(self):
        """Get Pinecone index."""
        if not self._pinecone_index:
            raise RuntimeError("Pinecone not initialized. Call initialize() first.")
        return self._pinecone_index

    async def close(self) -> None:
        """Close all database connections."""
        if self._engine:
            await self._engine.dispose()
            logger.info("PostgreSQL connection closed")

        if self._redis_client:
            await self._redis_client.close()
            logger.info("Redis connection closed")

        logger.info("All database connections closed")

    async def health_check(self) -> dict[str, str]:
        """Check health of all database connections.
        
        Returns:
            Dictionary with health status of each connection
        """
        health = {}

        # Check PostgreSQL
        try:
            async with self.get_session() as session:
                await session.execute(text("SELECT 1"))
            health["postgres"] = "healthy"
        except Exception as e:
            health["postgres"] = f"unhealthy: {str(e)}"

        # Check Redis
        try:
            await self.redis.ping()
            health["redis"] = "healthy"
        except Exception as e:
            health["redis"] = f"unhealthy: {str(e)}"

        # Check Pinecone
        try:
            self.pinecone_index.describe_index_stats()
            health["pinecone"] = "healthy"
        except Exception as e:
            health["pinecone"] = f"unhealthy: {str(e)}"

        return health


# Global database manager instance
db_manager = DatabaseManager()


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI to get database session."""
    async with db_manager.get_session() as session:
        yield session
