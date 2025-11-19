"""
CodeSmriti FastAPI application.

Multi-tenant code search API with authentication, repository management,
and vector search capabilities. Designed for PydanticAI integration.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from .config import settings
from .database import close_cluster

# Import routers (will be created next)
from .auth.routes import router as auth_router
from .users.routes import router as users_router
from .repos.routes import router as repos_router
from .jobs.routes import router as jobs_router
from .search.routes import router as search_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    logger.info(f"Starting {settings.app_name}")
    logger.info(f"Log level: {settings.log_level}")
    logger.info(f"CORS origins: {settings.cors_origins}")

    yield

    # Shutdown
    logger.info("Shutting down gracefully...")
    await close_cluster()
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
    lifespan=lifespan,
)

# CORS middleware for Cloudflare origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

# Register routers
app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users_router, prefix="/api/user", tags=["User"])
app.include_router(repos_router, prefix="/api/repos", tags=["Repositories"])
app.include_router(jobs_router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(search_router, prefix="/api/search", tags=["Search"])


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {
        "status": "healthy",
        "service": settings.app_name,
        "version": settings.api_version,
    }


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": f"Welcome to {settings.app_name}",
        "version": settings.api_version,
        "docs": "/docs",
    }
