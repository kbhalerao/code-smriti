"""
Search Tools for MCP
Handles vector search and similarity search across code and documentation
"""

import json
import os
from typing import Optional, List
from loguru import logger
import httpx
import numpy as np

from config import settings
from storage.couchbase_client import CouchbaseClient


class SearchTools:
    """
    MCP tools for searching code and documentation
    """

    def __init__(self):
        """Initialize search tools with Ollama API client and Couchbase"""
        self.ollama_host = settings.ollama_host or os.getenv("OLLAMA_HOST", "http://host.docker.internal:11434")
        self.model_name = "nomic-embed-text"

        logger.info(f"Initializing search tools with Ollama at {self.ollama_host}")
        logger.info(f"Using embedding model: {self.model_name}")

        # Initialize Couchbase client
        try:
            self.db = CouchbaseClient()
            logger.info("✓ Couchbase client initialized for vector search")
        except Exception as e:
            logger.error(f"Failed to initialize Couchbase client: {e}")
            self.db = None

    async def search_code(
        self,
        query: str,
        repo: Optional[str] = None,
        language: Optional[str] = None,
        hashtags: Optional[List[str]] = None,
        limit: int = 10
    ) -> str:
        """
        Search for code using vector similarity and filters

        Args:
            query: Search query (natural language or code snippet)
            repo: Filter by repository (owner/repo format)
            language: Filter by programming language
            hashtags: Filter by hashtags
            limit: Maximum number of results

        Returns:
            JSON string with search results
        """
        try:
            logger.info(f"Search request: query='{query}', repo={repo}, language={language}")

            # Check if Couchbase is available
            if not self.db:
                return json.dumps({"error": "Couchbase not available"})

            # Generate query embedding with task instruction prefix via Ollama API
            query_with_prefix = f"search_query: {query}"
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.ollama_host}/api/embeddings",
                    json={"model": self.model_name, "prompt": query_with_prefix},
                    timeout=30.0
                )
                response.raise_for_status()
                query_vector = response.json().get("embedding", [])

            # Perform vector search in Couchbase with filters
            filters = {}
            if repo:
                filters["repo_id"] = repo
            if language:
                filters["language"] = language

            results = self.db.vector_search(
                query_vector=query_vector,
                filters=filters if filters else None,
                limit=limit
            )

            # Format results for output
            formatted_results = []
            for result in results:
                # Extract fields based on document type
                formatted = {
                    "chunk_id": result.get("chunk_id"),
                    "similarity_score": result.get("similarity_score"),
                    "repo_id": result.get("repo_id"),
                    "file_path": result.get("file_path"),
                    "type": result.get("type")
                }

                if result.get("type") == "code_chunk":
                    formatted.update({
                        "chunk_type": result.get("chunk_type"),
                        "code_text": result.get("code_text"),
                        "language": result.get("language"),
                        "metadata": result.get("metadata", {})
                    })
                elif result.get("type") == "document":
                    formatted.update({
                        "doc_type": result.get("doc_type"),
                        "content": result.get("content", "")[:500],  # Limit content length
                        "metadata": result.get("metadata", {})
                    })

                formatted_results.append(formatted)

            return json.dumps({
                "query": query,
                "total_results": len(formatted_results),
                "results": formatted_results
            }, indent=2)

        except Exception as e:
            logger.error(f"Error in search_code: {e}")
            return json.dumps({"error": str(e)})

    async def find_similar(
        self,
        code_snippet: str,
        language: Optional[str] = None,
        limit: int = 5
    ) -> str:
        """
        Find code similar to a given snippet

        Args:
            code_snippet: Code snippet to find similar implementations
            language: Programming language filter
            limit: Maximum number of results

        Returns:
            JSON string with similar code chunks
        """
        try:
            logger.info(f"Finding similar code (language={language}, limit={limit})")

            # Check if Couchbase is available
            if not self.db:
                return json.dumps({"error": "Couchbase not available"})

            # Generate embedding for the code snippet with task instruction prefix via Ollama API
            snippet_with_prefix = f"search_query: {code_snippet}"
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.ollama_host}/api/embeddings",
                    json={"model": self.model_name, "prompt": snippet_with_prefix},
                    timeout=30.0
                )
                response.raise_for_status()
                snippet_vector = response.json().get("embedding", [])

            # Vector search for similar code in Couchbase
            filters = {}
            if language:
                filters["language"] = language

            results = self.db.vector_search(
                query_vector=snippet_vector,
                filters=filters if filters else None,
                limit=limit
            )

            # Format results
            formatted_results = []
            for result in results:
                if result.get("type") == "code_chunk":
                    formatted_results.append({
                        "chunk_id": result.get("chunk_id"),
                        "similarity_score": result.get("similarity_score"),
                        "repo_id": result.get("repo_id"),
                        "file_path": result.get("file_path"),
                        "code_text": result.get("code_text"),
                        "language": result.get("language"),
                        "chunk_type": result.get("chunk_type"),
                        "metadata": result.get("metadata", {})
                    })

            return json.dumps({
                "original_snippet": code_snippet[:100] + ("..." if len(code_snippet) > 100 else ""),
                "total_results": len(formatted_results),
                "results": formatted_results
            }, indent=2)

        except Exception as e:
            logger.error(f"Error in find_similar: {e}")
            return json.dumps({"error": str(e)})
