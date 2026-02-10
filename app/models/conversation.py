"""Data models for conversations."""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class ConversationTurn(BaseModel):
    """Single turn in a conversation."""

    turn_id: UUID = Field(default_factory=uuid4)
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

    turn_number: int = Field(..., ge=0)
    message: str = Field(..., min_length=1, max_length=10000)
    metadata: dict[str, Any] = Field(default_factory=dict)
    include_memories: bool = Field(default=True)
    stream: bool = Field(default=False)


class ConversationResponse(BaseModel):
    """Response from conversation processing."""

    turn_id: UUID
    user_id: str
    turn_number: int
    response: str
    memories_used: list[UUID]
    memories_extracted: int
    processing_time_ms: float
    metadata: dict[str, Any] = Field(default_factory=dict)

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
