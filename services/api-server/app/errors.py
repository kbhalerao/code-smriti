"""Exception handlers registered on the app in app.main."""

from fastapi import Request
from fastapi.responses import JSONResponse
from loguru import logger

from app.rag.embedding import EMBEDDING_UNAVAILABLE_DETAIL, EmbeddingServiceError


async def embedding_unavailable_handler(
    request: Request, exc: EmbeddingServiceError
) -> JSONResponse:
    logger.error(f"{request.url.path}: {exc}")
    return JSONResponse(status_code=503, content={"detail": EMBEDDING_UNAVAILABLE_DETAIL})
