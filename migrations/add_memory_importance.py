"""
Database migration: Add memory importance and weighting fields
Run this script to add new columns to existing memories table
"""

import asyncio
from sqlalchemy import text

# Add parent directory to path for imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from app.database import db_manager
from app.config import get_settings

settings = get_settings()


async def migrate():
    """Add importance_score and importance_level columns to memories table"""
    
    try:
        print("Initializing database connection...")
        await db_manager._init_postgres()
        
        async with db_manager._engine.begin() as conn:
            print("Adding importance_score and importance_level columns...")
            
            # Add importance_score column (0.0 to 1.0)
            await conn.execute(text("""
                ALTER TABLE memories 
                ADD COLUMN IF NOT EXISTS importance_score DOUBLE PRECISION DEFAULT 0.7
            """))
            
            # Add importance_level column
            await conn.execute(text("""
                ALTER TABLE memories 
                ADD COLUMN IF NOT EXISTS importance_level VARCHAR(20) DEFAULT 'medium'
            """))
            
            # Create index on importance_score for fast sorting
            await conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_memories_importance 
                ON memories(importance_score DESC)
            """))
            
            # Update existing memories with calculated weights
            print("Updating existing memories with default weights...")
            await conn.execute(text("""
                UPDATE memories 
                SET importance_score = CASE 
                    WHEN type = 'commitment' THEN 0.9
                    WHEN type = 'instruction' THEN 0.85
                    WHEN type = 'entity' THEN 0.8
                    WHEN type = 'preference' THEN 0.75
                    WHEN type = 'fact' THEN 0.7
                    ELSE 0.6
                END * confidence
                WHERE importance_score = 0.7
            """))
            
            # Set importance levels based on type
            await conn.execute(text("""
                UPDATE memories
                SET importance_level = CASE
                    WHEN lower(content) LIKE '%my name%' 
                        OR lower(content) LIKE '%i am%'
                        OR lower(content) LIKE '%call me%' THEN 'critical'
                    WHEN type IN ('commitment', 'instruction') THEN 'high'
                    WHEN type IN ('preference', 'entity') THEN 'medium'
                    ELSE 'low'
                END
                WHERE importance_level = 'medium'
            """))
        
        print("✅ Migration completed successfully!")
        print("- Added importance_score column")
        print("- Added importance_level column")
        print("- Created importance index")
        print("- Updated existing memories")
        
    except Exception as e:
        print(f"❌ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        await db_manager.close()


if __name__ == "__main__":
    asyncio.run(migrate())
