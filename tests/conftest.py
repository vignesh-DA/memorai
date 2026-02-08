"""Test configuration and fixtures."""

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.config import get_settings


@pytest.fixture
def settings():
    """Get test settings."""
    return get_settings()


@pytest.fixture
def mock_redis():
    """Mock Redis client."""
    mock = AsyncMock()
    mock.get.return_value = None
    mock.setex.return_value = True
    mock.delete.return_value = True
    mock.ping.return_value = True
    return mock


@pytest.fixture
def mock_pinecone_index():
    """Mock Pinecone index."""
    mock = MagicMock()
    mock.query.return_value = MagicMock(matches=[])
    mock.upsert.return_value = None
    mock.delete.return_value = None
    mock.describe_index_stats.return_value = {"total_vector_count": 0}
    return mock


@pytest.fixture
def mock_openai_client():
    """Mock OpenAI client."""
    mock = AsyncMock()
    
    # Mock embeddings
    mock.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 1536)]
    )
    
    # Mock chat completions
    mock.chat.completions.create.return_value = MagicMock(
        choices=[MagicMock(message=MagicMock(content="Test response"))],
        usage=MagicMock(prompt_tokens=10, completion_tokens=20)
    )
    
    return mock


@pytest.fixture
def sample_memory_data():
    """Sample memory data for testing."""
    return {
        "user_id": "test_user_123",
        "type": "preference",
        "content": "User prefers dark mode interface",
        "source_turn": 1,
        "confidence": 0.9,
        "tags": ["ui", "preference"],
        "entities": [],
    }


@pytest.fixture
def sample_conversation_request():
    """Sample conversation request."""
    return {
        "user_id": "test_user_123",
        "turn_number": 5,
        "message": "What's my preferred theme?",
        "metadata": {},
        "include_memories": True,
    }


@pytest.fixture(scope="session")
def event_loop():
    """Create event loop for async tests."""
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()
