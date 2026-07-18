"""Async HTTP client for the CodeSmriti RAG API.

This is the single place HTTP and authentication live. Both the MCP server and
the `smriti` CLI are thin presenters over this client: every method returns the
parsed JSON response as a plain dict (the API owns the schema) and raises a
typed error from `smriti_client.errors` on failure. No formatting happens here.
"""

import os
from typing import Any, Literal, Optional

import httpx

from .errors import (
    SmritiAuthError,
    SmritiConfigError,
    SmritiError,
    SmritiNotFoundError,
)

DEFAULT_API_URL = "https://smriti.agsci.com"

# Per-endpoint timeouts (seconds). RAG "ask" calls run an LLM; search and file
# fetches touch the vector index; everything else is cheap metadata.
_FAST = 30.0
_SEARCH = 60.0
_ASK = 120.0

SearchLevel = Literal["symbol", "file", "module", "repo", "doc", "spec"]


class SmritiClient:
    """Client for the CodeSmriti RAG API.

    Config resolution (constructor arg overrides environment):
      - token:    CODESMRITI_TOKEN, then COS_TOKEN (shared CoS token store)
      - base_url: CODESMRITI_API_URL, default https://smriti.agsci.com
    """

    def __init__(
        self,
        token: Optional[str] = None,
        base_url: Optional[str] = None,
    ) -> None:
        self.token = token or os.getenv("CODESMRITI_TOKEN") or os.getenv("COS_TOKEN")
        self.base_url = (
            base_url or os.getenv("CODESMRITI_API_URL") or DEFAULT_API_URL
        ).rstrip("/")

    def _auth_headers(self) -> dict[str, str]:
        if not self.token:
            raise SmritiConfigError(
                "No API token set. Mint a Personal Access Token in the Chief of "
                "Staff web UI (API Tokens panel) and export it as CODESMRITI_TOKEN."
            )
        return {"Authorization": f"Bearer {self.token}"}

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Optional[dict[str, Any]] = None,
        timeout: float = _FAST,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.request(
                    method,
                    url,
                    headers=self._auth_headers(),
                    json=json,
                    timeout=timeout,
                )
        except httpx.RequestError as e:
            raise SmritiError(f"Could not reach {url}: {e}") from e

        if resp.status_code == 401:
            raise SmritiAuthError(
                "Authentication failed. The token is missing, expired, or revoked."
            )
        if resp.status_code == 404:
            raise SmritiNotFoundError(f"Not found: {path}")
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise SmritiError(
                f"API error {resp.status_code} for {path}: {resp.text[:200]}"
            ) from e

        return resp.json()

    # -- Discovery & navigation ------------------------------------------------

    async def repos(self) -> dict[str, Any]:
        """List indexed repositories with document counts and languages."""
        return await self._request("GET", "/api/rag/repos")

    async def structure(
        self,
        repo_id: str,
        path: str = "",
        pattern: Optional[str] = None,
        include_summaries: bool = False,
    ) -> dict[str, Any]:
        """Return the directory listing for a path within a repo."""
        payload: dict[str, Any] = {
            "repo_id": repo_id,
            "path": path,
            "include_summaries": include_summaries,
        }
        if pattern:
            payload["pattern"] = pattern
        return await self._request("POST", "/api/rag/structure", json=payload)

    async def get_file(
        self,
        repo_id: str,
        file_path: str,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> dict[str, Any]:
        """Fetch source code (optionally a line range) from an indexed file."""
        payload: dict[str, Any] = {"repo_id": repo_id, "file_path": file_path}
        if start_line is not None:
            payload["start_line"] = start_line
        if end_line is not None:
            payload["end_line"] = end_line
        return await self._request(
            "POST", "/api/rag/file", json=payload, timeout=_SEARCH
        )

    # -- Retrieval & QA --------------------------------------------------------

    async def search(
        self,
        query: str,
        level: SearchLevel = "file",
        limit: int = 5,
        repo_filter: Optional[str] = None,
        preview: bool = False,
    ) -> dict[str, Any]:
        """Semantic search across code, docs, commits and specs."""
        payload: dict[str, Any] = {
            "query": query,
            "level": level,
            "limit": min(limit, 20),
            "preview": preview,
        }
        if repo_filter:
            payload["repo_filter"] = repo_filter
        return await self._request(
            "POST", "/api/rag/search", json=payload, timeout=_SEARCH
        )

    async def ask_code(self, query: str) -> dict[str, Any]:
        """RAG-powered answer about the codebase, with sources and gaps."""
        return await self._request(
            "POST",
            "/api/rag/ask/code",
            json={"query": query, "persona": "developer"},
            timeout=_ASK,
        )

    async def ask_proposal(self, query: str) -> dict[str, Any]:
        """Business-facing answer from BDR briefs (AgSci capabilities)."""
        return await self._request(
            "POST",
            "/api/rag/ask/proposal",
            json={"query": query, "persona": "sales"},
            timeout=_ASK,
        )

    # -- Dependency graph ------------------------------------------------------

    async def affected_tests(
        self, changed_files: list[str], cluster_id: str
    ) -> dict[str, Any]:
        """Trace which tests should run given a set of changed files."""
        return await self._request(
            "POST",
            "/api/rag/graph/affected-tests",
            json={"changed_files": changed_files, "cluster_id": cluster_id},
        )

    async def criticality(self, module: str, cluster_id: str) -> dict[str, Any]:
        """PageRank-based importance and dependents for a module."""
        return await self._request(
            "POST",
            "/api/rag/graph/criticality",
            json={"module": module, "cluster_id": cluster_id},
        )

    async def graph_info(self, cluster_id: str) -> dict[str, Any]:
        """Summary of a dependency graph (node/edge counts, repo breakdown)."""
        return await self._request(
            "POST", "/api/rag/graph/info", json={"cluster_id": cluster_id}
        )
