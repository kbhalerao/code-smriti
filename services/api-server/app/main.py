"""
CodeSmriti FastAPI application.

Multi-tenant code search API with authentication, repository management,
and vector search capabilities. Designed for PydanticAI integration.
"""

from contextlib import asynccontextmanager
import sys

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from .config import settings
from .database import close_cluster
from .chat.pydantic_rag_agent import close_shared_resources, get_http_client
from .errors import embedding_unavailable_handler
from .rag.embedding import EmbeddingServiceError, assert_corpus_matches, probe_embedder


# Configure logging
logger.remove()  # Remove default handler

# Add file handler for all logs
logger.add(
    "logs/api-server.log",
    rotation="100 MB",
    retention="7 days",
    level="DEBUG",
    format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}"
)

# Add console handler for warnings and errors only
logger.add(
    sys.stderr,
    level="WARNING",
    format="<level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan> - <level>{message}</level>"
)

# Import routers (will be created next)
from .auth.routes import router as auth_router
from .users.routes import router as users_router
from .repos.routes import router as repos_router
from .jobs.routes import router as jobs_router
from .search.routes import router as search_router
from .chat.routes import router as chat_router
from .inspect.routes import router as inspect_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    logger.info(f"Starting {settings.app_name}")
    logger.info(f"Log level: {settings.log_level}")
    logger.info(f"CORS origins: {settings.cors_origins}")

    get_http_client()
    logger.info("✓ HTTP client ready")

    # Loud, not fatal: a mismatch means results are wrong while the service is
    # otherwise healthy, and a dimension check cannot catch it — 768 truncated
    # from Qwen is the same shape nomic produced and a different space.
    #
    # Two independent checks, so that neither failing can hide the other. Queries
    # are embedded by Ollama, so there is nothing to pre-load; the probe is what
    # exercises that path.
    try:
        from .dependencies import get_db
        corpus_problem = await assert_corpus_matches(
            get_db(), loaded_model_name=settings.embedding_model_name
        )
    except Exception as e:
        corpus_problem = f"Could not verify the embedding manifest: {e}"
    probe_problem = await probe_embedder(settings.embedding_model_name)

    for problem in filter(None, (corpus_problem, probe_problem)):
        logger.error(f"EMBEDDING PROBLEM: {problem}")
    if not corpus_problem and not probe_problem:
        logger.info("✓ Embedding convention matches the corpus manifest; probe answered")

    yield

    # Shutdown
    logger.info("Shutting down gracefully...")
    await close_shared_resources()  # Close RAG agent shared resources
    await close_cluster()  # Close Couchbase connection
    logger.info("Shutdown complete")


# Create FastAPI app
app = FastAPI(
    title=settings.app_name,
    version=settings.api_version,
    lifespan=lifespan,
)

app.add_exception_handler(EmbeddingServiceError, embedding_unavailable_handler)

# CORS middleware for Cloudflare origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-User-ID"],
)

# Register routers
app.include_router(auth_router, prefix="/api/auth", tags=["Authentication"])
app.include_router(users_router, prefix="/api/user", tags=["User"])
app.include_router(repos_router, prefix="/api/repos", tags=["Repositories"])
app.include_router(jobs_router, prefix="/api/jobs", tags=["Jobs"])
app.include_router(search_router, prefix="/api/search", tags=["Search"])
app.include_router(chat_router, prefix="/api")
app.include_router(inspect_router, prefix="/api/inspect", tags=["Inspect"])


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
