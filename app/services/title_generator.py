"""Service for automatically generating conversation titles."""

import logging
from typing import Optional

from app.llm_client import get_llm_client

logger = logging.getLogger(__name__)


class TitleGenerator:
    """Generate conversation titles from first message."""

    @staticmethod
    def generate_title(first_message: str) -> str:
        """Generate a concise title from the first message.
        
        Args:
            first_message: The first user message in the conversation
            
        Returns:
            Generated title (max 50 chars)
        """
        try:
            # Keep it simple - just use the first message or generate a short title
            if len(first_message) <= 50:
                return first_message
            
            # Use LLM to generate a concise title
            messages = [
                {
                    "role": "system",
                    "content": """Generate a very short, concise title (max 6 words) for this conversation.
The title should capture the main topic or intent.
Return ONLY the title, no quotes, no explanations."""
                },
                {
                    "role": "user", 
                    "content": f"First message: {first_message}"
                }
            ]
            
            title = get_llm_client().chat_completion(
                messages=messages,
                temperature=0.3,
                max_tokens=20,
            )
            
            # Clean up the title
            title = title.strip().strip('"').strip("'")
            
            # Truncate if too long
            if len(title) > 50:
                title = title[:47] + "..."
            
            return title or first_message[:50]
            
        except Exception as e:
            logger.error(f"Failed to generate title: {e}")
            # Fallback to truncated first message
            return first_message[:50] + ("..." if len(first_message) > 50 else "")


title_generator = TitleGenerator()
