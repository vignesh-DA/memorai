"""Data models for memory system."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator

from app.config import settings


class MemoryType(str, Enum):
    """Types of memories that can be stored."""

    PREFERENCE = "preference"  # User preferences (likes/dislikes)
    FACT = "fact"  # Factual information about user
    COMMITMENT = "commitment"  # Promises, tasks, reminders
    INSTRUCTION = "instruction"  # How to interact with user
    ENTITY = "entity"  # People, places, things mentioned


class MemoryMetadata(BaseModel):
    """Metadata associated with a memory."""

    source_turn: int = Field(..., description="Conversation turn number where memory was created")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    last_accessed: datetime = Field(default_factory=datetime.utcnow)
    access_count: int = Field(default=0, ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score of memory accuracy")
    decay_score: float = Field(default=1.0, ge=0.0, le=1.0, description="Temporal decay factor")
    importance_score: float = Field(default=0.7, ge=0.0, le=1.0, description="Importance weight (0-1)")
    importance_level: str = Field(default="medium", description="Importance level (critical/high/medium/low)")
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence", "decay_score", "importance_score")
    @classmethod
    def validate_score_range(cls, v: float) -> float:
        """Ensure scores are between 0 and 1."""
        if not 0 <= v <= 1:
            raise ValueError("Score must be between 0 and 1")
        return v


class Memory(BaseModel):
    """Core memory model."""

    memory_id: UUID = Field(default_factory=uuid4)
    user_id: str = Field(..., min_length=1, max_length=255)
    type: MemoryType
    content: str = Field(..., min_length=1, max_length=5000)
    embedding: Optional[list[float]] = Field(None, description="Vector embedding of content")
    metadata: MemoryMetadata

    @field_validator("embedding")
    @classmethod
    def validate_embedding_dimension(cls, v: Optional[list[float]]) -> Optional[list[float]]:
        """Validate embedding dimension matches expected size."""
        expected_dim = settings.memory_embedding_dimension
        if v is not None and len(v) != expected_dim:
            raise ValueError(f"Embedding must be {expected_dim} dimensions, got {len(v)}")
        return v

    def update_access(self) -> None:
        """Update access metadata when memory is retrieved."""
        self.metadata.last_accessed = datetime.utcnow()
        self.metadata.access_count += 1

    def calculate_decay(self, current_turn: int) -> float:
        """Calculate temporal decay based on age and access patterns.
        
        Args:
            current_turn: Current conversation turn number
            
        Returns:
            Decay score between 0 and 1
        """
        age = current_turn - self.metadata.source_turn
        access_boost = min(self.metadata.access_count * 0.1, 0.5)
        
        # Exponential decay with access pattern boost
        base_decay = 0.95 ** (age / 100)
        self.metadata.decay_score = min(base_decay + access_boost, 1.0)
        
        return self.metadata.decay_score


class MemoryCreate(BaseModel):
    """Schema for creating a new memory."""

    user_id: str = Field(..., min_length=1, max_length=255)
    type: MemoryType
    content: str = Field(..., min_length=1, max_length=5000)
    source_turn: int = Field(..., ge=0)
    confidence: float = Field(..., ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)


class MemoryUpdate(BaseModel):
    """Schema for updating an existing memory."""

    content: Optional[str] = Field(None, min_length=1, max_length=5000)
    confidence: Optional[float] = Field(None, ge=0.0, le=1.0)
    tags: Optional[list[str]] = None
    entities: Optional[list[str]] = None


class MemorySearchQuery(BaseModel):
    """Schema for searching memories."""

    user_id: str = Field(..., min_length=1, max_length=255)
    query: str = Field(..., min_length=1, max_length=500)
    memory_types: Optional[list[MemoryType]] = None
    top_k: int = Field(default=10, ge=1, le=50)
    min_confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    current_turn: Optional[int] = Field(None, ge=0)


class MemorySearchResult(BaseModel):
    """Result from memory search with relevance score."""

    memory: Memory
    relevance_score: float = Field(..., ge=0.0, le=1.0)
    similarity_score: float = Field(..., ge=-1.0, le=1.0)  # Cosine similarity ranges from -1 to 1
    recency_score: float = Field(..., ge=0.0, le=1.0)
    access_score: float = Field(..., ge=0.0, le=1.0)

    class Config:
        """Pydantic config."""
        json_encoders = {
            UUID: str,
            datetime: lambda v: v.isoformat(),
        }


class MemoryConsolidation(BaseModel):
    """Schema for memory consolidation result."""

    original_memories: list[UUID]
    consolidated_memory: Memory
    consolidation_reason: str


class MemoryStats(BaseModel):
    """Statistics about user's memory store."""

    user_id: str
    total_memories: int
    memories_by_type: dict[str, int]  # Changed from MemoryType to str for JSON serialization
    avg_confidence: float
    oldest_memory_turn: int
    newest_memory_turn: int
    total_access_count: int
    hot_memories: int  # Memories accessed frequently
