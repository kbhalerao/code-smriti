"""CodeSmriti API client.

A thin async client for the CodeSmriti RAG API, shared by the MCP server and
the `smriti` CLI so HTTP and authentication live in exactly one place.

Usage:
    from smriti_client import SmritiClient

    client = SmritiClient()  # reads CODESMRITI_TOKEN / CODESMRITI_API_URL
    data = await client.search("how does auth work", level="file")
"""

from .client import DEFAULT_API_URL, SearchLevel, SmritiClient
from .errors import (
    SmritiAuthError,
    SmritiConfigError,
    SmritiError,
    SmritiNotFoundError,
)

__all__ = [
    "SmritiClient",
    "SearchLevel",
    "DEFAULT_API_URL",
    "SmritiError",
    "SmritiConfigError",
    "SmritiAuthError",
    "SmritiNotFoundError",
]
