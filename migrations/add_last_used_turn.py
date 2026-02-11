"""Add last_used_turn column to memories table for hackathon compliance."""

import asyncio
import logging
import sys
from pathlib import Path
from sqlalchemy import text

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import db_manager

logger = logging.getLogger(__name__)


async def migrate():
    """Add last_used_turn column to memories table."""
    await db_manager.initialize()

    async with db_manager.get_session() as session:
        # Add last_used_turn column
        await session.execute(text("""
            ALTER TABLE memories 
            ADD COLUMN IF NOT EXISTS last_used_turn INTEGER DEFAULT NULL
        """))

        # Create index for performance
        await session.execute(text("""
            CREATE INDEX IF NOT EXISTS idx_memories_last_used_turn 
            ON memories(last_used_turn)
        """))

        await session.commit()

    logger.info("✅ Added last_used_turn column to memories table")
    await db_manager.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(migrate())
