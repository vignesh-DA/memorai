"""Main FastAPI application - Production Grade."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.api.auth_routes import router as auth_router
from app.config import get_settings
from app.database import db_manager

# Configure structured logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

settings = get_settings()

# Initialize Sentry for error tracking (production)
if settings.sentry_dsn:
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.redis import RedisIntegration
        
        sentry_sdk.init(
            dsn=settings.sentry_dsn,
            environment=settings.environment,
            traces_sample_rate=settings.sentry_traces_sample_rate,
            profiles_sample_rate=settings.sentry_profiles_sample_rate,
            integrations=[
                FastApiIntegration(),
                SqlalchemyIntegration(),
                RedisIntegration(),
            ],
            # Custom tags
            tags={
                "service": "long-form-memory",
                "version": "1.0.0"
            },
            # Filter sensitive data
            send_default_pii=False,
            before_send=lambda event, hint: _filter_sensitive_data(event, hint),
        )
        logger.info(f"✅ Sentry initialized: environment={settings.environment}")
    except ImportError:
        logger.warning("⚠️ Sentry SDK not installed. Error tracking disabled.")
    except Exception as e:
        logger.error(f"❌ Sentry initialization failed: {e}")


def _filter_sensitive_data(event, hint):
    """Filter sensitive data from Sentry events."""
    # Remove sensitive headers
    if "request" in event and "headers" in event["request"]:
        headers = event["request"]["headers"]
        sensitive_headers = ["authorization", "cookie", "x-api-key"]
        for header in sensitive_headers:
            if header in headers:
                headers[header] = "[FILTERED]"
    return event


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager with graceful startup/shutdown."""
    # Startup
    logger.info("🚀 Starting Long-Form Memory System")
    logger.info(f"📍 Environment: {settings.environment}")
    logger.info(f"🔌 LLM Provider: {settings.llm_provider}")
    logger.info(f"🧠 Embedding Provider: {settings.embedding_provider}")
    
    try:
        # Initialize database connections
        await db_manager.initialize()
        logger.info("✅ Database connections established")
        
        # Initialize authentication tables
        from app.services.auth_service import AuthService
        auth_service = AuthService(db_manager._engine)
        await auth_service.initialize_tables()
        logger.info("✅ Authentication system initialized")
        
        # Warm up embedding model (optional, improves first request latency)
        if settings.embedding_provider == "sentence-transformers":
            try:
                from app.utils.embeddings import get_embedding_model
                get_embedding_model()
                logger.info("✅ Embedding model warmed up")
            except Exception as e:
                logger.warning(f"⚠️ Embedding model warmup failed: {e}")
        
        logger.info("🎉 System ready to serve requests")
        
    except Exception as e:
        logger.error(f"❌ Failed to initialize system: {e}", exc_info=True)
        raise

    yield

    # Shutdown
    logger.info("🛑 Shutting down Long-Form Memory System")
    
    try:
        # Close database connections gracefully
        await db_manager.close()
        logger.info("✅ Database connections closed")
        
        # Additional cleanup if needed
        logger.info("✅ Graceful shutdown completed")
        
    except Exception as e:
        logger.error(f"❌ Error during shutdown: {e}", exc_info=True)


