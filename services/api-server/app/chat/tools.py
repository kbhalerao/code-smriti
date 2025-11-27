"""Pydantic AI tools for RAG-enriched chat"""
from typing import List, Optional
from pydantic import BaseModel, Field
from app.database.couchbase_client import CouchbaseClient
from loguru import logger


class SearchResult(BaseModel):
    """Result from code search"""
    content: str = Field(description="The code content")
    repo_id: str = Field(description="Repository identifier (owner/repo)")
    file_path: str = Field(description="File path in the repository")
    language: str = Field(description="Programming language")
    score: float = Field(description="Relevance score")


class CodeSearchTool:
    """Tool for searching code using Couchbase vector search"""

    def __init__(self, db: CouchbaseClient, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id

    async def search_code(
        self,
        query: str,
        limit: int = 5,
        repo: Optional[str] = None,
        language: Optional[str] = None
    ) -> List[SearchResult]:
        """
        Search for code across indexed repositories using semantic search.

        Args:
            query: Natural language or code search query
            limit: Maximum number of results to return (default: 5)
            repo: Optional repository filter (format: owner/repo)
            language: Optional programming language filter (e.g., python, javascript)

        Returns:
            List of relevant code chunks with metadata
        """
        logger.info(f"Searching code: query='{query}', limit={limit}, repo={repo}, lang={language}")

        try:
            # Get tenant collection
            collection = self.db.get_tenant_collection(self.tenant_id)

            # Build N1QL query with filters
            where_clauses = ["repo_id IS NOT MISSING"]

            if repo:
                where_clauses.append(f"repo_id = '{repo}'")

            if language:
                where_clauses.append(f"language = '{language}'")

            where_clause = " AND ".join(where_clauses)

            # For now, doing text search without embeddings
            # TODO: Add vector search with embeddings once indexes are ready
            query_sql = f"""
                SELECT content, repo_id, file_path, language, 0.5 as score
                FROM `{self.tenant_id}`
                WHERE {where_clause}
                LIMIT {limit}
            """

            logger.debug(f"Executing query: {query_sql}")

            result = self.db.cluster.query(query_sql)
            results = [
                SearchResult(
                    content=row.get('content', ''),
                    repo_id=row.get('repo_id', ''),
                    file_path=row.get('file_path', ''),
                    language=row.get('language', ''),
                    score=row.get('score', 0.0)
                )
                for row in result
            ]

            logger.info(f"Found {len(results)} results")
            return results

        except Exception as e:
            logger.error(f"Code search failed: {e}")
            return []


class ListReposTool:
    """Tool for listing available repositories"""

    def __init__(self, db: CouchbaseClient, tenant_id: str):
        self.db = db
        self.tenant_id = tenant_id

    async def list_repos(self) -> List[str]:
        """
        List all repositories available in the tenant's bucket.

        Returns:
            List of repository identifiers (format: owner/repo)
        """
        logger.info(f"Listing repositories for tenant {self.tenant_id}")

        try:
            collection = self.db.get_tenant_collection(self.tenant_id)

            # Query for distinct repo_ids
            query = f"""
                SELECT DISTINCT repo_id
                FROM `{self.tenant_id}`
                WHERE repo_id IS NOT MISSING
                ORDER BY repo_id
            """

            result = self.db.cluster.query(query)
            repos = [row['repo_id'] for row in result]

            logger.info(f"Found {len(repos)} repositories")
            return repos

        except Exception as e:
            logger.error(f"List repos failed: {e}")
            return []
