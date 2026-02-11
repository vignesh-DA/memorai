"""Production-grade health check system."""

import asyncio
import logging
import time
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional

from pydantic import BaseModel
from sqlalchemy import text
from redis import Redis

from app.config import get_settings
from app.database import db_manager

logger = logging.getLogger(__name__)
settings = get_settings()


class HealthStatus(str, Enum):
    """Health check status."""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class ComponentHealth(BaseModel):
    """Health status of a single component."""
    status: HealthStatus
    message: str
    latency_ms: Optional[float] = None
    details: Optional[Dict[str, Any]] = None


class SystemHealth(BaseModel):
    """Overall system health."""
    status: HealthStatus
    timestamp: datetime
    uptime_seconds: float
    checks: Dict[str, ComponentHealth]
    version: str = "1.0.0"


class HealthChecker:
    """
    Comprehensive health checking system.
    
    Checks:
    - Database connectivity and latency
    - Redis connectivity and latency
    - Embedding model status
    - System resources
    - External API availability
    """
    
    def __init__(self):
        self.start_time = time.time()
        self.cache: Optional[SystemHealth] = None
        self.cache_ttl = 5  # Cache results for 5 seconds (fast health checks)
        self.last_check = 0
    
    async def get_health(self, include_details: bool = False) -> SystemHealth:
        """
        Get comprehensive system health.
        
        Args:
            include_details: Include detailed diagnostics (may be slower)
        """
        # Return cached result if fresh
        current_time = time.time()
        if self.cache and (current_time - self.last_check) < self.cache_ttl:
            return self.cache
        
        # Run health checks
        checks = {}
        
        # Run checks in parallel for speed
        check_tasks = [
            ("database", self._check_database()),
            ("redis", self._check_redis()),
            ("embeddings", self._check_embeddings()),
        ]
        
        if include_details:
            check_tasks.extend([
                ("disk", self._check_disk()),
                ("memory", self._check_memory()),
            ])
        
        # Execute all checks concurrently
        results = await asyncio.gather(
            *[task for _, task in check_tasks],
            return_exceptions=True
        )
        
        # Collect results
        for (name, _), result in zip(check_tasks, results):
            if isinstance(result, Exception):
                checks[name] = ComponentHealth(
                    status=HealthStatus.UNHEALTHY,
                    message=f"Check failed: {str(result)}"
                )
            else:
                checks[name] = result
        
        # Determine overall status
        overall_status = self._determine_overall_status(checks)
        
        # Build health response
        health = SystemHealth(
            status=overall_status,
            timestamp=datetime.now(),
            uptime_seconds=current_time - self.start_time,
            checks=checks
        )
        
        # Cache result
        self.cache = health
        self.last_check = current_time
        
        return health
    
    async def _check_database(self) -> ComponentHealth:
        """Check PostgreSQL database health."""
        if not db_manager._engine:
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message="Database not initialized"
            )
        
        try:
            start = time.time()
            
            # Test query
            async with db_manager._engine.begin() as conn:
                result = await conn.execute(text("SELECT 1 as health, version() as version"))
                row = result.fetchone()
            
            latency = (time.time() - start) * 1000
            
            # Check pool status
            pool = db_manager._engine.pool
            pool_status = {
                "size": pool.size() if hasattr(pool, 'size') else "unknown",
                "checked_in": getattr(pool, 'checkedin', lambda: 0)(),
                "overflow": getattr(pool, 'overflow', lambda: 0)(),
            }
            
            # Determine status based on latency
            if latency < 100:
                status = HealthStatus.HEALTHY
                message = "Database operational"
            elif latency < 500:
                status = HealthStatus.DEGRADED
                message = f"Database slow (latency: {latency:.2f}ms)"
            else:
                status = HealthStatus.DEGRADED
                message = f"Database very slow (latency: {latency:.2f}ms)"
            
            return ComponentHealth(
                status=status,
                message=message,
                latency_ms=round(latency, 2),
                details={
                    "version": row[1].split()[0] if row else "unknown",
                    "pool": pool_status
                }
            )
            
        except Exception as e:
            logger.error(f"Database health check failed: {e}", exc_info=True)
            return ComponentHealth(
                status=HealthStatus.UNHEALTHY,
                message=f"Database connection failed: {str(e)}"
            )
    
    async def _check_redis(self) -> ComponentHealth:
        """Check Redis health."""
        if not db_manager._redis_client:
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message="Redis not configured (optional component)"
            )
        
        try:
            start = time.time()
            
            # Test Redis connection (async client)
            redis_client = db_manager._redis_client
            
            # Ping test (async - requires await)
            await redis_client.ping()
            
            # Get info (async - requires await)
            info = await redis_client.info("server")
            
            latency = (time.time() - start) * 1000
            
            # Determine status based on latency
            if latency < 50:
                status = HealthStatus.HEALTHY
                message = "Redis operational"
            elif latency < 200:
                status = HealthStatus.DEGRADED
                message = f"Redis slow (latency: {latency:.2f}ms)"
            else:
                status = HealthStatus.DEGRADED
                message = f"Redis very slow (latency: {latency:.2f}ms)"
            
            return ComponentHealth(
                status=status,
                message=message,
                latency_ms=round(latency, 2),
                details={
                    "version": info.get("redis_version", "unknown"),
                    "uptime_days": info.get("uptime_in_days", 0),
                    "connected_clients": info.get("connected_clients", 0)
                }
            )
            
        except Exception as e:
            logger.error(f"Redis health check failed: {e}", exc_info=True)
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message=f"Redis connection failed: {str(e)} (non-critical)"
            )
    
    async def _check_embeddings(self) -> ComponentHealth:
        """Check embedding model health (lightweight check)."""
        try:
            # Check if embedding model is initialized
            if settings.embedding_provider == "sentence-transformers":
                # Check if model function has been cached (already loaded)
                from app.utils.embeddings import _get_sentence_transformer
                
                # Check if the lru_cache has any cached results
                cache_info = _get_sentence_transformer.cache_info()
                
                if cache_info.currsize > 0:
                    # Model is cached - do a quick test
                    model = _get_sentence_transformer(settings.embedding_model)
                    
                    start = time.time()
                    test_text = "health check"
                    _ = model.encode(test_text)
                    latency = (time.time() - start) * 1000
                    
                    return ComponentHealth(
                        status=HealthStatus.HEALTHY,
                        message="Embedding model operational",
                        latency_ms=round(latency, 2),
                        details={
                            "provider": settings.embedding_provider,
                            "model": settings.embedding_model,
                            "dimension": model.get_sentence_embedding_dimension(),
                            "cached": True
                        }
                    )
                else:
                    # Model not loaded yet - this is OK, will load on first use
                    return ComponentHealth(
                        status=HealthStatus.DEGRADED,
                        message="Embedding model not loaded yet (will initialize on first use)",
                        details={
                            "provider": settings.embedding_provider,
                            "model": settings.embedding_model,
                            "cached": False
                        }
                    )
            else:
                # OpenAI embeddings - just verify config
                return ComponentHealth(
                    status=HealthStatus.HEALTHY,
                    message="OpenAI embeddings configured",
                    details={
                        "provider": settings.embedding_provider,
                        "model": settings.openai_embedding_model
                    }
                )
            
        except Exception as e:
            logger.error(f"Embedding health check failed: {e}", exc_info=True)
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message=f"Embedding model issue: {str(e)}"
            )
    
    async def _check_disk(self) -> ComponentHealth:
        """Check disk space."""
        try:
            import shutil
            
            # Check disk space
            total, used, free = shutil.disk_usage("/")
            
            free_gb = free // (2**30)
            free_percent = (free / total) * 100
            
            if free_percent > 20:
                status = HealthStatus.HEALTHY
                message = f"Disk space sufficient ({free_gb}GB free)"
            elif free_percent > 10:
                status = HealthStatus.DEGRADED
                message = f"Disk space low ({free_gb}GB free)"
            else:
                status = HealthStatus.UNHEALTHY
                message = f"Disk space critical ({free_gb}GB free)"
            
            return ComponentHealth(
                status=status,
                message=message,
                details={
                    "total_gb": round(total / (2**30), 2),
                    "used_gb": round(used / (2**30), 2),
                    "free_gb": round(free / (2**30), 2),
                    "free_percent": round(free_percent, 2)
                }
            )
            
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message=f"Disk check failed: {str(e)}"
            )
    
    async def _check_memory(self) -> ComponentHealth:
        """Check system memory."""
        try:
            import psutil
            
            mem = psutil.virtual_memory()
            
            if mem.percent < 80:
                status = HealthStatus.HEALTHY
                message = f"Memory usage normal ({mem.percent:.1f}%)"
            elif mem.percent < 90:
                status = HealthStatus.DEGRADED
                message = f"Memory usage high ({mem.percent:.1f}%)"
            else:
                status = HealthStatus.UNHEALTHY
                message = f"Memory usage critical ({mem.percent:.1f}%)"
            
            return ComponentHealth(
                status=status,
                message=message,
                details={
                    "total_gb": round(mem.total / (2**30), 2),
                    "available_gb": round(mem.available / (2**30), 2),
                    "percent": mem.percent
                }
            )
            
        except ImportError:
            return ComponentHealth(
                status=HealthStatus.HEALTHY,
                message="Memory check not available (psutil not installed)"
            )
        except Exception as e:
            return ComponentHealth(
                status=HealthStatus.DEGRADED,
                message=f"Memory check failed: {str(e)}"
            )
    
    def _determine_overall_status(self, checks: Dict[str, ComponentHealth]) -> HealthStatus:
        """Determine overall system status from component checks."""
        if not checks:
            return HealthStatus.UNHEALTHY
        
        # Count status types
        statuses = [check.status for check in checks.values()]
        
        # Define critical vs optional components
        critical_components = ["database"]  # Only database is truly critical
        optional_components = ["redis", "embeddings", "disk", "memory"]  # Can be degraded
        
        # If any critical component is unhealthy, system is unhealthy
        for comp in critical_components:
            if comp in checks and checks[comp].status == HealthStatus.UNHEALTHY:
                return HealthStatus.UNHEALTHY
        
        # If any non-optional component is unhealthy = system degraded
        for comp_name, comp_health in checks.items():
            if comp_name not in optional_components and comp_health.status == HealthStatus.UNHEALTHY:
                return HealthStatus.DEGRADED
        
        # If critical components degraded = system degraded
        for comp in critical_components:
            if comp in checks and checks[comp].status == HealthStatus.DEGRADED:
                return HealthStatus.DEGRADED
        
        # All critical components healthy = system healthy
        # (even if optional components are degraded)
        return HealthStatus.HEALTHY


# Global health checker instance
health_checker = HealthChecker()
