"""
Memory Weighting and Decay System
Makes important memories last longer while less important ones fade
"""

import math
from datetime import datetime, timedelta
from typing import Optional
from enum import Enum


class ImportanceLevel(Enum):
    """Memory importance levels"""
    CRITICAL = "critical"  # Never decays: identity, goals, relationships
    HIGH = "high"  # Slow decay: preferences, skills, commitments
    MEDIUM = "medium"  # Normal decay: facts, interests
    LOW = "low"  # Fast decay: small talk, temporary info


class MemoryWeightCalculator:
    """Calculate and manage memory importance weights"""
    
    # Base importance scores by memory type
    TYPE_IMPORTANCE = {
        "ENTITY": 0.8,  # People, places important
        "FACT": 0.7,  # Facts moderately important
        "PREFERENCE": 0.75,  # Preferences fairly important
        "COMMITMENT": 0.9,  # Commitments very important
        "EPISODIC": 0.6,  # Conversations less important
        "INSTRUCTION": 0.85,  # Instructions very important
    }
    
    # Decay rates (weight loss per day)
    DECAY_RATES = {
        ImportanceLevel.CRITICAL: 0.0,  # No decay
        ImportanceLevel.HIGH: 0.001,  # 0.1% per day (~3 years to halve)
        ImportanceLevel.MEDIUM: 0.005,  # 0.5% per day (~140 days to halve)
        ImportanceLevel.LOW: 0.02,  # 2% per day (~35 days to halve)
    }
    
    # Keywords that boost importance
    CRITICAL_KEYWORDS = [
        "my name", "i am", "i'm called", "call me",
        "my wife", "my husband", "my partner", "my fiancé",
        "my goal", "i want to", "i plan to"
    ]
    
    HIGH_IMPORTANCE_KEYWORDS = [
        "always", "never", "important", "remember",
        "deadline", "appointment", "meeting", "promise"
    ]
    
    @classmethod
    def calculate_initial_weight(
        cls,
        memory_type: str,
        content: str,
        confidence: float,
        context: dict
    ) -> tuple[float, ImportanceLevel]:
        """Calculate initial memory weight and importance level.
        
        Args:
            memory_type: Type of memory (ENTITY, FACT, etc.)
            content: Memory content text
            confidence: Extraction confidence (0-1)
            context: Memory context metadata
            
        Returns:
            Tuple of (weight, importance_level)
        """
        # Start with base type importance
        base_weight = cls.TYPE_IMPORTANCE.get(memory_type, 0.5)
        
        content_lower = content.lower()
        
        # Check for critical keywords (identity, relationships, goals)
        if any(kw in content_lower for kw in cls.CRITICAL_KEYWORDS):
            importance = ImportanceLevel.CRITICAL
            weight = 1.0  # Maximum importance
        
        # Check for high importance keywords
        elif any(kw in content_lower for kw in cls.HIGH_IMPORTANCE_KEYWORDS):
            importance = ImportanceLevel.HIGH
            weight = min(base_weight * 1.3, 1.0)
        
        # Commitments and instructions are high importance
        elif memory_type in ["COMMITMENT", "INSTRUCTION"]:
            importance = ImportanceLevel.HIGH
            weight = base_weight
        
        # Preferences and entities are medium
        elif memory_type in ["PREFERENCE", "ENTITY"]:
            importance = ImportanceLevel.MEDIUM
            weight = base_weight
        
        # Facts and episodic are low by default
        else:
            importance = ImportanceLevel.LOW
            weight = base_weight * 0.8
        
        # Adjust by confidence
        weight *= confidence
        
        # Boost if mentioned multiple entities (more context)
        entities = context.get("entities", [])
        if len(entities) > 2:
            weight = min(weight * 1.1, 1.0)
        
        # Boost if has scheduled date (time-sensitive)
        if context.get("scheduled_date"):
            weight = min(weight * 1.2, 1.0)
            importance = ImportanceLevel.HIGH
        
        return round(weight, 3), importance
    
    @classmethod
    def calculate_current_weight(
        cls,
        initial_weight: float,
        importance_level: ImportanceLevel,
        created_at: datetime,
        access_count: int = 0,
        last_accessed: Optional[datetime] = None
    ) -> float:
        """Calculate current weight with time decay and access boost.
        
        Args:
            initial_weight: Original weight when created
            importance_level: Importance level (CRITICAL, HIGH, etc.)
            created_at: When memory was created
            access_count: Number of times accessed
            last_accessed: When last accessed
            
        Returns:
            Current decayed weight (0-1)
        """
        # CRITICAL memories never decay
        if importance_level == ImportanceLevel.CRITICAL:
            return initial_weight
        
        # Calculate time-based decay
        days_old = (datetime.utcnow() - created_at).days
        decay_rate = cls.DECAY_RATES[importance_level]
        
        # Exponential decay: weight = initial * e^(-decay_rate * days)
        time_decay = math.exp(-decay_rate * days_old)
        decayed_weight = initial_weight * time_decay
        
        # Boost by access count (each access adds 0.05, max +0.3)
        access_boost = min(access_count * 0.05, 0.3)
        
        # Boost if accessed recently (within 7 days)
        recency_boost = 0.0
        if last_accessed:
            days_since_access = (datetime.utcnow() - last_accessed).days
            if days_since_access < 7:
                recency_boost = 0.1 * (1 - days_since_access / 7)
        
        # Combine boosts (but can't exceed 1.0)
        final_weight = min(decayed_weight + access_boost + recency_boost, 1.0)
        
        return round(final_weight, 3)
    
    @classmethod
    def should_compress(
        cls,
        weight: float,
        days_old: int,
        importance_level: ImportanceLevel
    ) -> bool:
        """Determine if memory should be compressed.
        
        Low-weight old memories should be compressed into summaries.
        
        Args:
            weight: Current weight
            days_old: Age in days
            importance_level: Importance level
            
        Returns:
            True if should compress
        """
        # Never compress critical memories
        if importance_level == ImportanceLevel.CRITICAL:
            return False
        
        # Compress low-weight old memories
        if weight < 0.3 and days_old > 90:
            return True
        
        # Compress very old low-importance memories
        if importance_level == ImportanceLevel.LOW and days_old > 180:
            return True
        
        return False
    
    @classmethod
    def should_archive(
        cls,
        weight: float,
        days_old: int,
        importance_level: ImportanceLevel
    ) -> bool:
        """Determine if memory should be archived (removed from active recall).
        
        Very low weight memories can be archived to keep system fast.
        
        Args:
            weight: Current weight
            days_old: Age in days
            importance_level: Importance level
            
        Returns:
            True if should archive
        """
        # Never archive critical or high importance
        if importance_level in [ImportanceLevel.CRITICAL, ImportanceLevel.HIGH]:
            return False
        
        # Archive very low weight old memories
        if weight < 0.1 and days_old > 180:
            return True
        
        # Archive ancient low-importance memories
        if importance_level == ImportanceLevel.LOW and days_old > 365:
            return True
        
        return False
    
    @classmethod
    def calculate_retrieval_score(
        cls,
        similarity: float,
        current_weight: float,
        recency_days: int,
        access_count: int = 0
    ) -> float:
        """Calculate final retrieval score combining similarity, weight, and recency.
        
        Args:
            similarity: Semantic similarity score (0-1)
            current_weight: Current memory weight (0-1)
            recency_days: Days since creation
            access_count: Number of accesses
            
        Returns:
            Combined retrieval score (0-1)
        """
        # Weighted combination
        # Similarity: 50%, Weight: 30%, Recency: 15%, Access: 5%
        
        # Recency score (newer = higher, exponential decay)
        recency_score = math.exp(-recency_days / 30)  # Half-value at 30 days
        
        # Access score (more accessed = higher, logarithmic)
        access_score = min(math.log(access_count + 1) / 5, 1.0) if access_count > 0 else 0.0
        
        # Combine
        final_score = (
            similarity * 0.5 +
            current_weight * 0.3 +
            recency_score * 0.15 +
            access_score * 0.05
        )
        
        return round(final_score, 3)


class MemoryDecayManager:
    """Manage automatic memory decay"""
    
    @staticmethod
    async def decay_all_memories(storage):
        """Run decay calculation on all memories (should be scheduled).
        
        Args:
            storage: MemoryStorage instance
        """
        from app.models.memory import Memory
        
        # This would typically run as a background job (e.g., daily cron)
        # For now, it's a manual trigger endpoint
        
        memories = await storage.list_memories(limit=10000)  # Batch process
        
        updated_count = 0
        for memory in memories:
            # Calculate current weight
            current_weight = MemoryWeightCalculator.calculate_current_weight(
                initial_weight=memory.metadata.importance_score or 0.7,
                importance_level=ImportanceLevel(memory.metadata.importance_level or "medium"),
                created_at=memory.metadata.created_at,
                access_count=memory.metadata.access_count or 0,
                last_accessed=memory.metadata.last_accessed
            )
            
            # Update if changed significantly
            if abs(current_weight - memory.metadata.decay_score) > 0.05:
                await storage.update_memory_weight(memory.memory_id, current_weight)
                updated_count += 1
        
        return updated_count
