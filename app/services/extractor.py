"""Memory extraction service using LLM."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Optional

from app.config import get_settings
from app.llm_client import llm_client
from app.models.memory import MemoryCreate, MemoryType
from app.utils.metrics import metrics
from app.utils.temporal import parse_temporal_reference

logger = logging.getLogger(__name__)
settings = get_settings()


EXTRACTION_SYSTEM_PROMPT = """You are a memory extraction system. Your task is to analyze conversation turns and identify information worth remembering long-term.

Extract memories in these categories:
- PREFERENCE: User likes, dislikes, preferences
- FACT: Factual information about the user (name, location, job, etc.)
- COMMITMENT: Promises, reminders, tasks, deadlines
- INSTRUCTION: How the user wants to be addressed or assisted
- ENTITY: Important people, places, organizations mentioned

Only extract information that would be useful to recall in future conversations. Be concise and specific."""

EXTRACTION_USER_PROMPT = """Analyze this conversation turn and extract important memories.

Turn #{turn_number}:
User: {user_message}
Assistant: {assistant_message}

Return a JSON array of memories with this format:
[
    {{
        "type": "preference|fact|commitment|instruction|entity",
        "content": "brief, specific description of what to remember",
        "confidence": 0.0-1.0,
        "tags": ["relevant", "tags"],
        "entities": ["mentioned", "entities"]
    }}
]

