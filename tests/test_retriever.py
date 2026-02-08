"""Tests for memory retrieval service."""

import pytest
from unittest.mock import MagicMock

from app.models.memory import MemorySearchQuery, MemoryType
from app.services.retriever import MemoryRetriever
from app.utils.embeddings import EmbeddingGenerator


@pytest.mark.asyncio
async def test_calculate_recency_score():
    """Test recency score calculation."""
    embedder = EmbeddingGenerator(redis_client=None)
    retriever = MemoryRetriever(
        redis_client=MagicMock(),
        embedding_generator=embedder,
    )
    
    # Recent memory
    score = retriever._calculate_recency_score(
        source_turn=95,
        current_turn=100,
    )
    assert score > 0.9
    
    # Old memory
    score = retriever._calculate_recency_score(
        source_turn=0,
        current_turn=1000,
    )
    assert score < 0.5


def test_calculate_relevance_score():
    """Test composite relevance score calculation."""
    embedder = EmbeddingGenerator(redis_client=None)
    retriever = MemoryRetriever(
        redis_client=MagicMock(),
        embedding_generator=embedder,
    )
    
    score = retriever._calculate_relevance_score(
        similarity=0.9,
        recency=0.8,
        access=0.7,
        confidence=0.9,
    )
    
    assert 0.0 <= score <= 1.0
    assert score > 0.7  # Should be high given good inputs


@pytest.mark.asyncio
async def test_search_with_empty_results(mock_redis, mock_pinecone_index):
    """Test search with no results."""
    embedder = EmbeddingGenerator(redis_client=mock_redis)
    retriever = MemoryRetriever(
        redis_client=mock_redis,
        embedding_generator=embedder,
    )
    
    # Mock Pinecone to return no matches
    retriever.pinecone_index = mock_pinecone_index
    mock_pinecone_index.query.return_value = MagicMock(matches=[])
    
    query = MemorySearchQuery(
        user_id="test_user",
        query="test query",
        top_k=10,
    )
    
    # Mock embedding generation
    with pytest.mock.patch.object(embedder, 'generate', return_value=[0.1] * 1536):
        results = await retriever.search(query)
    
    assert isinstance(results, list)
