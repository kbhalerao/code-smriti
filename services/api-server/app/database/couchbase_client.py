"""
Couchbase database client with connection pooling.
"""

from datetime import timedelta
from typing import Optional

from couchbase.auth import PasswordAuthenticator
from couchbase.cluster import Cluster
from couchbase.options import ClusterOptions
from loguru import logger

from ..config import settings

# Global cluster instance (singleton)
_cluster: Optional[Cluster] = None


def get_cluster() -> Cluster:
    """
    Get or create Couchbase cluster connection (singleton).

    Returns:
        Cluster: Connected Couchbase cluster instance.
    """
    global _cluster

    if _cluster is None:
        logger.info(f"Connecting to Couchbase at {settings.couchbase_host}:{settings.couchbase_port}")

        connection_string = f"couchbase://{settings.couchbase_host}"

        auth = PasswordAuthenticator(
            settings.couchbase_user,
            settings.couchbase_password
        )

        opts = ClusterOptions(auth)
        _cluster = Cluster(connection_string, opts)

        # Wait for cluster to be ready
        _cluster.wait_until_ready(timedelta(seconds=10))

        logger.info("✓ Connected to Couchbase")

    return _cluster


def get_code_collection():
    """
    Get code_kosha bucket default collection.

    Returns:
        Collection: Code chunks collection.
    """
    cluster = get_cluster()
    bucket = cluster.bucket(settings.couchbase_bucket_code)
    return bucket.default_collection()


def get_users_collection():
    """
    Get users bucket default collection.

    Returns:
        Collection: Users collection.
    """
    cluster = get_cluster()
    bucket = cluster.bucket(settings.couchbase_bucket_users)
    return bucket.default_collection()


def get_jobs_collection():
    """
    Get ingestion_jobs bucket default collection.

    Returns:
        Collection: Jobs collection.
    """
    cluster = get_cluster()
    bucket = cluster.bucket(settings.couchbase_bucket_jobs)
    return bucket.default_collection()


async def close_cluster():
    """Close Couchbase cluster connection (for graceful shutdown)."""
    global _cluster
    if _cluster:
        logger.info("Closing Couchbase connection")
        _cluster.close()
        _cluster = None


class CouchbaseClient:
    """Convenience wrapper for Couchbase operations"""

    def __init__(self):
        self.cluster = get_cluster()

    def get_tenant_collection(self, tenant_id: str):
        """Get collection for a specific tenant bucket"""
        bucket = self.cluster.bucket(tenant_id)
        return bucket.default_collection()