Only include information actually worth remembering. If nothing significant, return empty array []."""


class MemoryExtractor:
    """Extract memories from conversation turns using LLM."""

    def __init__(self):
        """Initialize memory extractor."""
        # Use unified LLM client
        pass

    async def extract_from_turn(
        self,
        user_id: str,
        turn_number: int,
        user_message: str,
        assistant_message: str,
    ) -> list[MemoryCreate]:
        """Extract memories from a conversation turn.
        
        Args:
            user_id: User ID
            turn_number: Turn number in conversation
            user_message: User's message
            assistant_message: Assistant's response
            
        Returns:
            List of memory creation objects
        """
        import time
        start_time = time.time()

        try:
            # Prepare messages
            messages = [
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": EXTRACTION_USER_PROMPT.format(
                        turn_number=turn_number,
                        user_message=user_message,
                        assistant_message=assistant_message,
                    ),
                },
            ]

            # Call LLM in thread pool to avoid blocking
            logger.info(f"🤖 Calling LLM for memory extraction (turn {turn_number})...")
            response_data = await asyncio.to_thread(
                llm_client.extract_json,
                messages=messages
            )
            logger.info(f"📝 LLM response: {response_data}")

            duration = time.time() - start_time
            
            # Record metrics
            metrics.record_llm_call(
                model=settings.llm_provider,
                operation="memory_extraction",
                duration=duration,
                prompt_tokens=0,  # Not available in unified client
                completion_tokens=0,
            )

            # Parse memories from response
            # LLM returns either a list directly or a dict with 'memories' key
            if isinstance(response_data, list):
                memories_data = response_data
            else:
                memories_data = response_data.get('memories', [])
            
            logger.info(f"📊 Parsed {len(memories_data)} memory items from response")

            # Convert to MemoryCreate objects
            memories = []
            current_time = datetime.utcnow()
            
            for mem_data in memories_data:
                try:
                    # Normalize type to lowercase (LLM sometimes returns uppercase)
                    raw_type = mem_data.get('type', 'fact').lower()
                    memory_type = MemoryType(raw_type)
                    confidence = float(mem_data.get('confidence', 0.7))
                    
                    # Filter out low confidence memories
                    if confidence < settings.memory_confidence_threshold:
                        continue

                    # Parse temporal references and enhance content
                    original_content = mem_data['content']
                    enhanced_content, scheduled_date = parse_temporal_reference(
                        original_content,
                        reference_date=current_time
                    )
                    
                    # Add scheduled_date to context if extracted
                    context = {
                        'user_message': user_message[:200],
                        'assistant_message': assistant_message[:200],
                        'extraction_time': current_time.isoformat(),
                    }
                    
                    if scheduled_date:
                        context['scheduled_date'] = scheduled_date.isoformat()
                    
                    memory = MemoryCreate(
                        user_id=user_id,
                        type=memory_type,
                        content=enhanced_content,  # Use enhanced content with absolute dates
                        source_turn=turn_number,
                        confidence=confidence,
                        tags=mem_data.get('tags', []),
                        entities=mem_data.get('entities', []),
                        context=context,
                    )
                    memories.append(memory)

                except (KeyError, ValueError) as e:
                    logger.warning(f"Skipping invalid memory data: {e}")
                    continue

            logger.info(
                f"Extracted {len(memories)} memories from turn {turn_number} "
                f"for user {user_id} in {duration:.2f}s"
            )
            return memories

        except Exception as e:
            logger.error(f"Memory extraction failed: {e}")
            return []

    async def extract_entity(self, text: str) -> list[str]:
        """Extract named entities from text.
        
        Args:
            text: Text to extract entities from
            
        Returns:
            List of entity names
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Extract named entities (people, places, organizations) from text. Return as JSON array.",
                    },
                    {
                        "role": "user",
                        "content": f"Extract entities from: {text}\n\nReturn format: {{\"entities\": [\"entity1\", \"entity2\"]}}",
                    },
                ],
                temperature=0.1,
            )

            content = response.choices[0].message.content
            data = json.loads(content)
            return data.get('entities', [])

        except Exception as e:
            logger.warning(f"Entity extraction failed: {e}")
            return []

    async def classify_memory_type(self, content: str) -> MemoryType:
        """Classify the type of a memory.
        
        Args:
            content: Memory content
            
        Returns:
            Classified memory type
        """
        # Simple heuristic classification (could be enhanced with LLM)
        content_lower = content.lower()
        
        keywords = {
            MemoryType.PREFERENCE: ['like', 'prefer', 'love', 'hate', 'favorite', 'enjoy'],
            MemoryType.COMMITMENT: ['remind', 'tomorrow', 'schedule', 'meeting', 'call', 'task'],
            MemoryType.INSTRUCTION: ['call me', 'address me', 'refer to', 'always', 'never'],
            MemoryType.ENTITY: ['works at', 'lives in', 'friend', 'family', 'organization'],
        }
        
        for mem_type, words in keywords.items():
            if any(word in content_lower for word in words):
                return mem_type
        
        return MemoryType.FACT  # Default

    async def consolidate_memories(
        self,
        memories: list[str],
    ) -> Optional[str]:
        """Consolidate multiple related memories into one.
        
        Args:
            memories: List of memory contents to consolidate
            
        Returns:
            Consolidated memory content or None
        """
        if len(memories) < 2:
            return None

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Consolidate multiple related memories into a single, comprehensive memory. "
                        "Preserve all important information while removing redundancy.",
                    },
                    {
                        "role": "user",
                        "content": f"Consolidate these memories:\n" + "\n".join(f"{i+1}. {m}" for i, m in enumerate(memories)),
                    },
                ],
                temperature=0.1,
            )

            consolidated = response.choices[0].message.content.strip()
            logger.info(f"Consolidated {len(memories)} memories")
            return consolidated

        except Exception as e:
            logger.error(f"Memory consolidation failed: {e}")
            return None

    async def resolve_conflict(
        self,
        memory1: str,
        memory2: str,
    ) -> Optional[str]:
        """Resolve conflicting memories.
        
        Args:
            memory1: First memory content
            memory2: Second memory content
            
        Returns:
            Resolved memory or None
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": "Two memories conflict. Determine which is more likely correct "
                        "or if they can be reconciled. Return the best memory.",
                    },
                    {
                        "role": "user",
                        "content": f"Memory 1: {memory1}\nMemory 2: {memory2}\n\n"
                        "Which is correct or how can they be reconciled?",
                    },
                ],
                temperature=0.1,
            )

            resolved = response.choices[0].message.content.strip()
            logger.info("Resolved memory conflict")
            return resolved

        except Exception as e:
            logger.error(f"Conflict resolution failed: {e}")
            return None
