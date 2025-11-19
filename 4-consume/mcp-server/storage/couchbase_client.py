"""
Couchbase Client for TotalRecall
Handles storage and retrieval of code chunks, notes, and vector search
"""

from typing import List, Dict, Optional
from datetime import timedelta
from loguru import logger

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions, QueryOptions
from couchbase.exceptions import DocumentNotFoundException

from config import settings


class CouchbaseClient:
    """
    Client for interacting with Couchbase
    Handles vector search, document storage, and queries
    """

    def __init__(self):
        """Initialize Couchbase connection"""
        logger.info(f"Connecting to Couchbase at {settings.couchbase_host}:{settings.couchbase_port}")

        # Create authenticator
        auth = PasswordAuthenticator(
            settings.couchbase_username,
            settings.couchbase_password
        )

        # Connect to cluster
        connection_string = f"couchbase://{settings.couchbase_host}"

        self.cluster = Cluster(
            connection_string,
            ClusterOptions(auth)
        )

        # Wait for cluster to be ready
        self.cluster.wait_until_ready(timedelta(seconds=10))

        # Get bucket
        self.bucket = self.cluster.bucket(settings.couchbase_bucket)
        self.collection = self.bucket.default_collection()

        logger.info(f"✓ Connected to Couchbase bucket: {settings.couchbase_bucket}")

    def upsert(self, doc_id: str, document: Dict) -> None:
        """
        Insert or update a document

        Args:
            doc_id: Document ID
            document: Document content
        """
        try:
            self.collection.upsert(doc_id, document)
            logger.debug(f"Upserted document: {doc_id}")
        except Exception as e:
            logger.error(f"Error upserting document {doc_id}: {e}")
            raise

    def batch_upsert(self, documents: List[Dict]) -> None:
        """
        Batch insert/update documents

        Args:
            documents: List of documents with 'chunk_id' field
        """
        try:
            for doc in documents:
                doc_id = doc.get("chunk_id") or doc.get("note_id")
                if doc_id:
                    self.collection.upsert(doc_id, doc)

            logger.info(f"✓ Batch upserted {len(documents)} documents")
        except Exception as e:
            logger.error(f"Error in batch upsert: {e}")
            raise

    def get(self, doc_id: str) -> Optional[Dict]:
        """
        Retrieve a document by ID

        Args:
            doc_id: Document ID

        Returns:
            Document content or None if not found
        """
        try:
            result = self.collection.get(doc_id)
            return result.content_as[dict]
        except DocumentNotFoundException:
            logger.warning(f"Document not found: {doc_id}")
            return None
        except Exception as e:
            logger.error(f"Error retrieving document {doc_id}: {e}")
            return None

    def query(self, n1ql_query: str, **kwargs) -> List[Dict]:
        """
        Execute a N1QL query

        Args:
            n1ql_query: N1QL query string
            **kwargs: Query parameters

        Returns:
            List of result documents
        """
        try:
            result = self.cluster.query(
                n1ql_query,
                QueryOptions(named_parameters=kwargs)
            )

            return [row for row in result]
        except Exception as e:
            logger.error(f"Error executing query: {e}")
            return []

    def search_by_repo(self, repo_id: str, limit: int = 100) -> List[Dict]:
        """
        Get all chunks for a repository

        Args:
            repo_id: Repository ID
            limit: Maximum results

        Returns:
            List of code chunks
        """
        query = f"""
            SELECT *
            FROM `{settings.couchbase_bucket}`
            WHERE repo_id = $repo_id
            LIMIT $limit
        """

        return self.query(query, repo_id=repo_id, limit=limit)

    def search_by_language(self, language: str, limit: int = 100) -> List[Dict]:
        """
        Get all chunks for a programming language

        Args:
            language: Programming language
            limit: Maximum results

        Returns:
            List of code chunks
        """
        query = f"""
            SELECT *
            FROM `{settings.couchbase_bucket}`
            WHERE metadata.language = $language
            LIMIT $limit
        """

        return self.query(query, language=language, limit=limit)

    def search_by_hashtags(self, hashtags: List[str], content_type: str = "all") -> List[Dict]:
        """
        Search for documents by hashtags

        Args:
            hashtags: List of hashtags to search
            content_type: Type filter (code, note, all)

        Returns:
            List of matching documents
        """
        # Build query with hashtag array matching
        hashtag_conditions = " OR ".join([
            f"ANY tag IN hashtags SATISFIES tag = '{tag}' END"
            for tag in hashtags
        ])

        type_condition = ""
        if content_type == "code":
            type_condition = " AND type = 'code_chunk'"
        elif content_type == "note":
            type_condition = " AND type = 'note'"

        query = f"""
            SELECT *
            FROM `{settings.couchbase_bucket}`
            WHERE ({hashtag_conditions}){type_condition}
        """

        return self.query(query)

    def list_repositories(self) -> List[Dict]:
        """
        Get list of all indexed repositories with statistics

        Returns:
            List of repositories with metadata
        """
        query = f"""
            SELECT
                repo_id,
                COUNT(*) as total_chunks,
                ARRAY_AGG(DISTINCT metadata.language) as languages,
                MAX(created_at) as last_indexed
            FROM `{settings.couchbase_bucket}`
            WHERE type = 'code_chunk'
            GROUP BY repo_id
        """

        return self.query(query)

    def get_system_stats(self) -> Dict:
        """
        Get system-wide statistics

        Returns:
            Dictionary with statistics
        """
        try:
            # Count documents by type
            stats_query = f"""
                SELECT
                    type,
                    COUNT(*) as count
                FROM `{settings.couchbase_bucket}`
                GROUP BY type
            """

            type_counts = self.query(stats_query)

            # Count repositories
            repo_query = f"""
                SELECT COUNT(DISTINCT repo_id) as repo_count
                FROM `{settings.couchbase_bucket}`
                WHERE type = 'code_chunk'
            """

            repo_result = self.query(repo_query)
            repo_count = repo_result[0].get("repo_count", 0) if repo_result else 0

            # Get languages
            lang_query = f"""
                SELECT ARRAY_AGG(DISTINCT metadata.language) as languages
                FROM `{settings.couchbase_bucket}`
                WHERE type = 'code_chunk'
            """

            lang_result = self.query(lang_query)
            languages = lang_result[0].get("languages", []) if lang_result else []

            # Build stats dictionary
            stats = {
                "total_repositories": repo_count,
                "total_code_chunks": 0,
                "total_notes": 0,
                "total_documents": 0,
                "indexed_languages": [lang for lang in languages if lang],
                "status": "healthy"
            }

            for item in type_counts:
                if item["type"] == "code_chunk":
                    stats["total_code_chunks"] = item["count"]
                elif item["type"] == "note":
                    stats["total_notes"] = item["count"]
                elif item["type"] == "document":
                    stats["total_documents"] = item["count"]

            return stats

        except Exception as e:
            logger.error(f"Error getting system stats: {e}")
            return {"status": "error", "error": str(e)}

    def vector_search(
        self,
        query_vector: List[float],
        filters: Optional[Dict] = None,
        limit: int = 10
    ) -> List[Dict]:
        """
        Perform vector similarity search using Couchbase FTS

        Args:
            query_vector: Query embedding vector (768 dimensions)
            filters: Optional filters (repo_id, language, chunk_type, doc_type)
            limit: Maximum results

        Returns:
            List of similar documents with similarity scores
        """
        import requests
        from requests.auth import HTTPBasicAuth

        try:
            # Build FTS query
            search_request = {
                "query": {
                    "match_none": {}
                },
                "knn": [
                    {
                        "field": "embedding",
                        "vector": query_vector,
                        "k": limit
                    }
                ],
                "size": limit,
                "fields": ["*"]
            }

            # Add filters if provided
            filter_clauses = []
            if filters:
                if filters.get("repo_id"):
                    filter_clauses.append({
                        "field": "repo_id",
                        "match": filters["repo_id"]
                    })
                if filters.get("language"):
                    filter_clauses.append({
                        "field": "language",
                        "match": filters["language"]
                    })
                if filters.get("chunk_type"):
                    filter_clauses.append({
                        "field": "chunk_type",
                        "match": filters["chunk_type"]
                    })
                if filters.get("doc_type"):
                    filter_clauses.append({
                        "field": "doc_type",
                        "match": filters["doc_type"]
                    })

            if filter_clauses:
                if len(filter_clauses) == 1:
                    search_request["query"] = filter_clauses[0]
                else:
                    search_request["query"] = {
                        "conjuncts": filter_clauses
                    }

            # Perform search via FTS REST API
            url = f"http://{settings.couchbase_host}:8094/api/index/code_vector_index/query"
            auth = HTTPBasicAuth(settings.couchbase_username, settings.couchbase_password)

            response = requests.post(
                url,
                json=search_request,
                auth=auth,
                headers={"Content-Type": "application/json"},
                timeout=10.0
            )
            response.raise_for_status()

            result = response.json()

            # Parse results
            hits = result.get("hits", [])
            results = []

            for hit in hits:
                doc_id = hit.get("id")
                score = hit.get("score", 0.0)

                # Get full document from Couchbase
                try:
                    doc_result = self.collection.get(doc_id)
                    doc = doc_result.content_as[dict]

                    # Remove embedding from result to reduce size
                    doc_copy = doc.copy()
                    doc_copy.pop("embedding", None)

                    results.append({
                        "chunk_id": doc_id,
                        "similarity_score": score,
                        **doc_copy
                    })
                except DocumentNotFoundException:
                    logger.warning(f"Document {doc_id} not found")
                    continue

            logger.info(f"Vector search returned {len(results)} results")
            return results

        except Exception as e:
            logger.error(f"Error in vector_search: {e}")
            return []

    def close(self):
        """Close the Couchbase connection"""
        # Couchbase Python SDK handles connection pooling
        # No explicit close needed
        logger.info("Couchbase client closed")