# Create FastAPI app with enhanced metadata
app = FastAPI(
    title="Long-Form Memory System",
    description="""
    # 🧠 Production-Grade Memory System for LLM Applications
    
    ## Features
    - **Persistent Long-Term Memory**: Store and retrieve conversational context across sessions
    - **Semantic Search**: Vector-based memory retrieval with pgvector
    - **Multi-User Support**: Secure authentication with JWT tokens
    - **Real-Time Updates**: WebSocket support for live memory updates
    - **Production Ready**: Rate limiting, monitoring, error tracking
    
    ## Architecture
    - FastAPI backend with async/await
    - PostgreSQL + pgvector for vector storage
    - Redis for caching and rate limiting
    - Sentence Transformers for embeddings
    - Groq/OpenAI/Anthropic for LLM inference
    
    ## Rate Limits
    - **Per User**: 100 requests/minute
    - **Global**: 1000 requests/minute
    
    ## Authentication
    All endpoints except `/health` and `/docs` require JWT authentication.
    Use `/api/v1/auth/login` to obtain a token.
    """,
    version="1.0.0",
    lifespan=lifespan,
    # Swagger UI configuration
    docs_url="/docs" if settings.environment != "production" else None,  # Disable in prod
    redoc_url="/redoc" if settings.environment != "production" else None,
    openapi_url="/openapi.json",
    # OpenAPI metadata
    contact={
        "name": "Long-Form Memory Team",
        "email": "support@longformmemory.ai",
    },
    license_info={
        "name": "MIT License",
        "url": "https://opensource.org/licenses/MIT",
    },
    # Servers (for API docs)
    servers=[
        {"url": "http://localhost:8000", "description": "Development"},
        {"url": "https://api.longformmemory.ai", "description": "Production"},
    ] if settings.environment == "development" else None,
)

# CORS Middleware - Production Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins if settings.environment == "production" else ["*"],
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Process-Time", "X-RateLimit-Limit", "X-RateLimit-Remaining"],
    max_age=600,  # Cache preflight requests for 10 minutes
)

# GZip Compression - Improve response times
app.add_middleware(GZipMiddleware, minimum_size=1000)

# Custom Middleware - Production Features
from app.middleware import (
    RateLimitMiddleware,
    SecurityHeadersMiddleware,
    RequestLoggingMiddleware,
)

# Add production middleware (order matters!)
if settings.security_headers_enabled:
    app.add_middleware(SecurityHeadersMiddleware)
    logger.info("✅ Security headers enabled")

if settings.rate_limit_enabled:
    app.add_middleware(RateLimitMiddleware, redis_client=db_manager._redis_client)
    logger.info(f"✅ Rate limiting enabled: {settings.rate_limit_per_minute}/min per user")

# Request logging (always enabled for observability)
app.add_middleware(RequestLoggingMiddleware)


# Request timing and metrics middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Add processing time header and record metrics."""
    import time
    
    start_time = time.time()
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # Add processing time header
        response.headers["X-Process-Time"] = f"{process_time:.4f}"
        
        # Record metrics
        try:
            from app.utils.metrics import metrics
            metrics.record_request(
                endpoint=request.url.path,
                method=request.method,
                status=response.status_code,
                duration=process_time,
            )
        except Exception as e:
            # Metrics should never break the application
            logger.debug(f"Metrics recording failed: {e}")
        
        return response
        
    except Exception as e:
        # Record error metric
        process_time = time.time() - start_time
        try:
            from app.utils.metrics import metrics
            metrics.record_request(
                endpoint=request.url.path,
                method=request.method,
                status=500,
                duration=process_time,
            )
        except:
            pass
        raise


