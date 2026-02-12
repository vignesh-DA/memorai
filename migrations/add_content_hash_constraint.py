"""Add unique constraint on (user_id, content_hash) to prevent duplicate memories.

This migration creates a content_hash column and adds a unique constraint
to prevent storing identical memories for the same user.

Run: python -m migrations.add_content_hash_constraint
"""

import asyncio
import hashlib
import logging
from sqlalchemy import text
from app.database import db_manager

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def hash_content(content: str) -> str:
    """Generate SHA256 hash of content for deduplication.
    
    Args:
        content: Memory content text
        
    Returns:
        Hex digest of SHA256 hash
    """
    return hashlib.sha256(content.lower().strip().encode('utf-8')).hexdigest()


async def migrate():
    """Add content_hash column and unique constraint."""
    logger.info("Starting migration: add_content_hash_constraint")
    
    async with db_manager.get_session() as session:
        try:
            # Step 1: Add content_hash column
            logger.info("Adding content_hash column...")
            await session.execute(text("""
                ALTER TABLE memories 
                ADD COLUMN IF NOT EXISTS content_hash VARCHAR(64)
            """))
            await session.commit()
            logger.info("✅ content_hash column added")
            
            # Step 2: Populate content_hash for existing memories
            logger.info("Populating content_hash for existing memories...")
            result = await session.execute(text("""
                SELECT memory_id, content FROM memories WHERE content_hash IS NULL
            """))
            rows = result.fetchall()
            
            logger.info(f"Found {len(rows)} memories without content_hash")
            
            for memory_id, content in rows:
                content_hash = hash_content(content)
                await session.execute(
                    text("""
                        UPDATE memories 
                        SET content_hash = :hash 
                        WHERE memory_id = :id
                    """),
                    {"hash": content_hash, "id": str(memory_id)}
                )
            
            await session.commit()
            logger.info(f"✅ Populated content_hash for {len(rows)} memories")
            
            # Step 3: Create unique index (prevents duplicate inserts)
            logger.info("Creating unique index on (user_id, content_hash)...")
            
            # Drop existing duplicates before adding constraint
            await session.execute(text("""
                DELETE FROM memories m1
                USING memories m2
                WHERE m1.memory_id > m2.memory_id
                  AND m1.user_id = m2.user_id
                  AND m1.content_hash = m2.content_hash
            """))
            await session.commit()
            logger.info("✅ Removed duplicate memories")
            
            # Add unique constraint
            await session.execute(text("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_memories_user_content_hash
                ON memories(user_id, content_hash)
            """))
            await session.commit()
            logger.info("✅ Unique index created")
            
            logger.info("Migration completed successfully!")
            
        except Exception as e:
            await session.rollback()
            logger.error(f"Migration failed: {e}")
            raise


async def rollback():
    """Remove content_hash column and constraint."""
    logger.info("Rolling back migration: add_content_hash_constraint")
    
    async with db_manager.get_session() as session:
        try:
            await session.execute(text("""
                DROP INDEX IF EXISTS idx_memories_user_content_hash
            """))
            await session.execute(text("""
                ALTER TABLE memories DROP COLUMN IF EXISTS content_hash
            """))
            await session.commit()
            logger.info("✅ Rollback completed")
            
        except Exception as e:
            await session.rollback()
            logger.error(f"Rollback failed: {e}")
            raise


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1 and sys.argv[1] == "rollback":
        asyncio.run(rollback())
    else:
        asyncio.run(migrate())
