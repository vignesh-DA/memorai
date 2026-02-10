"""
Memory Conflict Resolution System
Detects and resolves conflicting memories automatically
"""

import logging
from datetime import datetime
from typing import Optional, List, Tuple
from uuid import UUID
from app.models.memory import Memory, MemoryType
from app.llm_client import llm_client

logger = logging.getLogger(__name__)


class ConflictType:
    """Types of memory conflicts"""
    LOCATION_CHANGE = "location_change"  # User moved
    STATUS_CHANGE = "status_change"  # Relationship/job status changed
    PREFERENCE_CHANGE = "preference_change"  # Likes/dislikes changed
    FACTUAL_CONTRADICTION = "factual_contradiction"  # Direct contradiction
    TEMPORAL_UPDATE = "temporal_update"  # Time-bound info updated


class MemoryConflictResolver:
    """Detect and resolve conflicts between memories"""
    
    # Conflict patterns
    CONFLICT_PATTERNS = {
        "location": ["live in", "based in", "located in", "from", "moved to"],
        "job": ["work at", "working at", "employed by", "job at", "position at"],
        "relationship": ["married to", "dating", "engaged to", "partner", "single"],
        "age": ["years old", "age is", "age:"],
        "preference": ["like", "love", "hate", "dislike", "prefer"],
    }
    
    @classmethod
    async def check_conflict(
        cls,
        new_memory: Memory,
        existing_memories: List[Memory]
    ) -> Optional[Tuple[Memory, str]]:
        """Check if new memory conflicts with existing ones.
        
        Args:
            new_memory: Newly created memory
            existing_memories: List of user's existing memories
            
        Returns:
            Tuple of (conflicting_memory, conflict_type) or None
        """
        new_content_lower = new_memory.content.lower()
        
        # Check each existing memory for conflicts
        for existing in existing_memories:
            existing_content_lower = existing.content.lower()
            
            # Skip if same memory
            if existing.memory_id == new_memory.memory_id:
                continue
            
            # Check location conflicts
            if cls._has_pattern(new_content_lower, cls.CONFLICT_PATTERNS["location"]):
                if cls._has_pattern(existing_content_lower, cls.CONFLICT_PATTERNS["location"]):
                    # Both mention location - check if different
                    if await cls._are_conflicting(new_memory.content, existing.content, "location"):
                        return (existing, ConflictType.LOCATION_CHANGE)
            
            # Check job conflicts
            if cls._has_pattern(new_content_lower, cls.CONFLICT_PATTERNS["job"]):
                if cls._has_pattern(existing_content_lower, cls.CONFLICT_PATTERNS["job"]):
                    if await cls._are_conflicting(new_memory.content, existing.content, "job"):
                        return (existing, ConflictType.STATUS_CHANGE)
            
            # Check relationship conflicts
            if cls._has_pattern(new_content_lower, cls.CONFLICT_PATTERNS["relationship"]):
                if cls._has_pattern(existing_content_lower, cls.CONFLICT_PATTERNS["relationship"]):
                    if await cls._are_conflicting(new_memory.content, existing.content, "relationship"):
                        return (existing, ConflictType.STATUS_CHANGE)
            
            # Check age conflicts (should only increase)
            if cls._has_pattern(new_content_lower, cls.CONFLICT_PATTERNS["age"]):
                if cls._has_pattern(existing_content_lower, cls.CONFLICT_PATTERNS["age"]):
                    if await cls._are_conflicting(new_memory.content, existing.content, "age"):
                        return (existing, ConflictType.FACTUAL_CONTRADICTION)
            
            # Check preference changes (less critical)
            if new_memory.type == MemoryType.PREFERENCE and existing.type == MemoryType.PREFERENCE:
                if await cls._are_conflicting(new_memory.content, existing.content, "preference"):
                    return (existing, ConflictType.PREFERENCE_CHANGE)
        
        return None
    
    @classmethod
    def _has_pattern(cls, text: str, patterns: List[str]) -> bool:
        """Check if text contains any of the patterns"""
        return any(pattern in text for pattern in patterns)
    
    @classmethod
    async def _are_conflicting(
        cls,
        content1: str,
        content2: str,
        category: str
    ) -> bool:
        """Use LLM to determine if two statements conflict.
        
        Args:
            content1: First statement
            content2: Second statement
            category: Category of conflict (location, job, etc.)
            
        Returns:
            True if conflicting
        """
        try:
            prompt = f"""Determine if these two statements about {category} conflict:

Statement 1: {content1}
Statement 2: {content2}

Return ONLY a JSON object with this format:
{{"conflict": true/false, "reason": "brief explanation"}}

Examples of conflicts:
- "Lives in Chennai" vs "Lives in Bangalore" = TRUE
- "Works at Google" vs "Works at Microsoft" = TRUE
- "28 years old" vs "30 years old" = TRUE (unless time passed)
- "Likes pizza" vs "Loves pizza" = FALSE (same preference)
"""
            
            response = await llm_client.extract_json([
                {"role": "system", "content": "You are a conflict detection system. Determine if statements contradict."},
                {"role": "user", "content": prompt}
            ])
            
            return response.get("conflict", False)
        
        except Exception as e:
            logger.warning(f"Conflict detection failed: {e}")
            # Conservative: assume no conflict if detection fails
            return False
    
    @classmethod
    async def resolve_conflict(
        cls,
        new_memory: Memory,
        old_memory: Memory,
        conflict_type: str,
        storage
    ) -> str:
        """Resolve conflict between memories.
        
        Strategy:
        - Newer memories win (usually updates)
        - Decrease importance of old memory
        - Add context to both memories
        - Update user profile if applicable
        
        Args:
            new_memory: New conflicting memory
            old_memory: Existing memory
            conflict_type: Type of conflict
            storage: Storage instance for updates
            
        Returns:
            Resolution strategy used
        """
        try:
            # Determine resolution strategy
            resolution = "newer_wins"
            
            # Critical conflicts (location, job changes) - mark old as outdated
            if conflict_type in [ConflictType.LOCATION_CHANGE, ConflictType.STATUS_CHANGE]:
                # Decrease importance of old memory
                await storage.update_memory_importance(
                    memory_id=old_memory.memory_id,
                    new_importance=0.3,  # Mark as outdated
                    importance_level="low"
                )
                
                # Add context to old memory
                old_memory.metadata.context["superseded_by"] = str(new_memory.memory_id)
                old_memory.metadata.context["superseded_at"] = datetime.utcnow().isoformat()
                old_memory.metadata.context["resolution"] = "outdated_information"
                
                # Update old memory with context
                await storage.update_memory_context(
                    memory_id=old_memory.memory_id,
                    context=old_memory.metadata.context
                )
                
                # Add context to new memory
                new_memory.metadata.context["supersedes"] = str(old_memory.memory_id)
                new_memory.metadata.context["previous_value"] = old_memory.content
                
                await storage.update_memory_context(
                    memory_id=new_memory.memory_id,
                    context=new_memory.metadata.context
                )
                
                logger.info(f"Resolved {conflict_type}: New memory supersedes old")
                resolution = "superseded"
            
            # Preference changes - keep both but mark evolution
            elif conflict_type == ConflictType.PREFERENCE_CHANGE:
                # Keep both memories but link them
                old_memory.metadata.context["evolved_to"] = str(new_memory.memory_id)
                new_memory.metadata.context["evolved_from"] = str(old_memory.memory_id)
                
                await storage.update_memory_context(old_memory.memory_id, old_memory.metadata.context)
                await storage.update_memory_context(new_memory.memory_id, new_memory.metadata.context)
                
                logger.info(f"Resolved {conflict_type}: Marked as preference evolution")
                resolution = "evolution"
            
            # Factual contradictions - flag for review
            elif conflict_type == ConflictType.FACTUAL_CONTRADICTION:
                # Mark both for potential review
                old_memory.metadata.context["potential_conflict"] = str(new_memory.memory_id)
                new_memory.metadata.context["potential_conflict"] = str(old_memory.memory_id)
                
                await storage.update_memory_context(old_memory.memory_id, old_memory.metadata.context)
                await storage.update_memory_context(new_memory.memory_id, new_memory.metadata.context)
                
                logger.warning(f"Factual contradiction detected: {old_memory.content} vs {new_memory.content}")
                resolution = "flagged_for_review"
            
            return resolution
        
        except Exception as e:
            logger.error(f"Failed to resolve conflict: {e}")
            return "resolution_failed"
    
    @classmethod
    async def detect_and_resolve(
        cls,
        new_memory: Memory,
        user_memories: List[Memory],
        storage
    ) -> Optional[str]:
        """Complete conflict detection and resolution pipeline.
        
        Args:
            new_memory: Newly created memory
            user_memories: User's existing memories
            storage: Storage instance
            
        Returns:
            Resolution strategy if conflict found, None otherwise
        """
        # Check for conflicts
        conflict = await cls.check_conflict(new_memory, user_memories)
        
        if conflict:
            old_memory, conflict_type = conflict
            logger.info(f"Conflict detected: {conflict_type}")
            logger.info(f"Old: {old_memory.content}")
            logger.info(f"New: {new_memory.content}")
            
            # Resolve the conflict
            resolution = await cls.resolve_conflict(
                new_memory,
                old_memory,
                conflict_type,
                storage
            )
            
            return resolution
        
        return None
