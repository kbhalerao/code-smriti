"""
Couchbase Client for storing code chunks and embeddings
Handles connection, bucket operations, and vector storage
"""

from typing import List, Union, Dict, Any
from datetime import timedelta
import time

from loguru import logger
from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions, UpsertOptions
from couchbase.exceptions import (
    CouchbaseException,
    BucketNotFoundException,
    DocumentNotFoundException
)

from config import WorkerConfig
from parsers.code_parser import CodeChunk
from parsers.document_parser import DocumentChunk

config = WorkerConfig()


class CouchbaseClient:
    """
    Client for interacting with Couchbase
    Stores code chunks with embeddings for vector search
    """

    def __init__(self):
        """Initialize connection to Couchbase"""
        logger.info(f"Connecting to Couchbase at {config.couchbase_host}:{config.couchbase_port}")

        # Create cluster connection
        connection_string = f"couchbase://{config.couchbase_host}"
        auth = PasswordAuthenticator(config.couchbase_username, config.couchbase_password)

        try:
            self.cluster = Cluster(
                connection_string,
                ClusterOptions(auth)
            )

            # Wait for cluster to be ready
            self.cluster.wait_until_ready(timedelta(seconds=10))

            # Get bucket
            self.bucket = self.cluster.bucket(config.couchbase_bucket)
            self.collection = self.bucket.default_collection()

            logger.info(f"✓ Connected to Couchbase bucket: {config.couchbase_bucket}")

        except BucketNotFoundException:
            logger.error(f"Bucket '{config.couchbase_bucket}' not found. Please create it first.")
            raise
        except CouchbaseException as e:
            logger.error(f"Failed to connect to Couchbase: {e}")
            raise

    def upsert_chunk(self, chunk: Union[CodeChunk, DocumentChunk]) -> bool:
        """
        Insert or update a single chunk

        Args:
            chunk: CodeChunk or DocumentChunk object

        Returns:
            True if successful
        """
        try:
            doc = chunk.to_dict()
            self.collection.upsert(chunk.chunk_id, doc)
            return True
        except CouchbaseException as e:
            logger.error(f"Error upserting chunk {chunk.chunk_id}: {e}")
            return False

    async def batch_upsert(
        self,
        chunks: List[Union[CodeChunk, DocumentChunk]],
        batch_size: int = 100
    ) -> Dict[str, int]:
        """
        Batch upsert chunks to Couchbase

        Args:
            chunks: List of CodeChunk or DocumentChunk objects
            batch_size: Number of documents to upsert at once

        Returns:
            Dictionary with success and failure counts
        """
        if not chunks:
            logger.warning("No chunks to upsert")
            return {"success": 0, "failed": 0}

        logger.info(f"Upserting {len(chunks)} chunks to Couchbase (batch_size={batch_size})")

        success_count = 0
        failed_count = 0
        total_batches = (len(chunks) + batch_size - 1) // batch_size

        try:
            for i in range(0, len(chunks), batch_size):
                batch = chunks[i:i + batch_size]
                batch_num = i // batch_size + 1

                # Log progress for large batches
                if total_batches > 10 and (batch_num % 10 == 0 or batch_num == 1):
                    logger.info(f"Upserting batch {batch_num}/{total_batches} ({(batch_num/total_batches)*100:.1f}%)")

                # Upsert each chunk in the batch
                for chunk in batch:
                    if self.upsert_chunk(chunk):
                        success_count += 1
                    else:
                        failed_count += 1

            logger.info(f"✓ Upsert complete: {success_count} succeeded, {failed_count} failed")

            return {
                "success": success_count,
                "failed": failed_count
            }

        except Exception as e:
            logger.error(f"Error during batch upsert: {e}", exc_info=True)
            return {
                "success": success_count,
                "failed": failed_count
            }

    def get_chunk(self, chunk_id: str) -> Dict[str, Any]:
        """
        Retrieve a chunk by ID

        Args:
            chunk_id: Chunk identifier

        Returns:
            Chunk document or None if not found
        """
        try:
            result = self.collection.get(chunk_id)
            return result.content_as[dict]
        except DocumentNotFoundException:
            logger.warning(f"Chunk not found: {chunk_id}")
            return None
        except CouchbaseException as e:
            logger.error(f"Error retrieving chunk {chunk_id}: {e}")
            return None

    def get_file_chunks(self, repo_id: str, file_path: str) -> List[Dict[str, Any]]:
        """
        Get all chunks for a specific file in a repository

        Args:
            repo_id: Repository identifier
            file_path: File path within repository

        Returns:
            List of chunk documents
        """
        try:
            query = f"""
                SELECT META().id as chunk_id, *
                FROM `{config.couchbase_bucket}`
                WHERE repo_id = $repo_id AND file_path = $file_path
            """

            result = self.cluster.query(query, repo_id=repo_id, file_path=file_path)
            return list(result)
        except CouchbaseException as e:
            logger.error(f"Error retrieving chunks for {repo_id}/{file_path}: {e}")
            return []

    def delete_file_chunks(self, repo_id: str, file_path: str) -> int:
        """
        Delete all chunks for a specific file
        Used when file is updated to remove old chunks

        Args:
            repo_id: Repository identifier
            file_path: File path within repository

        Returns:
            Number of chunks deleted
        """
        try:
            # First count
            count_query = f"""
                SELECT COUNT(*) as count
                FROM `{config.couchbase_bucket}`
                WHERE repo_id = $repo_id AND file_path = $file_path
            """
            count_result = self.cluster.query(count_query, repo_id=repo_id, file_path=file_path)
            count_rows = list(count_result)
            deleted_count = count_rows[0]['count'] if count_rows else 0

            if deleted_count == 0:
                return 0

            # Delete
            delete_query = f"""
                DELETE FROM `{config.couchbase_bucket}`
                WHERE repo_id = $repo_id AND file_path = $file_path
            """
            self.cluster.query(delete_query, repo_id=repo_id, file_path=file_path)

            logger.debug(f"Deleted {deleted_count} chunks for {repo_id}/{file_path}")
            return deleted_count
        except CouchbaseException as e:
            logger.error(f"Error deleting chunks for {repo_id}/{file_path}: {e}")
            return 0

    def check_file_commit(self, repo_id: str, file_path: str) -> str:
        """
        Check the commit hash of the most recent chunk for a file
        Used to determine if file has changed

        Args:
            repo_id: Repository identifier
            file_path: File path within repository

        Returns:
            Commit hash or empty string if file not found
        """
        try:
            query = f"""
                SELECT metadata.commit_hash
                FROM `{config.couchbase_bucket}`
                WHERE repo_id = $repo_id AND file_path = $file_path
                LIMIT 1
            """

            result = self.cluster.query(query, repo_id=repo_id, file_path=file_path)
            rows = list(result)

            if rows and 'commit_hash' in rows[0]:
                return rows[0]['commit_hash']
            return ""
        except CouchbaseException as e:
            logger.error(f"Error checking commit for {repo_id}/{file_path}: {e}")
            return ""

    def delete_repo_chunks(self, repo_id: str) -> int:
        """
        Delete all chunks for a repository
        Useful for re-indexing a repo

        Args:
            repo_id: Repository identifier

        Returns:
            Number of chunks deleted
        """
        try:
            # First count how many chunks exist
            count_query = f"""
                SELECT COUNT(*) as count
                FROM `{config.couchbase_bucket}`
                WHERE repo_id = $repo_id
            """
            count_result = self.cluster.query(count_query, repo_id=repo_id)
            count_rows = list(count_result)
            deleted_count = count_rows[0]['count'] if count_rows else 0

            if deleted_count == 0:
                logger.info(f"No chunks found for repo: {repo_id}")
                return 0

            # Delete the chunks
            delete_query = f"""
                DELETE FROM `{config.couchbase_bucket}`
                WHERE repo_id = $repo_id
            """
            self.cluster.query(delete_query, repo_id=repo_id)

            logger.info(f"Deleted {deleted_count} chunks for repo: {repo_id}")
            return deleted_count

        except CouchbaseException as e:
            logger.error(f"Error deleting chunks for repo {repo_id}: {e}")
            return 0

    def get_stats(self) -> Dict[str, Any]:
        """
        Get statistics about stored chunks

        Returns:
            Dictionary with statistics
        """
        try:
            # Query to get counts by type and repo
            query = f"""
                SELECT
                    COUNT(*) as total_chunks,
                    COUNT(DISTINCT repo_id) as total_repos,
                    type,
                    repo_id
                FROM `{config.couchbase_bucket}`
                GROUP BY type, repo_id
            """

            result = self.cluster.query(query)
            stats = list(result)

            total_chunks = sum(s['total_chunks'] for s in stats)
            total_repos = len(set(s['repo_id'] for s in stats))

            return {
                "total_chunks": total_chunks,
                "total_repos": total_repos,
                "by_repo": stats
            }

        except CouchbaseException as e:
            logger.error(f"Error getting stats: {e}")
            return {}

    def close(self):
        """Close the Couchbase connection"""
        if hasattr(self, 'cluster'):
            self.cluster.close()
            logger.info("Couchbase connection closed")
