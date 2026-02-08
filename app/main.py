"""Main FastAPI application."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import get_settings
from app.database import db_manager

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    # Startup
    logger.info("Starting Long-Form Memory System")
    try:
        await db_manager.initialize()
        logger.info("Database connections established")
    except Exception as e:
        logger.error(f"Failed to initialize databases: {e}")
        raise

    yield

    # Shutdown
    logger.info("Shutting down Long-Form Memory System")
    await db_manager.close()
    logger.info("Database connections closed")


# Create FastAPI app
app = FastAPI(
    title="Long-Form Memory System",
    description="Production-grade memory system for LLM applications",
    version="1.0.0",
    lifespan=lifespan,
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# Request timing middleware
@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Add processing time header to responses."""
    import time
    
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    
    # Record metrics
    from app.utils.metrics import metrics
    metrics.record_request(
        endpoint=request.url.path,
        method=request.method,
        status=response.status_code,
        duration=process_time,
    )
    
    return response


# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Handle uncaught exceptions."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={
            "detail": "Internal server error",
            "error": str(exc) if settings.environment != "production" else None,
        },
    )


# Include routers
app.include_router(router)

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


# API info endpoint
@app.get("/api")
async def api_info():
    """API information endpoint."""
    return {
        "service": "Long-Form Memory System",
        "version": "1.0.0",
        "status": "running",
        "docs": "/docs",
        "frontend": "/"
    }


# Serve UI (alias)
@app.get("/ui")
async def serve_ui():
    """Serve the web UI (alias to root)."""
    ui_path = frontend_path / "index.html"
    return FileResponse(ui_path)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=settings.api_port,
        reload=settings.environment == "development",
        log_level=settings.log_level.lower(),
    )
