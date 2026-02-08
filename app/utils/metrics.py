"""Monitoring and metrics utilities."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from functools import wraps
from typing import Any, Callable, Optional

from prometheus_client import Counter, Gauge, Histogram, generate_latest

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


# Prometheus Metrics
REQUEST_COUNT = Counter(
    "memory_api_requests_total",
    "Total API requests",
    ["endpoint", "method", "status"],
)

REQUEST_LATENCY = Histogram(
    "memory_api_request_duration_seconds",
    "API request latency",
    ["endpoint", "method"],
)

MEMORY_OPERATIONS = Counter(
    "memory_operations_total",
    "Total memory operations",
    ["operation", "status"],
)

MEMORY_RETRIEVAL_LATENCY = Histogram(
    "memory_retrieval_duration_ms",
    "Memory retrieval latency in milliseconds",
    buckets=[5, 10, 25, 50, 100, 250, 500, 1000],
)

MEMORY_COUNT = Gauge(
    "memory_total_count",
    "Total number of memories stored",
    ["user_id"],
)

EMBEDDING_GENERATION_TIME = Histogram(
    "embedding_generation_duration_seconds",
    "Time to generate embeddings",
    ["batch_size"],
)

LLM_CALL_LATENCY = Histogram(
    "llm_call_duration_seconds",
    "LLM API call latency",
    ["model", "operation"],
)

LLM_TOKEN_USAGE = Counter(
    "llm_tokens_used_total",
    "Total tokens used in LLM calls",
    ["model", "type"],  # type: prompt, completion
)

CACHE_HITS = Counter(
    "cache_hits_total",
    "Cache hit count",
    ["cache_type"],
)

CACHE_MISSES = Counter(
    "cache_misses_total",
    "Cache miss count",
    ["cache_type"],
)


class MetricsCollector:
    """Collect and export application metrics."""

    def __init__(self):
        """Initialize metrics collector."""
        self.enabled = settings.enable_metrics

    def record_request(
        self,
        endpoint: str,
        method: str,
        status: int,
        duration: float,
    ) -> None:
        """Record API request metrics."""
        if not self.enabled:
            return

        REQUEST_COUNT.labels(
            endpoint=endpoint,
            method=method,
            status=str(status),
        ).inc()

        REQUEST_LATENCY.labels(
            endpoint=endpoint,
            method=method,
        ).observe(duration)

    def record_memory_operation(
        self,
        operation: str,
        status: str = "success",
    ) -> None:
        """Record memory operation."""
        if not self.enabled:
            return

        MEMORY_OPERATIONS.labels(
            operation=operation,
            status=status,
        ).inc()

    def record_retrieval_latency(self, duration_ms: float) -> None:
        """Record memory retrieval latency."""
        if not self.enabled:
            return

        MEMORY_RETRIEVAL_LATENCY.observe(duration_ms)

    def update_memory_count(self, user_id: str, count: int) -> None:
        """Update total memory count for user."""
        if not self.enabled:
            return

        MEMORY_COUNT.labels(user_id=user_id).set(count)

    def record_embedding_time(self, duration: float, batch_size: int) -> None:
        """Record embedding generation time."""
        if not self.enabled:
            return

        EMBEDDING_GENERATION_TIME.labels(
            batch_size=str(batch_size),
        ).observe(duration)

    def record_llm_call(
        self,
        model: str,
        operation: str,
        duration: float,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
    ) -> None:
        """Record LLM API call metrics."""
        if not self.enabled:
            return

        LLM_CALL_LATENCY.labels(
            model=model,
            operation=operation,
        ).observe(duration)

        if prompt_tokens > 0:
            LLM_TOKEN_USAGE.labels(
                model=model,
                type="prompt",
            ).inc(prompt_tokens)

        if completion_tokens > 0:
            LLM_TOKEN_USAGE.labels(
                model=model,
                type="completion",
            ).inc(completion_tokens)

    def record_cache_hit(self, cache_type: str) -> None:
        """Record cache hit."""
        if not self.enabled:
            return

        CACHE_HITS.labels(cache_type=cache_type).inc()

    def record_cache_miss(self, cache_type: str) -> None:
        """Record cache miss."""
        if not self.enabled:
            return

        CACHE_MISSES.labels(cache_type=cache_type).inc()

    def export_metrics(self) -> bytes:
        """Export metrics in Prometheus format."""
        return generate_latest()


# Global metrics instance
metrics = MetricsCollector()


def track_time(operation: str) -> Callable:
    """Decorator to track function execution time.
    
    Args:
        operation: Name of the operation being tracked
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            try:
                result = await func(*args, **kwargs)
                metrics.record_memory_operation(operation, "success")
                return result
            except Exception as e:
                metrics.record_memory_operation(operation, "error")
                raise
            finally:
                duration = time.time() - start_time
                logger.debug(f"{operation} took {duration:.3f}s")

        @wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                metrics.record_memory_operation(operation, "success")
                return result
            except Exception as e:
                metrics.record_memory_operation(operation, "error")
                raise
            finally:
                duration = time.time() - start_time
                logger.debug(f"{operation} took {duration:.3f}s")

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        return sync_wrapper

    return decorator


@asynccontextmanager
async def track_latency(operation: str):
    """Context manager to track operation latency.
    
    Args:
        operation: Name of the operation
        
    Yields:
        Dictionary to store timing info
    """
    start_time = time.time()
    timing_info = {"start": start_time}
    
    try:
        yield timing_info
    finally:
        duration = time.time() - start_time
        timing_info["duration"] = duration
        timing_info["duration_ms"] = duration * 1000
        logger.debug(f"{operation} latency: {duration * 1000:.2f}ms")
