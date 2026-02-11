"""Production-grade middleware for Long-Form Memory System."""

import logging
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Callable, Optional

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from redis import Redis
from starlette.middleware.base import BaseHTTPMiddleware

from app.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Production-grade rate limiting middleware with Redis backend.
    
    Features:
    - Per-user rate limiting
    - Global rate limiting
    - Burst allowance with token bucket algorithm
    - Graceful degradation (fallback to in-memory if Redis fails)
    - Detailed metrics and logging
    """
    
    def __init__(self, app, redis_client: Optional[Redis] = None):
        super().__init__(app)
        self.redis_client = redis_client
        self.fallback_storage = defaultdict(list)  # In-memory fallback
        self.use_redis = redis_client is not None
        self.enabled = settings.rate_limit_enabled
        
        # Exempt paths from rate limiting
        self.exempt_paths = {"/health", "/api/health", "/", "/auth.html", "/static", "/docs", "/openapi.json", "/redoc"}
        
        logger.info(
            f"RateLimitMiddleware initialized: "
            f"enabled={self.enabled}, "
            f"backend={'Redis' if self.use_redis else 'In-Memory'}, "
            f"limits={settings.rate_limit_per_minute}/min per user, "
            f"global={settings.rate_limit_global_per_minute}/min"
        )
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        """Process request with rate limiting."""
        if not self.enabled:
            return await call_next(request)
        
        # Skip rate limiting for exempt paths
        if any(request.url.path.startswith(path) for path in self.exempt_paths):
            return await call_next(request)
        
        # Extract user identifier
        user_id = self._get_user_identifier(request)
        client_ip = self._get_client_ip(request)
        
        # Check rate limits
        try:
            # Check global rate limit
            if not await self._check_global_limit():
                logger.warning(f"Global rate limit exceeded from IP: {client_ip}")
                return self._rate_limit_response("Global rate limit exceeded. Please try again later.")
            
            # Check per-user rate limit
            if not await self._check_user_limit(user_id):
                logger.warning(f"User rate limit exceeded: user={user_id}, ip={client_ip}")
                return self._rate_limit_response(
                    f"Rate limit exceeded. Maximum {settings.rate_limit_per_minute} requests per minute allowed."
                )
            
            # Record successful request
            await self._record_request(user_id)
            
        except Exception as e:
            # Rate limiting should never break the application
            logger.error(f"Rate limiting error: {e}", exc_info=True)
            # Continue processing the request
        
        response = await call_next(request)
        
        # Add rate limit headers
        remaining = await self._get_remaining_requests(user_id)
        response.headers["X-RateLimit-Limit"] = str(settings.rate_limit_per_minute)
        response.headers["X-RateLimit-Remaining"] = str(max(0, remaining))
        response.headers["X-RateLimit-Reset"] = str(int(time.time()) + 60)
        
        return response
    
    def _get_user_identifier(self, request: Request) -> str:
        """Extract user identifier from request (JWT, session, or IP)."""
        # Try to get authenticated user
        if hasattr(request.state, "user") and request.state.user:
            return f"user:{request.state.user}"
        
        # Fallback to IP address
        return f"ip:{self._get_client_ip(request)}"
    
    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP from request headers."""
        # Check X-Forwarded-For header (common in production behind proxy)
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        
        # Check X-Real-IP header
        real_ip = request.headers.get("X-Real-IP")
        if real_ip:
            return real_ip
        
        # Fallback to direct client
        return request.client.host if request.client else "unknown"
    
    async def _check_global_limit(self) -> bool:
        """Check if global rate limit is exceeded."""
        key = "ratelimit:global"
        return await self._check_limit(key, settings.rate_limit_global_per_minute)
    
    async def _check_user_limit(self, user_id: str) -> bool:
        """Check if user rate limit is exceeded."""
        key = f"ratelimit:{user_id}"
        return await self._check_limit(key, settings.rate_limit_per_minute)
    
    async def _check_limit(self, key: str, limit: int) -> bool:
        """Check rate limit using token bucket algorithm."""
        current_time = time.time()
        window_start = current_time - 60  # 1-minute sliding window
        
        if self.use_redis and self.redis_client:
            try:
                # Use Redis for distributed rate limiting
                pipe = self.redis_client.pipeline()
                
                # Remove old entries
                pipe.zremrangebyscore(key, 0, window_start)
                
                # Count requests in current window
                pipe.zcard(key)
                
                # Execute pipeline
                results = pipe.execute()
                request_count = results[1]
                
                return request_count < limit
                
            except Exception as e:
                logger.error(f"Redis rate limit check failed: {e}. Falling back to in-memory.")
                self.use_redis = False
        
        # In-memory fallback
        if key not in self.fallback_storage:
            self.fallback_storage[key] = []
        
        # Remove old timestamps
        self.fallback_storage[key] = [
            ts for ts in self.fallback_storage[key] if ts > window_start
        ]
        
        return len(self.fallback_storage[key]) < limit
    
    async def _record_request(self, user_id: str):
        """Record a request for rate limiting."""
        key = f"ratelimit:{user_id}"
        current_time = time.time()
        
        if self.use_redis and self.redis_client:
            try:
                # Add timestamp to sorted set
                self.redis_client.zadd(key, {str(current_time): current_time})
                # Set expiration to clean up (2 minutes to be safe)
                self.redis_client.expire(key, 120)
                return
            except Exception as e:
                logger.error(f"Redis record request failed: {e}")
                self.use_redis = False
        
        # In-memory fallback
        self.fallback_storage[key].append(current_time)
    
    async def _get_remaining_requests(self, user_id: str) -> int:
        """Get remaining requests for user."""
        key = f"ratelimit:{user_id}"
        window_start = time.time() - 60
        
        if self.use_redis and self.redis_client:
            try:
                count = self.redis_client.zcount(key, window_start, float('inf'))
                return settings.rate_limit_per_minute - count
            except:
                pass
        
        # In-memory fallback
        if key in self.fallback_storage:
            recent = [ts for ts in self.fallback_storage[key] if ts > window_start]
            return settings.rate_limit_per_minute - len(recent)
        
        return settings.rate_limit_per_minute
    
    def _rate_limit_response(self, message: str) -> JSONResponse:
        """Return rate limit exceeded response."""
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={
                "error": "rate_limit_exceeded",
                "message": message,
                "retry_after": 60
            },
            headers={
                "Retry-After": "60",
                "X-RateLimit-Limit": str(settings.rate_limit_per_minute),
                "X-RateLimit-Remaining": "0"
            }
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Add security headers to all responses.
    
    Headers:
    - X-Content-Type-Options: nosniff
    - X-Frame-Options: DENY
    - X-XSS-Protection: 1; mode=block
    - Strict-Transport-Security: (production only)
    - Content-Security-Policy
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        
        if not settings.security_headers_enabled:
            return response
        
        # Basic security headers
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        
        # HSTS (production only)
        if settings.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        
        # CSP (Content Security Policy)
        # More permissive in development, strict in production
        if settings.is_production:
            # Production: Strict CSP
            csp_directives = [
                "default-src 'self'",
                "script-src 'self'",
                "style-src 'self'",
                "img-src 'self' data: https:",
                "font-src 'self' data:",
                "connect-src 'self'",
                "frame-ancestors 'none'"
            ]
        else:
            # Development: Permissive CSP for easier debugging
            csp_directives = [
                "default-src 'self' http://localhost:* http://127.0.0.1:*",
                "script-src 'self' 'unsafe-inline' 'unsafe-eval' http://localhost:* http://127.0.0.1:*",
                "style-src 'self' 'unsafe-inline' http://localhost:* http://127.0.0.1:*",
                "img-src 'self' data: https: http:",
                "font-src 'self' data:",
                "connect-src 'self' http://localhost:* http://127.0.0.1:* ws://localhost:* ws://127.0.0.1:*",
                "frame-ancestors 'none'"
            ]
        
        response.headers["Content-Security-Policy"] = "; ".join(csp_directives)
        
        # Server identification (remove version info in production)
        if not settings.is_production:
            response.headers["X-Powered-By"] = "Long-Form Memory System v1.0"
        
        return response


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Enhanced request logging for production observability.
    
    Logs:
    - Request method, path, client IP
    - Response status, processing time
    - User identifier (if authenticated)
    - Request ID for tracing
    """
    
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate request ID for tracing
        request_id = self._generate_request_id()
        request.state.request_id = request_id
        
        # Extract client info
        client_ip = self._get_client_ip(request)
        user_agent = request.headers.get("user-agent", "unknown")
        
        # Start timer
        start_time = time.time()
        
        # Log incoming request
        logger.info(
            f"→ {request.method} {request.url.path}",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "client_ip": client_ip,
                "user_agent": user_agent[:100],  # Truncate
            }
        )
        
        # Process request
        try:
            response = await call_next(request)
            duration_ms = (time.time() - start_time) * 1000
            
            # Log response
            log_level = logging.WARNING if response.status_code >= 400 else logging.INFO
            logger.log(
                log_level,
                f"← {response.status_code} {request.method} {request.url.path} ({duration_ms:.2f}ms)",
                extra={
                    "request_id": request_id,
                    "status": response.status_code,
                    "duration_ms": duration_ms,
                }
            )
            
            # Add request ID to response headers for client tracing
            response.headers["X-Request-ID"] = request_id
            
            return response
            
        except Exception as e:
            duration_ms = (time.time() - start_time) * 1000
            logger.error(
                f"✗ {request.method} {request.url.path} failed ({duration_ms:.2f}ms): {e}",
                extra={"request_id": request_id},
                exc_info=True
            )
            raise
    
    def _generate_request_id(self) -> str:
        """Generate unique request ID."""
        import uuid
        return str(uuid.uuid4())[:8]
    
    def _get_client_ip(self, request: Request) -> str:
        """Extract client IP."""
        forwarded = request.headers.get("X-Forwarded-For")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return request.client.host if request.client else "unknown"
