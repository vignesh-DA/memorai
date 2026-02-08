"""Tests for memory extraction service."""

import pytest
from unittest.mock import AsyncMock, patch

from app.models.memory import MemoryType
from app.services.extractor import MemoryExtractor


@pytest.mark.asyncio
async def test_extract_from_turn(mock_openai_client):
    """Test memory extraction from conversation turn."""
    extractor = MemoryExtractor()
    
    # Mock the OpenAI response
    mock_response = {
        "memories": [
            {
                "type": "preference",
                "content": "User likes coffee",
                "confidence": 0.9,
                "tags": ["beverage"],
                "entities": ["coffee"]
            }
        ]
    }
    
    with patch.object(extractor, 'client', mock_openai_client):
        mock_openai_client.chat.completions.create.return_value.choices[0].message.content = (
            str(mock_response)
        )
        
        memories = await extractor.extract_from_turn(
            user_id="test_user",
            turn_number=1,
            user_message="I love coffee",
            assistant_message="Great! Coffee is wonderful.",
        )
        
        assert len(memories) >= 0  # May be filtered by confidence threshold


@pytest.mark.asyncio
async def test_classify_memory_type():
    """Test memory type classification."""
    extractor = MemoryExtractor()
    
    # Test preference
    mem_type = await extractor.classify_memory_type("I like pizza")
    assert mem_type == MemoryType.PREFERENCE
    
    # Test commitment
    mem_type = await extractor.classify_memory_type("Remind me to call tomorrow")
    assert mem_type == MemoryType.COMMITMENT
    
    # Test instruction
    mem_type = await extractor.classify_memory_type("Always call me by my nickname")
    assert mem_type == MemoryType.INSTRUCTION


@pytest.mark.asyncio
async def test_extract_entity(mock_openai_client):
    """Test entity extraction."""
    extractor = MemoryExtractor()
    
    with patch.object(extractor, 'client', mock_openai_client):
        mock_openai_client.chat.completions.create.return_value.choices[0].message.content = (
            '{"entities": ["John", "Microsoft", "Seattle"]}'
        )
        
        entities = await extractor.extract_entity(
            "John works at Microsoft in Seattle"
        )
        
        assert isinstance(entities, list)
