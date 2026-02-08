# Long-Form Memory System for LLM Applications

A production-grade memory layer that enables LLMs to retain and recall information across 1,000+ conversation turns in real-time.

## 🚀 Features

- **Automated Memory Extraction** - Identifies important information from conversations
- **Hybrid Search** - Combines semantic similarity with recency and access patterns
- **Sub-50ms Retrieval** - Optimized for real-time inference
- **Memory Management** - Automatic decay, consolidation, and conflict resolution
- **Scalable Architecture** - Handles millions of users with 1000+ turns each
- **Production-Ready** - Full error handling, logging, monitoring, and metrics

## 📋 Architecture

```
User Input → Retrieve Memories → Build Context → LLM Inference → Extract New Memories (async)
```

### Technology Stack

- **Backend**: FastAPI (Python 3.11+)
- **Vector DB**: Pinecone (cosine similarity)
- **Cache**: Redis (hot memories, embeddings)
- **Database**: PostgreSQL with pgvector
- **LLM**: OpenAI GPT-4o-mini (extraction), GPT-4o (responses)
- **Embeddings**: OpenAI text-embedding-3-small (1536d)
- **Queue**: Celery + Redis (async processing)
- **Monitoring**: Prometheus + Grafana

## 🏗️ Project Structure

```
long_form_memory/
├── app/
│   ├── main.py                 # FastAPI application
│   ├── config.py               # Configuration management
│   ├── database.py             # Database connections
│   ├── worker.py               # Celery worker tasks
│   ├── models/
│   │   ├── memory.py           # Memory schemas
│   │   └── conversation.py     # Conversation schemas
│   ├── services/
│   │   ├── extractor.py        # Memory extraction
│   │   ├── retriever.py        # Memory retrieval
│   │   ├── storage.py          # Database operations
│   │   └── memory_manager.py   # Consolidation, decay
│   ├── api/
│   │   └── routes.py           # API endpoints
│   └── utils/
│       ├── embeddings.py       # Embedding generation
│       └── metrics.py          # Prometheus metrics
├── scripts/
│   └── init_db.py              # Database initialization
├── tests/                      # Test suite
├── docker-compose.yml          # Docker orchestration
├── Dockerfile                  # Container definition
├── requirements.txt            # Python dependencies
└── README.md                   # This file
```

## 🔧 Setup

### Prerequisites

- Python 3.11+
- PostgreSQL 16+ with pgvector extension
- Redis 7+
- Pinecone account
- OpenAI API key

### Local Development

1. **Clone and setup environment**

```bash
cd "e:\Long Term Memory"
python -m venv venv
venv\Scripts\activate  # Windows
pip install -r requirements.txt
```

2. **Configure environment variables**

```bash
copy .env.example .env
# Edit .env with your credentials
```

Required environment variables:
- `OPENAI_API_KEY` - Your OpenAI API key
- `PINECONE_API_KEY` - Your Pinecone API key
- `PINECONE_ENVIRONMENT` - Pinecone environment (e.g., us-east1-gcp)
- `POSTGRES_PASSWORD` - PostgreSQL password
- `REDIS_PASSWORD` - Redis password (optional)

3. **Start infrastructure (Docker)**

```bash
docker-compose up -d postgres redis
```

4. **Initialize database**

```bash
python scripts/init_db.py
```

5. **Run the application**

```bash
# API server
uvicorn app.main:app --reload --port 8000

# Celery worker (separate terminal)
celery -A app.worker.celery_app worker --loglevel=info

# Celery beat for periodic tasks (separate terminal)
celery -A app.worker.celery_app beat --loglevel=info
```

### Docker Deployment

```bash
# Start all services
docker-compose up -d

# View logs
docker-compose logs -f api

# Stop all services
docker-compose down
```

## 📚 API Documentation

Once running, visit:
- **API Docs**: http://localhost:8000/docs
- **Prometheus**: http://localhost:9090
- **Grafana**: http://localhost:3000 (admin/admin)

### Key Endpoints

#### Process Conversation
```bash
POST /api/v1/conversation
{
  "user_id": "user_123",
  "turn_number": 500,
  "message": "What's my favorite color?",
  "include_memories": true
}
```

#### Create Memory
```bash
POST /api/v1/memories
{
  "user_id": "user_123",
  "type": "preference",
  "content": "User prefers dark mode",
  "source_turn": 10,
  "confidence": 0.9
}
```

#### Search Memories
```bash
POST /api/v1/memories/{user_id}/search?query=preferences&top_k=10
```

#### Get User Stats
```bash
GET /api/v1/memories/{user_id}/stats
```

#### Optimize Memories
```bash
POST /api/v1/memories/{user_id}/optimize?current_turn=500
```

#### Health Check
```bash
GET /api/v1/health
```

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app --cov-report=html

