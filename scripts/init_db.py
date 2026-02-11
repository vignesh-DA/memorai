"""Database schema initialization."""

import asyncio
import logging
import sys
from pathlib import Path
from sqlalchemy import text

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.config import get_settings
from app.database import db_manager

logger = logging.getLogger(__name__)
settings = get_settings()


async def create_schema():
    """Create database schema."""
    await db_manager.initialize()

    async with db_manager.get_session() as session:
        # Create memories table
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS memories (
                memory_id UUID PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                type VARCHAR(50) NOT NULL,
                content TEXT NOT NULL,
                embedding vector(384),
                source_turn INTEGER NOT NULL,
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                last_accessed TIMESTAMP,
                access_count INTEGER DEFAULT 0,
                confidence FLOAT NOT NULL,
                decay_score FLOAT DEFAULT 1.0,
                tags JSONB DEFAULT '[]'::jsonb,
                entities JSONB DEFAULT '[]'::jsonb,
                context JSONB DEFAULT '{}'::jsonb
            )
        """))

        # Create indexes
        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_memories_user_id 
            ON memories(user_id)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_memories_type 
            ON memories(type)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_memories_source_turn 
            ON memories(source_turn)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_memories_created_at 
            ON memories(created_at DESC)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_memories_confidence 
            ON memories(confidence)
        """))

        # Create conversations table
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS conversations (
                conversation_id UUID PRIMARY KEY,
                user_id VARCHAR(255) NOT NULL,
                title VARCHAR(500),
                created_at TIMESTAMP NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMP NOT NULL DEFAULT NOW(),
                is_archived BOOLEAN DEFAULT FALSE,
                turn_count INTEGER DEFAULT 0,
                metadata JSONB DEFAULT '{}'::jsonb
            )
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_conversations_user_id 
            ON conversations(user_id)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_conversations_updated_at 
            ON conversations(updated_at DESC)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_conversations_archived 
            ON conversations(is_archived)
        """))

        # Create conversation_turns table
        await session.execute(text("""
            CREATE TABLE IF NOT EXISTS conversation_turns (
                turn_id UUID PRIMARY KEY,
                conversation_id UUID NOT NULL,
                user_id VARCHAR(255) NOT NULL,
                turn_number INTEGER NOT NULL,
                user_message TEXT NOT NULL,
                assistant_message TEXT,
                timestamp TIMESTAMP NOT NULL DEFAULT NOW(),
                metadata JSONB DEFAULT '{}'::jsonb,
                memories_retrieved UUID[],
                memories_created UUID[],
                FOREIGN KEY (conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
            )
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_turns_conversation_id 
            ON conversation_turns(conversation_id)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_turns_user_id 
            ON conversation_turns(user_id)
        """))

        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_turns_number 
            ON conversation_turns(turn_number)
        """))

        await session.commit()

    logger.info("Database schema created successfully")
    await db_manager.close()


async def drop_schema():
    """Drop database schema (use with caution)."""
    await db_manager.initialize()

    async with db_manager.get_session() as session:
        await session.execute(text("DROP TABLE IF EXISTS conversation_turns CASCADE"))
        await session.execute(text("DROP TABLE IF EXISTS conversations CASCADE"))
        await session.execute(text("DROP TABLE IF EXISTS memories CASCADE"))
        await session.commit()

    logger.info("Database schema dropped")
    await db_manager.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(create_schema())
