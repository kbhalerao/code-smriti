"""The process's shared httpx client, so calls reuse connections."""

from typing import Optional

import httpx
from loguru import logger

_http_client: Optional[httpx.AsyncClient] = None


def get_http_client() -> httpx.AsyncClient:
    """Get or create the shared HTTP client (singleton)."""
    global _http_client
    if _http_client is None:
        logger.info("Creating shared httpx AsyncClient")
        _http_client = httpx.AsyncClient(
            timeout=120.0,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
        )
    return _http_client


async def close_http_client() -> None:
    """Close the shared client on shutdown."""
    global _http_client
    if _http_client is not None:
        await _http_client.aclose()
        _http_client = None
        logger.info("Closed shared HTTP client")
