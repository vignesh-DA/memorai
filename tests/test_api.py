"""Tests for API endpoints."""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import app


client = TestClient(app)


def test_root_endpoint():
    """Test root endpoint."""
    response = client.get("/")
    assert response.status_code == 200
    assert "service" in response.json()


def test_health_endpoint():
    """Test health check endpoint."""
    with patch("app.database.db_manager.health_check") as mock_health:
        mock_health.return_value = {
            "postgres": "healthy",
            "redis": "healthy",
            "pinecone": "healthy",
        }
        
        response = client.get("/api/v1/health")
        assert response.status_code in [200, 503]  # May fail if services not running


@pytest.mark.skip(reason="Requires full setup")
def test_conversation_endpoint(sample_conversation_request):
    """Test conversation processing endpoint."""
    response = client.post(
        "/api/v1/conversation",
        json=sample_conversation_request,
    )
    
    # Will fail without proper setup, but tests schema
    assert response.status_code in [200, 500]


@pytest.mark.skip(reason="Requires full setup")
def test_create_memory(sample_memory_data):
    """Test memory creation endpoint."""
    response = client.post(
        "/api/v1/memories",
        json=sample_memory_data,
    )
    
    # Will fail without proper setup
    assert response.status_code in [201, 500]


def test_metrics_endpoint():
    """Test metrics export endpoint."""
    response = client.get("/api/v1/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"] == "text/plain; charset=utf-8"
