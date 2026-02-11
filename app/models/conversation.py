"""Data models for conversations."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """Single turn in a conversation."""

    turn_id: UUID = Field(default_factory=uuid4)
    conversation_id: Optional[UUID] = None
    user_id: str = Field(..., min_length=1, max_length=255)
    turn_number: int = Field(..., ge=0)
    user_message: str = Field(..., min_length=1)
    assistant_message: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    metadata: dict[str, Any] = Field(default_factory=dict)
    memories_retrieved: list[UUID] = Field(default_factory=list)
    memories_created: list[UUID] = Field(default_factory=list)


class ConversationRequest(BaseModel):
    """Request to process a conversation turn."""

    conversation_id: Optional[UUID] = None  # None = new conversation
    turn_number: int = Field(..., ge=0)
    message: str = Field(..., min_length=1, max_length=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    include_memories: bool = Field(default=True)
    stream: bool = Field(default=False)


class ActiveMemory(BaseModel):
    """Memory that was used in a conversation turn (for response)."""
    
    memory_id: str
    content: str
    type: str
    origin_turn: int  # source_turn
    last_used_turn: Optional[int] = None
    confidence: float
    relevance_score: Optional[float] = None
    
    class Config:
        """Pydantic config."""
        json_encoders = {
            UUID: str,
            datetime: lambda v: v.isoformat(),
        }


class ConversationResponse(BaseModel):
    """Response from conversation processing."""

    turn_id: UUID
    conversation_id: UUID
    user_id: str
    turn_number: int
    response: str
    active_memories: list[ActiveMemory] = Field(default_factory=list)  # NEW: Required by problem statement
    memories_used: list[UUID]  # Keep for backward compatibility
    memories_extracted: int
    processing_time_ms: float
    retrieval_time_ms: Optional[float] = None  # NEW: Latency breakdown
    injection_time_ms: Optional[float] = None  # NEW: Latency breakdown
    metadata: dict[str, Any] = Field(default_factory=dict)
    response_generated: bool = True  # NEW: Required by problem statement

    class Config:
        """Pydantic config."""
        json_encoders = {
            UUID: str,
            datetime: lambda v: v.isoformat(),
        }


class ConversationContext(BaseModel):
    """Context for LLM including relevant memories."""

    user_id: str
    turn_number: int
    current_message: str
    relevant_memories: list[str] = Field(default_factory=list)
    system_instructions: str = ""
    max_tokens: int = 4000

    def format_for_llm(self) -> str:
        """Format context into prompt for LLM."""
        parts = []
        
        if self.system_instructions:
            parts.append(self.system_instructions)
        
        if self.relevant_memories:
            parts.append("\n--- RELEVANT MEMORIES ---")
            for i, memory in enumerate(self.relevant_memories, 1):
                parts.append(f"{i}. {memory}")
            parts.append("--- END MEMORIES ---\n")
        
        parts.append(f"User: {self.current_message}")
        
        return "\n".join(parts)


class ConversationHistory(BaseModel):
    """Full conversation history for a user."""

    user_id: str
    turns: list[ConversationTurn]
    total_turns: int
    first_turn_timestamp: datetime
    last_turn_timestamp: datetime
    total_memories_created: int


# New conversation management models

class Conversation(BaseModel):
    """Conversation metadata."""

    conversation_id: UUID = Field(default_factory=uuid4)
    user_id: str = Field(..., min_length=1, max_length=255)
    title: Optional[str] = Field(None, max_length=500)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    is_archived: bool = Field(default=False)
    turn_count: int = Field(default=0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    class Config:
        """Pydantic config."""
        json_encoders = {
            UUID: str,
            datetime: lambda v: v.isoformat(),
        }


class ConversationSummary(BaseModel):
    """Summary of a conversation for list view."""

    conversation_id: UUID
    user_id: str
    title: Optional[str]
    created_at: datetime
    updated_at: datetime
    is_archived: bool
    turn_count: int
    last_message_preview: Optional[str] = None

    class Config:
        """Pydantic config."""
        json_encoders = {
            UUID: str,
            datetime: lambda v: v.isoformat(),
        }


class ConversationListResponse(BaseModel):
    """Response for conversation list."""

    conversations: list[ConversationSummary]
    total_count: int
    archived_count: int


class ConversationCreateRequest(BaseModel):
    """Request to create a new conversation."""

    title: Optional[str] = Field(None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ConversationUpdateRequest(BaseModel):
    """Request to update conversation metadata."""

    title: Optional[str] = Field(None, max_length=500)
    is_archived: Optional[bool] = None
    metadata: Optional[dict[str, Any]] = None


class ConversationExport(BaseModel):
    """Exportable conversation format."""

    conversation: Conversation
    turns: list[ConversationTurn]
    export_date: datetime = Field(default_factory=datetime.utcnow)

    class Config:
        """Pydantic config."""
        json_encoders = {
            UUID: str,
            datetime: lambda v: v.isoformat(),
        }
