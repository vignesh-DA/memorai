"""Configuration management for Long-Form Memory System."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    environment: Literal["development", "staging", "production"] = "development"
    log_level: str = "INFO"
    api_port: int = 8000

    # Database - PostgreSQL
    postgres_host: str = "localhost"
    postgres_port: int = 5432
    postgres_db: str = "long_form_memory"
    postgres_user: str = "postgres"
    postgres_password: str = ""

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""
    redis_cache_ttl: int = 3600

    # Pinecone
    pinecone_api_key: str = ""
    pinecone_environment: str = "us-east1-gcp"
    pinecone_index_name: str = "long-form-memory"

    # OpenAI
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    openai_extraction_model: str = "gpt-4o-mini"
    openai_main_model: str = "gpt-4o"

    # Anthropic Claude
    anthropic_api_key: str = ""
    claude_model: str = "claude-3-5-sonnet-20241022"

    # Groq
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"

    # LLM Provider Selection
    llm_provider: Literal["openai", "anthropic", "groq"] = "groq"
    embedding_provider: Literal["openai", "sentence-transformers"] = "sentence-transformers"
    embedding_model: str = "all-MiniLM-L6-v2"

    # Authentication
    jwt_secret_key: str = Field(default="your-secret-key-change-in-production-min-32-chars")
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 10080  # 7 days
    refresh_token_expire_days: int = 30

    # Memory Settings
    memory_retrieval_top_k: int = 10
    memory_embedding_dimension: int = 384
    memory_cache_hot_threshold: int = 5
    memory_decay_days: int = 90
    memory_confidence_threshold: float = 0.7

    # Performance
    max_context_tokens: int = 4000
    retrieval_timeout_ms: int = 50
    batch_embedding_size: int = 100
    connection_pool_size: int = 10

    # Celery
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"

    # Monitoring
    prometheus_port: int = 9090
    enable_metrics: bool = True
    
    # Sentry Error Tracking
    sentry_dsn: str = ""
    sentry_traces_sample_rate: float = 0.1
    sentry_profiles_sample_rate: float = 0.1
    
    # Rate Limiting
    rate_limit_enabled: bool = True
    rate_limit_per_minute: int = 100  # Per user
    rate_limit_burst: int = 20  # Burst allowance
    rate_limit_global_per_minute: int = 1000  # Global limit
    
    # CORS Security
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000", "http://localhost:8000"])
    cors_allow_credentials: bool = True
    
    # Security Headers
    security_headers_enabled: bool = True
    
    # Graceful Shutdown
    shutdown_timeout: int = 30  # seconds

    @field_validator("memory_confidence_threshold")
    @classmethod
    def validate_confidence_threshold(cls, v: float) -> float:
        """Ensure confidence threshold is between 0 and 1."""
        if not 0 <= v <= 1:
            raise ValueError("Confidence threshold must be between 0 and 1")
        return v
    
    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors_origins(cls, v):
        """Parse CORS origins from string or list."""
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",")]
        return v

    @property
    def postgres_url(self) -> str:
        """Generate PostgreSQL connection URL."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def postgres_async_url(self) -> str:
        """Generate async PostgreSQL connection URL."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        """Generate Redis connection URL."""
        if self.redis_password:
            return f"redis://:{self.redis_password}@{self.redis_host}:{self.redis_port}/{self.redis_db}"
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"

    @property
    def is_production(self) -> bool:
        """Check if running in production environment."""
        return self.environment == "production"


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()


# Global settings instance for convenience
settings = get_settings()
