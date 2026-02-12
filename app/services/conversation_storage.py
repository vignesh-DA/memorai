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
        conversation_id: UUID,
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
            conversation_id: Conversation identifier
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
                    turn_id, conversation_id, user_id, turn_number,
                    user_message, assistant_message,
                    timestamp, metadata,
                    memories_retrieved, memories_created
                ) VALUES (
                    :turn_id, :conversation_id, :user_id, :turn_number,
                    :user_message, :assistant_message,
                    :timestamp, :metadata,
                    :memories_retrieved, :memories_created
                )
            """)

            await self.session.execute(
                query,
                {
                    "turn_id": str(turn_id),
                    "conversation_id": str(conversation_id),
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
                f"Stored conversation turn {turn_number} for conversation {conversation_id}"
            )

            return ConversationTurn(
                turn_id=turn_id,
                conversation_id=conversation_id,
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
        user_id: Optional[str] = None,
        conversation_id: Optional[UUID] = None,
        limit: int = 10,
        before_turn: Optional[int] = None,
    ) -> list[ConversationTurn]:
        """Get recent conversation turns for a user or specific conversation.
        
        Args:
            user_id: User identifier (optional if conversation_id provided)
            conversation_id: Conversation identifier (optional, takes priority)
            limit: Maximum number of turns to retrieve
            before_turn: Only get turns before this number
            
        Returns:
            List of conversation turns
        """
        try:
            # Build base conditions
            conditions = []
            params = {"limit": limit}
            
            if conversation_id:
                conditions.append("conversation_id = :conversation_id")
                params["conversation_id"] = conversation_id
            elif user_id:
                conditions.append("user_id = :user_id")
                params["user_id"] = user_id
            else:
                raise ValueError("Either user_id or conversation_id must be provided")
            
            if before_turn is not None:
                conditions.append("turn_number < :before_turn")
                params["before_turn"] = before_turn
            
            where_clause = " AND ".join(conditions)
            
            query = text(f"""
                SELECT * FROM conversation_turns
                WHERE {where_clause}
                ORDER BY turn_number DESC
                LIMIT :limit
            """)

            result = await self.session.execute(query, params)
            rows = result.fetchall()

            turns = []
            for row in rows:
                # 🔧 FIX: Handle metadata - may already be dict from Postgres
                metadata = row[7] if row[7] else {}
                if isinstance(metadata, str):
                    metadata = json.loads(metadata)
                
                turns.append(
                    ConversationTurn(
                        turn_id=row[0],  # turn_id UUID
                        conversation_id=row[1],  # conversation_id UUID (was missing!)
                        user_id=row[2],  # user_id VARCHAR
                        turn_number=row[3],  # turn_number INTEGER
                        user_message=row[4],  # user_message TEXT
                        assistant_message=row[5] or "",  # assistant_message TEXT
                        timestamp=row[6],  # timestamp TIMESTAMP
                        metadata=metadata,  # metadata JSONB
                        memories_retrieved=row[8] or [],  # memories_retrieved UUID[]
                        memories_created=row[9] or [],  # memories_created UUID[]
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