# Global exception handler with Sentry integration
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions with proper logging and tracking."""
    # Get request ID if available
    request_id = getattr(request.state, "request_id", "unknown")
    
    # Log error with context
    logger.error(
        f"Unhandled exception [{request_id}]: {request.method} {request.url.path}",
        exc_info=True,
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "client": request.client.host if request.client else "unknown"
        }
    )
    
    # Capture in Sentry if available
    if settings.sentry_dsn:
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(exc)
        except:
            pass
    
    # Return appropriate error response
    error_detail = {
        "error": "internal_server_error",
        "message": "An unexpected error occurred. Please try again later.",
        "request_id": request_id,
    }
    
    # Include error details in non-production
    if settings.environment != "production":
        error_detail["detail"] = str(exc)
        error_detail["type"] = type(exc).__name__
    
    return JSONResponse(
        status_code=500,
        content=error_detail,
    )


# Include routers
app.include_router(auth_router)  # Authentication routes
app.include_router(router)  # Memory routes

# Mount static files
from pathlib import Path
frontend_path = Path(__file__).parent.parent / "frontend"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")


# Root endpoint - Redirect to UI
@app.get("/")
async def root():
    """Root endpoint - serves the UI."""
    ui_path = frontend_path / "index.html"
    return FileResponse(ui_path)


# Auth page endpoint
@app.get("/auth.html")
async def serve_auth():
    """Serve the authentication page."""
    auth_path = frontend_path / "auth.html"
    return FileResponse(auth_path)


# API info endpoint
@app.get(
    "/api",
    tags=["System"],
    summary="API information",
)
async def api_info():
    """API information and available endpoints."""
    return {
        "service": "Long-Form Memory System",
        "version": "1.0.0",
        "environment": settings.environment,
        "status": "running",
        "endpoints": {
            "docs": "/docs" if settings.environment != "production" else "disabled",
            "health": "/health",
            "detailed_health": "/health/detailed",
            "api": "/api/v1",
            "frontend": "/"
        },
        "features": {
            "rate_limiting": settings.rate_limit_enabled,
            "security_headers": settings.security_headers_enabled,
            "error_tracking": bool(settings.sentry_dsn),
        },
        "limits": {
            "rate_limit_per_minute": settings.rate_limit_per_minute,
            "max_context_tokens": settings.max_context_tokens,
        }
    }


# Health check endpoints - Production grade
from app.health import health_checker

@app.get(
    "/health",
    tags=["System"],
    summary="Basic health check",
    description="Quick health check endpoint for load balancers",
)
@app.get("/api/health")
async def health_check():
    """Basic health check endpoint (fast, cached)."""
    health = await health_checker.get_health(include_details=False)
    
    # Return appropriate status code
    status_code = 200 if health.status == "healthy" else 503
    
    return JSONResponse(
        status_code=status_code,
        content={
            "status": health.status,
            "timestamp": health.timestamp.isoformat(),
            "uptime_seconds": round(health.uptime_seconds, 2),
            "version": health.version
        }
    )


@app.get(
    "/health/detailed",
    tags=["System"],
    summary="Detailed health check",
    description="Comprehensive health check with component details (may be slower)",
)
async def health_check_detailed():
    """Detailed health check endpoint with component diagnostics."""
    health = await health_checker.get_health(include_details=True)
    
    # Return appropriate status code
    status_code = 200 if health.status == "healthy" else 503
    
    return JSONResponse(
        status_code=status_code,
        content=health.model_dump()
    )


# Serve UI (alias)
@app.get("/ui")
async def serve_ui():
    """Serve the web UI (alias to root)."""
    ui_path = frontend_path / "index.html"
    return FileResponse(ui_path)


# Startup event - Log system configuration
@app.on_event("startup")
async def log_startup_info():
    """Log important startup information."""
    logger.info("=" * 60)
    logger.info("🧠 LONG-FORM MEMORY SYSTEM - PRODUCTION CONFIGURATION")
    logger.info("=" * 60)
    logger.info(f"Environment: {settings.environment.upper()}")
    logger.info(f"API Port: {settings.api_port}")
    logger.info(f"Database: {settings.postgres_host}:{settings.postgres_port}")
    logger.info(f"Redis: {settings.redis_host}:{settings.redis_port}")
    logger.info(f"LLM Provider: {settings.llm_provider}")
    logger.info(f"Embedding Provider: {settings.embedding_provider}")
    logger.info(f"Rate Limiting: {'ENABLED' if settings.rate_limit_enabled else 'DISABLED'}")
    logger.info(f"Security Headers: {'ENABLED' if settings.security_headers_enabled else 'DISABLED'}")
    logger.info(f"Error Tracking: {'ENABLED (Sentry)' if settings.sentry_dsn else 'DISABLED'}")
    logger.info(f"CORS Origins: {len(settings.cors_origins)} configured")
    logger.info("=" * 60)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.api_port,
        reload=settings.environment == "development",
        log_level=settings.log_level.lower(),
    )
