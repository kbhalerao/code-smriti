"""
Search Tools for MCP
Handles vector search and similarity search across code and documentation
"""

import json
from typing import Optional, List
from loguru import logger
from sentence_transformers import SentenceTransformer
import numpy as np

from config import settings
# Couchbase will be implemented in the storage module
# from storage.couchbase_client import CouchbaseClient


class SearchTools:
    """
    MCP tools for searching code and documentation
    """

    def __init__(self):
        """Initialize search tools with embedding model"""
        logger.info(f"Loading embedding model: {settings.embedding_model} (revision: {settings.embedding_model_revision})")
        self.embedding_model = SentenceTransformer(
            settings.embedding_model,
            trust_remote_code=True,
            revision=settings.embedding_model_revision
        )
        logger.info("Embedding model loaded successfully")

        # TODO: Initialize Couchbase client
        # self.db = CouchbaseClient()

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

            # Generate query embedding with task instruction prefix
            query_with_prefix = f"search_query: {query}"
            query_embedding = self.embedding_model.encode(query_with_prefix, convert_to_tensor=False)
            query_vector = query_embedding.tolist()

            # TODO: Perform vector search in Couchbase with filters
            # For now, return stub data
            results = [
                {
                    "repo_id": repo or "example/repo",
                    "file_path": "src/example.py",
                    "chunk_type": "function",
                    "code_text": "def example_function():\n    pass",
                    "metadata": {
                        "language": language or "python",
                        "function_name": "example_function",
                        "start_line": 10,
                        "end_line": 11
                    },
                    "similarity_score": 0.95
                }
            ]

            return json.dumps({
                "query": query,
                "total_results": len(results),
                "results": results
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

            # Generate embedding for the code snippet with task instruction prefix
            snippet_with_prefix = f"search_query: {code_snippet}"
            snippet_embedding = self.embedding_model.encode(snippet_with_prefix, convert_to_tensor=False)
            snippet_vector = snippet_embedding.tolist()

            # TODO: Vector search for similar code in Couchbase
            # For now, return stub data
            results = [
                {
                    "repo_id": "example/repo",
                    "file_path": "src/similar.py",
                    "code_text": code_snippet,
                    "similarity_score": 0.98,
                    "metadata": {
                        "language": language or "python",
                        "function_name": "similar_function"
                    }
                }
            ]

            return json.dumps({
                "original_snippet": code_snippet[:100] + "...",
                "total_results": len(results),
                "results": results
            }, indent=2)

        except Exception as e:
            logger.error(f"Error in find_similar: {e}")
            return json.dumps({"error": str(e)})
