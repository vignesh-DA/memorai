"""Service for managing conversations (create, list, update, archive, delete)."""

import json
import logging
from datetime import datetime
from typing import Optional
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.conversation import (
    Conversation,
    ConversationExport,
    ConversationSummary,
    ConversationTurn,
)

logger = logging.getLogger(__name__)


class ConversationManager:
    """Manage conversations for users."""

    def __init__(self, session: AsyncSession):
        """Initialize conversation manager.
        
        Args:
            session: Database session
        """
        self.session = session

    async def create_conversation(
        self,
        user_id: str,
        title: Optional[str] = None,
        metadata: dict = None,
    ) -> Conversation:
        """Create a new conversation.
        
        Args:
            user_id: User identifier
            title: Optional conversation title
            metadata: Optional metadata
            
        Returns:
            Created conversation
        """
        try:
            conversation_id = uuid4()
            now = datetime.utcnow()

            query = text("""
                INSERT INTO conversations (
                    conversation_id, user_id, title,
                    created_at, updated_at, is_archived,
                    turn_count, metadata
                ) VALUES (
                    :conversation_id, :user_id, :title,
                    :created_at, :updated_at, :is_archived,
                    :turn_count, :metadata
                )
            """)

            await self.session.execute(
                query,
                {
                    "conversation_id": str(conversation_id),
                    "user_id": user_id,
                    "title": title or "New Conversation",
                    "created_at": now,
                    "updated_at": now,
                    "is_archived": False,
                    "turn_count": 0,
                    "metadata": json.dumps(metadata or {}),
                },
            )

            await self.session.commit()

            logger.info(f"Created conversation {conversation_id} for user {user_id}")

            return Conversation(
                conversation_id=conversation_id,
                user_id=user_id,
                title=title or "New Conversation",
                created_at=now,
                updated_at=now,
                is_archived=False,
                turn_count=0,
                metadata=metadata or {},
            )

        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error creating conversation: {e}")
            raise

    async def get_conversation(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> Optional[Conversation]:
        """Get a conversation by ID.
        
        Args:
            conversation_id: Conversation identifier
            user_id: User identifier (for authorization)
            
        Returns:
            Conversation if found and owned by user, None otherwise
        """
        try:
            query = text("""
                SELECT conversation_id, user_id, title, created_at, updated_at,
                       is_archived, turn_count, metadata
                FROM conversations
                WHERE conversation_id = :conversation_id AND user_id = :user_id
            """)

            result = await self.session.execute(
                query,
                {"conversation_id": str(conversation_id), "user_id": user_id},
            )

            row = result.fetchone()
            if not row:
                return None

            return Conversation(
                conversation_id=str(row[0]),  # Convert asyncpg UUID to string
                user_id=row[1],
                title=row[2],
                created_at=row[3],
                updated_at=row[4],
                is_archived=row[5],
                turn_count=row[6],
                metadata=row[7] if isinstance(row[7], dict) else {},
            )

        except Exception as e:
            logger.error(f"Error getting conversation {conversation_id}: {e}")
            raise

    async def list_conversations(
        self,
        user_id: str,
        include_archived: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> list[ConversationSummary]:
        """List conversations for a user.
        
        Args:
            user_id: User identifier
            include_archived: Include archived conversations
            limit: Maximum number of conversations to return
            offset: Offset for pagination
            
        Returns:
            List of conversation summaries
        """
        try:
            # Build query based on archived filter
            archived_filter = "" if include_archived else "AND c.is_archived = FALSE"

            query = text(f"""
                SELECT 
                    c.conversation_id, c.user_id, c.title, 
                    c.created_at, c.updated_at, c.is_archived, c.turn_count,
                    ct.user_message as last_message
                FROM conversations c
                LEFT JOIN LATERAL (
                    SELECT user_message
                    FROM conversation_turns
                    WHERE conversation_id = c.conversation_id
                    ORDER BY turn_number DESC
                    LIMIT 1
                ) ct ON TRUE
                WHERE c.user_id = :user_id {archived_filter}
                ORDER BY c.updated_at DESC
                LIMIT :limit OFFSET :offset
            """)

            result = await self.session.execute(
                query,
                {
                    "user_id": user_id,
                    "limit": limit,
                    "offset": offset,
                },
            )

            conversations = []
            for row in result.fetchall():
                # Generate preview from last message
                last_message = row[7] if row[7] else None
                preview = None
                if last_message:
                    preview = last_message[:100] + "..." if len(last_message) > 100 else last_message

                conversations.append(
                    ConversationSummary(
                        conversation_id=str(row[0]),  # Convert asyncpg UUID to string
                        user_id=row[1],
                        title=row[2],
                        created_at=row[3],
                        updated_at=row[4],
                        is_archived=row[5],
                        turn_count=row[6],
                        last_message_preview=preview,
                    )
                )

            return conversations

        except Exception as e:
            logger.error(f"Error listing conversations for user {user_id}: {e}")
            raise

    async def update_conversation(
        self,
        conversation_id: UUID,
        user_id: str,
        title: Optional[str] = None,
        is_archived: Optional[bool] = None,
        metadata: Optional[dict] = None,
    ) -> Optional[Conversation]:
        """Update conversation metadata.
        
        Args:
            conversation_id: Conversation identifier
            user_id: User identifier (for authorization)
            title: New title (optional)
            is_archived: New archived status (optional)
            metadata: New metadata (optional)
            
        Returns:
            Updated conversation if found and owned by user
        """
        try:
            # Build update query dynamically
            updates = ["updated_at = :updated_at"]
            params = {
                "conversation_id": str(conversation_id),
                "user_id": user_id,
                "updated_at": datetime.utcnow(),
            }

            if title is not None:
                updates.append("title = :title")
                params["title"] = title

            if is_archived is not None:
                updates.append("is_archived = :is_archived")
                params["is_archived"] = is_archived

            if metadata is not None:
                updates.append("metadata = :metadata")
                params["metadata"] = json.dumps(metadata)

            query = text(f"""
                UPDATE conversations
                SET {', '.join(updates)}
                WHERE conversation_id = :conversation_id AND user_id = :user_id
                RETURNING conversation_id, user_id, title, created_at, updated_at,
                          is_archived, turn_count, metadata
            """)

            result = await self.session.execute(query, params)
            await self.session.commit()

            row = result.fetchone()
            if not row:
                return None

            return Conversation(
                conversation_id=str(row[0]),  # Convert asyncpg UUID to string
                user_id=row[1],
                title=row[2],
                created_at=row[3],
                updated_at=row[4],
                is_archived=row[5],
                turn_count=row[6],
                metadata=row[7] if isinstance(row[7], dict) else {},
            )

        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error updating conversation {conversation_id}: {e}")
            raise

    async def delete_conversation(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> bool:
        """Delete a conversation and all its turns.
        
        Args:
            conversation_id: Conversation identifier
            user_id: User identifier (for authorization)
            
        Returns:
            True if deleted, False if not found
        """
        try:
            query = text("""
                DELETE FROM conversations
                WHERE conversation_id = :conversation_id AND user_id = :user_id
            """)

            result = await self.session.execute(
                query,
                {"conversation_id": str(conversation_id), "user_id": user_id},
            )

            await self.session.commit()

            deleted = result.rowcount > 0
            if deleted:
                logger.info(f"Deleted conversation {conversation_id} for user {user_id}")

            return deleted

        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error deleting conversation {conversation_id}: {e}")
            raise

    async def search_conversations(
        self,
        user_id: str,
        query: str,
        limit: int = 20,
    ) -> list[ConversationSummary]:
        """Search conversations by title or content.
        
        Args:
            user_id: User identifier
            query: Search query
            limit: Maximum results
            
        Returns:
            Matching conversations
        """
        try:
            search_query = text("""
                SELECT DISTINCT
                    c.conversation_id, c.user_id, c.title,
                    c.created_at, c.updated_at, c.is_archived, c.turn_count,
                    NULL as last_message
                FROM conversations c
                LEFT JOIN conversation_turns ct ON c.conversation_id = ct.conversation_id
                WHERE c.user_id = :user_id
                AND (
                    c.title ILIKE :search_pattern
                    OR ct.user_message ILIKE :search_pattern
                    OR ct.assistant_message ILIKE :search_pattern
                )
                ORDER BY c.updated_at DESC
                LIMIT :limit
            """)

            result = await self.session.execute(
                search_query,
                {
                    "user_id": user_id,
                    "search_pattern": f"%{query}%",
                    "limit": limit,
                },
            )

            conversations = []
            for row in result.fetchall():
                conversations.append(
                    ConversationSummary(
                        conversation_id=str(row[0]),  # Convert asyncpg UUID to string
                        user_id=row[1],
                        title=row[2],
                        created_at=row[3],
                        updated_at=row[4],
                        is_archived=row[5],
                        turn_count=row[6],
                        last_message_preview=None,
                    )
                )

            return conversations

        except Exception as e:
            logger.error(f"Error searching conversations: {e}")
            raise

    async def export_conversation(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> Optional[ConversationExport]:
        """Export a full conversation with all turns.
        
        Args:
            conversation_id: Conversation identifier
            user_id: User identifier (for authorization)
            
        Returns:
            Conversation export if found
        """
        try:
            # Get conversation
            conversation = await self.get_conversation(conversation_id, user_id)
            if not conversation:
                return None

            # Get all turns
            turns_query = text("""
                SELECT turn_id, conversation_id, user_id, turn_number,
                       user_message, assistant_message, timestamp, metadata,
                       memories_retrieved, memories_created
                FROM conversation_turns
                WHERE conversation_id = :conversation_id AND user_id = :user_id
                ORDER BY turn_number ASC
            """)

            result = await self.session.execute(
                turns_query,
                {"conversation_id": str(conversation_id), "user_id": user_id},
            )

            turns = []
            for row in result.fetchall():
                # Convert UUID arrays from strings to UUID objects
                memories_retrieved = []
                if row[8]:
                    memories_retrieved = [str(m) for m in row[8]]
                
                memories_created = []
                if row[9]:
                    memories_created = [str(m) for m in row[9]]
                
                turns.append(
                    ConversationTurn(
                        turn_id=str(row[0]),  # Convert asyncpg UUID to string
                        conversation_id=str(row[1]),  # Convert asyncpg UUID to string
                        user_id=row[2],
                        turn_number=row[3],
                        user_message=row[4],
                        assistant_message=row[5],
                        timestamp=row[6],
                        metadata=row[7] if isinstance(row[7], dict) else {},
                        memories_retrieved=memories_retrieved,
                        memories_created=memories_created,
                    )
                )

            return ConversationExport(
                conversation=conversation,
                turns=turns,
            )

        except Exception as e:
            logger.error(f"Error exporting conversation {conversation_id}: {e}")
            raise

    async def increment_turn_count(
        self,
        conversation_id: UUID,
        user_id: str,
    ) -> None:
        """Increment turn count and update timestamp for a conversation.
        
        Args:
            conversation_id: Conversation identifier
            user_id: User identifier
        """
        try:
            query = text("""
                UPDATE conversations
                SET turn_count = turn_count + 1,
                    updated_at = :updated_at
                WHERE conversation_id = :conversation_id AND user_id = :user_id
            """)

            await self.session.execute(
                query,
                {
                    "conversation_id": str(conversation_id),
                    "user_id": user_id,
                    "updated_at": datetime.utcnow(),
                },
            )

            await self.session.commit()

        except Exception as e:
            await self.session.rollback()
            logger.error(f"Error incrementing turn count: {e}")
            raise

    async def get_conversation_count(
        self,
        user_id: str,
        archived_only: bool = False,
    ) -> int:
        """Get total conversation count for a user.
        
        Args:
            user_id: User identifier
            archived_only: Count only archived conversations
            
        Returns:
            Total count
        """
        try:
            archived_filter = "AND is_archived = TRUE" if archived_only else ""

            query = text(f"""
                SELECT COUNT(*)
                FROM conversations
                WHERE user_id = :user_id {archived_filter}
            """)

            result = await self.session.execute(query, {"user_id": user_id})
            count = result.scalar()

            return count or 0

        except Exception as e:
            logger.error(f"Error getting conversation count: {e}")
            raise
