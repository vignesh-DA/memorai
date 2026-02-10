"""Service for storing and retrieving full conversation history."""

import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import ConversationTurn

logger = logging.getLogger(__name__)


class ConversationStorage:
    """Handle storage and retrieval of full conversation turns."""

    def __init__(self, session: AsyncSession):
        """Initialize conversation storage.
        
        Args:
            session: Database session
        """
        self.session = session

    async def store_turn(
        self,
        user_id: str,
        turn_number: int,
        user_message: str,
        assistant_message: str,
        memories_retrieved: list[UUID] = None,
        memories_created: list[UUID] = None,
        metadata: dict = None,
    ) -> ConversationTurn:
        """Store a complete conversation turn.
        
        Args:
            user_id: User identifier
            turn_number: Turn number in conversation
            user_message: User's message
            assistant_message: Assistant's response
            memories_retrieved: UUIDs of memories used for context
            memories_created: UUIDs of newly extracted memories
            metadata: Additional metadata
            
        Returns:
            Stored conversation turn
        """
        try:
            turn_id = uuid4()
            timestamp = datetime.utcnow()

            # Convert UUID lists to PostgreSQL array format
            memories_retrieved_array = (
                [str(m) for m in memories_retrieved] if memories_retrieved else []
            )
            memories_created_array = (
                [str(m) for m in memories_created] if memories_created else []
            )

            query = text("""
                INSERT INTO conversation_turns (
                    turn_id, user_id, turn_number,
                    user_message, assistant_message,
                    timestamp, metadata,
                    memories_retrieved, memories_created
                ) VALUES (
                    :turn_id, :user_id, :turn_number,
                    :user_message, :assistant_message,
                    :timestamp, :metadata,
                    :memories_retrieved, :memories_created
                )
            """)

            await self.session.execute(
                query,
                {
                    "turn_id": str(turn_id),
                    "user_id": user_id,
                    "turn_number": turn_number,
                    "user_message": user_message,
                    "assistant_message": assistant_message,
                    "timestamp": timestamp,
                    "metadata": json.dumps(metadata or {}),
                    "memories_retrieved": memories_retrieved_array,
                    "memories_created": memories_created_array,
                },
            )

            await self.session.commit()

            logger.info(
                f"Stored conversation turn {turn_number} for user {user_id}"
            )

            return ConversationTurn(
                turn_id=turn_id,
                user_id=user_id,
                turn_number=turn_number,
                user_message=user_message,
                assistant_message=assistant_message,
                timestamp=timestamp,
                metadata=metadata or {},
                memories_retrieved=memories_retrieved or [],
                memories_created=memories_created or [],
            )

        except Exception as e:
            logger.error(f"Failed to store conversation turn: {e}")
            raise

    async def get_recent_turns(
        self,
        user_id: str,
        limit: int = 10,
        before_turn: Optional[int] = None,
    ) -> list[ConversationTurn]:
        """Get recent conversation turns for a user.
        
        Args:
            user_id: User identifier
            limit: Maximum number of turns to retrieve
            before_turn: Only get turns before this number
            
        Returns:
            List of conversation turns
        """
        try:
            if before_turn is not None:
                query = text("""
                    SELECT * FROM conversation_turns
                    WHERE user_id = :user_id
                    AND turn_number < :before_turn
                    ORDER BY turn_number DESC
                    LIMIT :limit
                """)
                params = {
                    "user_id": user_id,
                    "before_turn": before_turn,
                    "limit": limit,
                }
            else:
                query = text("""
                    SELECT * FROM conversation_turns
                    WHERE user_id = :user_id
                    ORDER BY turn_number DESC
                    LIMIT :limit
                """)
                params = {"user_id": user_id, "limit": limit}

            result = await self.session.execute(query, params)
            rows = result.fetchall()

            turns = []
            for row in rows:
                turns.append(
                    ConversationTurn(
                        turn_id=row[0],  # asyncpg returns UUID objects already
                        user_id=row[1],
                        turn_number=row[2],
                        user_message=row[3],
                        assistant_message=row[4] or "",
                        timestamp=row[5],
                        metadata=json.loads(row[6]) if row[6] else {},
                        memories_retrieved=row[7] or [],  # Already UUID array
                        memories_created=row[8] or [],  # Already UUID array
                    )
                )

            # Reverse to get chronological order
            turns.reverse()

            logger.info(f"Retrieved {len(turns)} turns for user {user_id}")
            return turns

        except Exception as e:
            logger.error(f"Failed to retrieve conversation turns: {e}")
            raise

    async def get_conversation_window(
        self,
        user_id: str,
        current_turn: int,
        window_size: int = 5,
    ) -> str:
        """Get formatted conversation context window.
        
        Args:
            user_id: User identifier
            current_turn: Current turn number
            window_size: Number of previous turns to include
            
        Returns:
            Formatted conversation history string
        """
        turns = await self.get_recent_turns(
            user_id=user_id,
            limit=window_size,
            before_turn=current_turn,
        )

        if not turns:
            return ""

        lines = ["--- RECENT CONVERSATION ---"]
        for turn in turns:
            lines.append(f"\nTurn {turn.turn_number}:")
            lines.append(f"User: {turn.user_message}")
            if turn.assistant_message:
                lines.append(f"Assistant: {turn.assistant_message}")
        lines.append("--- END CONVERSATION ---\n")

        return "\n".join(lines)
