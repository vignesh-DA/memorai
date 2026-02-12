"""Memory lifecycle management with TTL (Time-To-Live) policies."""

import logging
from datetime import datetime, timedelta
from typing import Optional
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.memory import MemoryType

logger = logging.getLogger(__name__)


# 🔥 PRODUCTION FEATURE: Memory Lifecycle Policy
# Different memory types have different TTLs (time-to-live)
MEMORY_TTL_POLICY = {
    MemoryType.COMMITMENT: None,  # Until fulfilled (manual deletion)
    MemoryType.PREFERENCE: None,  # Persistent (no expiry)
    MemoryType.FACT: None,  # Persistent (no expiry)
    MemoryType.INSTRUCTION: None,  # Persistent (no expiry)
    MemoryType.ENTITY: 180,  # 180 days (6 months) - entities may become outdated
}

# Default TTL for temporary context (if we add TEMP_CONTEXT type later)
DEFAULT_TEMP_CONTEXT_TTL = 50  # turns


async def expire_old_memories(
    session: AsyncSession,
    user_id: Optional[str] = None,
    dry_run: bool = False
) -> int:
    """Expire memories based on TTL policy.
    
    Args:
        session: Database session
        user_id: User ID to expire memories for (optional, expires all users if None)
        dry_run: If True, only count expired memories without deleting
        
    Returns:
        Number of memories expired
    """
    try:
        # Build expiry date for different memory types
        now = datetime.utcnow()
        expired_count = 0
        
        # Check entity memories (180 day TTL)
        entity_expiry_date = now - timedelta(days=MEMORY_TTL_POLICY[MemoryType.ENTITY])
        
        # Build where clause
        where_clause = "type = 'entity' AND created_at < :expiry_date"
        params = {"expiry_date": entity_expiry_date}
        
        if user_id:
            where_clause += " AND user_id = :user_id"
            params["user_id"] = user_id
        
        if dry_run:
            # Count only
            query = text(f"""
                SELECT COUNT(*)
                FROM memories
                WHERE {where_clause}
            """)
            result = await session.execute(query, params)
            expired_count = result.scalar()
            logger.info(f"[DRY RUN] Would expire {expired_count} memories")
        else:
            # Delete expired memories
            query = text(f"""
                DELETE FROM memories
                WHERE {where_clause}
                RETURNING memory_id
            """)
            result = await session.execute(query, params)
            await session.commit()
            expired_count = result.rowcount
            logger.info(f"Expired {expired_count} old entity memories")
        
        return expired_count
        
    except Exception as e:
        logger.error(f"Failed to expire memories: {e}")
        await session.rollback()
        return 0


async def should_memory_expire(
    memory_type: MemoryType,
    created_at: datetime,
    turn_number: int = None,
    current_turn: int = None
) -> bool:
    """Check if a memory should expire based on TTL policy.
    
    Args:
        memory_type: Memory type
        created_at: When memory was created
        turn_number: Turn when memory was created (for turn-based TTL)
        current_turn: Current conversation turn (for turn-based TTL)
        
    Returns:
        True if memory should expire
    """
    ttl = MEMORY_TTL_POLICY.get(memory_type)
    
    if ttl is None:
        # Persistent memory, never expires
        return False
    
    # Check if TTL is turn-based (integer) or time-based
    if isinstance(ttl, int) and turn_number is not None and current_turn is not None:
        # Turn-based TTL
        turn_age = current_turn - turn_number
        return turn_age > ttl
    
    # Time-based TTL (days)
    if isinstance(ttl, int):
        age = datetime.utcnow() - created_at
        return age.days > ttl
    
    return False


async def mark_commitment_fulfilled(
    session: AsyncSession,
    memory_id: UUID
) -> bool:
    """Mark a commitment memory as fulfilled and schedule for deletion.
    
    Args:
        session: Database session
        memory_id: Memory ID
        
    Returns:
        True if marked successfully
    """
    try:
        # Add a "fulfilled" flag to context
        query = text("""
            UPDATE memories
            SET context = jsonb_set(
                COALESCE(context, '{}'::jsonb),
                '{fulfilled}',
                'true'::jsonb
            ),
            updated_at = :now
            WHERE memory_id = :memory_id
            AND type = 'commitment'
        """)
        
        await session.execute(query, {
            "memory_id": str(memory_id),
            "now": datetime.utcnow()
        })
        await session.commit()
        
        logger.info(f"Marked commitment {memory_id} as fulfilled")
        return True
        
    except Exception as e:
        logger.error(f"Failed to mark commitment fulfilled: {e}")
        await session.rollback()
        return False


async def cleanup_fulfilled_commitments(
    session: AsyncSession,
    user_id: Optional[str] = None,
    days_after_fulfillment: int = 7
) -> int:
    """Delete commitment memories that were fulfilled X days ago.
    
    Args:
        session: Database session
        user_id: User ID (optional)
        days_after_fulfillment: Days to wait after fulfillment before deletion
        
    Returns:
        Number of commitments deleted
    """
    try:
        cutoff_date = datetime.utcnow() - timedelta(days=days_after_fulfillment)
        
        where_clause = """
            type = 'commitment'
            AND context->>'fulfilled' = 'true'
            AND updated_at < :cutoff_date
        """
        params = {"cutoff_date": cutoff_date}
        
        if user_id:
            where_clause += " AND user_id = :user_id"
            params["user_id"] = user_id
        
        query = text(f"""
            DELETE FROM memories
            WHERE {where_clause}
            RETURNING memory_id
        """)
        
        result = await session.execute(query, params)
        await session.commit()
        deleted_count = result.rowcount
        
        logger.info(f"Deleted {deleted_count} fulfilled commitments older than {days_after_fulfillment} days")
        return deleted_count
        
    except Exception as e:
        logger.error(f"Failed to cleanup fulfilled commitments: {e}")
        await session.rollback()
        return 0


async def get_memory_stats_with_ttl(
    session: AsyncSession,
    user_id: str
) -> dict:
    """Get memory statistics including TTL information.
    
    Args:
        session: Database session
        user_id: User ID
        
    Returns:
        Dictionary with memory stats
    """
    try:
        query = text("""
            SELECT
                type,
                COUNT(*) as count,
                AVG(EXTRACT(DAYS FROM (NOW() - created_at))) as avg_age_days,
                COUNT(CASE WHEN context->>'fulfilled' = 'true' THEN 1 END) as fulfilled_count
            FROM memories
            WHERE user_id = :user_id
            GROUP BY type
        """)
        
        result = await session.execute(query, {"user_id": user_id})
        rows = result.fetchall()
        
        stats = {
            "by_type": {},
            "total_count": 0,
            "fulfilled_commitments": 0
        }
        
        for row in rows:
            memory_type = row[0]
            count = row[1]
            avg_age = row[2] or 0
            fulfilled = row[3] or 0
            
            stats["by_type"][memory_type] = {
                "count": count,
                "avg_age_days": round(avg_age, 1),
                "fulfilled_count": fulfilled if memory_type == "commitment" else None
            }
            stats["total_count"] += count
            
            if memory_type == "commitment":
                stats["fulfilled_commitments"] = fulfilled
        
        return stats
        
    except Exception as e:
        logger.error(f"Failed to get memory stats: {e}")
        return {"error": str(e)}