# Run specific test file
pytest tests/test_extractor.py -v
```

## 📊 Memory Schema

```python
{
    "memory_id": "uuid",
    "user_id": "string",
    "type": "preference|fact|commitment|instruction|entity",
    "content": "string",
    "embedding": "vector[1536]",
    "metadata": {
        "source_turn": "int",
        "created_at": "datetime",
        "last_accessed": "datetime",
        "access_count": "int",
        "confidence": "float (0-1)",
        "decay_score": "float (0-1)"
    }
}
```

## 🎯 Retrieval Algorithm

Composite relevance score:
```
score = 0.4 × semantic_similarity +
        0.3 × recency_score +
        0.2 × access_frequency +
        0.1 × confidence_score
```

## 🔄 Memory Lifecycle

1. **Extraction** - LLM analyzes conversation turn
2. **Storage** - Saved to PostgreSQL + Pinecone + Redis cache
3. **Retrieval** - Hybrid search finds relevant memories (<50ms)
4. **Decay** - Temporal decay applied based on age/access
5. **Consolidation** - Similar memories merged
6. **Cleanup** - Old low-value memories removed

## ⚙️ Configuration

Key settings in `.env`:

```bash
# Performance
MEMORY_RETRIEVAL_TOP_K=10          # Memories per query
RETRIEVAL_TIMEOUT_MS=50            # Max retrieval latency
MAX_CONTEXT_TOKENS=4000            # Token budget for context
BATCH_EMBEDDING_SIZE=100           # Batch size for embeddings

# Memory Management
MEMORY_CONFIDENCE_THRESHOLD=0.7    # Min confidence to store
MEMORY_DECAY_DAYS=90               # Decay period
MEMORY_CACHE_HOT_THRESHOLD=5       # Access count for hot

# Database
CONNECTION_POOL_SIZE=10            # DB connection pool
REDIS_CACHE_TTL=3600              # Cache TTL in seconds
```

## 📈 Monitoring

Built-in Prometheus metrics:
- `memory_api_requests_total` - API request count
- `memory_api_request_duration_seconds` - Request latency
- `memory_retrieval_duration_ms` - Retrieval latency (p95 < 50ms goal)
- `memory_operations_total` - Memory operation count
- `llm_call_duration_seconds` - LLM API latency
- `llm_tokens_used_total` - Token usage tracking
- `cache_hits_total` / `cache_misses_total` - Cache performance

## 🚨 Error Handling

- Automatic retries with exponential backoff
- Graceful degradation (works without cache)
- Comprehensive logging at INFO and DEBUG levels
- Health checks for all dependencies

## 🔒 Security & Privacy

- GDPR-compliant memory deletion: `DELETE /api/v1/memories/{user_id}/all`
- No PII in logs (configurable)
- Secure credential management via environment variables
- Connection pooling with SSL support (production)

## 🎓 Example Usage

See example script:

```python
import httpx

# Process conversation with memory
response = httpx.post("http://localhost:8000/api/v1/conversation", json={
    "user_id": "user_123",
    "turn_number": 1,
    "message": "I love espresso and prefer dark roast coffee"
})

# Later conversation
response = httpx.post("http://localhost:8000/api/v1/conversation", json={
    "user_id": "user_123",
    "turn_number": 500,
    "message": "What kind of coffee should I buy?"
})
# System will recall the preference from turn 1
```

## 📊 Performance Targets

- ✅ Memory recall accuracy: **>95%**
- ✅ Retrieval latency: **p95 <50ms**
- ✅ False positive rate: **<5%**
- ✅ System uptime: **>99.9%**
- ✅ Cost per 1000 turns: **<$0.10**

## 🛠️ Troubleshooting

### Database connection errors
```bash
# Check PostgreSQL
docker-compose logs postgres

# Recreate schema
python scripts/init_db.py
```

### Pinecone errors
```bash
# Verify API key and environment in .env
# Check index exists in Pinecone dashboard
```

### High latency
```bash
# Check Prometheus metrics
# Increase connection pool size
# Enable Redis caching
# Reduce MEMORY_RETRIEVAL_TOP_K
```

## 🔮 Future Enhancements

- [ ] Multi-tenant isolation
- [ ] Semantic memory clustering
- [ ] Importance-based memory pruning
- [ ] Cross-user memory sharing (with consent)
- [ ] Memory versioning and rollback
- [ ] Enhanced conflict detection
- [ ] Real-time streaming responses
- [ ] Multi-modal memory (images, audio)

## 📄 License

This project is licensed under the MIT License.

## 🤝 Contributing

Contributions welcome! Please:
1. Follow PEP 8 style guide
2. Add type hints
3. Include docstrings
4. Write tests for new features
5. Update documentation

## 📞 Support

For issues and questions:
- GitHub Issues: [Create an issue]
- Documentation: Check `/docs` endpoint
- Logs: Check application logs for detailed errors

---

**Built with ❤️ for production-grade LLM applications**
