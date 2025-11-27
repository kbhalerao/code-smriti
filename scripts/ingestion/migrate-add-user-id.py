#!/usr/bin/env python3
"""
Migration script: Add user_id to existing chunks

This script:
1. Creates a "system" user for existing data
2. Adds user_id field to all existing chunks in code_kosha
3. Discovers repos from chunks and adds them to system user
4. Computes chunk counts per repo

Run: python3 migrate-add-user-id.py
"""

import os
import sys
from datetime import timedelta, datetime
from collections import defaultdict

# Add parent directory to path to import from lib
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions
from couchbase.exceptions import DocumentExistsException
from loguru import logger

# User ID for kbhalerao (default user for existing data)
# Generate a consistent UUID for this user
import uuid
KBHALERAO_USER_ID = str(uuid.uuid5(uuid.NAMESPACE_DNS, "kbhalerao@codesmriti"))

# Load environment variables
from dotenv import load_dotenv
load_dotenv()

# Couchbase configuration
CB_HOST = os.getenv("COUCHBASE_HOST", "localhost")
CB_USER = os.getenv("COUCHBASE_USER", "Administrator")
CB_PASSWORD = os.getenv("COUCHBASE_PASSWORD", "password123")
CB_BUCKET_CODE = os.getenv("COUCHBASE_BUCKET_CODE", "code_kosha")
CB_BUCKET_USERS = os.getenv("COUCHBASE_BUCKET_USERS", "users")

logger.info("=" * 50)
logger.info("Migration: Add user_id to existing chunks")
logger.info("=" * 50)
logger.info("")

def main():
    # Connect to Couchbase
    logger.info(f"Connecting to Couchbase at {CB_HOST}...")
    connection_string = f"couchbase://{CB_HOST}"
    auth = PasswordAuthenticator(CB_USER, CB_PASSWORD)

    cluster = Cluster(connection_string, ClusterOptions(auth))
    cluster.wait_until_ready(timedelta(seconds=10))

    code_bucket = cluster.bucket(CB_BUCKET_CODE)
    users_bucket = cluster.bucket(CB_BUCKET_USERS)

    code_collection = code_bucket.default_collection()
    users_collection = users_bucket.default_collection()

    logger.info("✓ Connected to Couchbase")
    logger.info("")

    # Step 1: Create kbhalerao user account
    logger.info("Step 1: Creating user account for kbhalerao...")

    # Generate a bcrypt hash for default password (user should change this)
    import bcrypt
    default_password = "changeme123"  # User should change this after first login
    password_hash = bcrypt.hashpw(default_password.encode(), bcrypt.gensalt()).decode()

    user = {
        "type": "user",
        "user_id": KBHALERAO_USER_ID,
        "email": "kaustubh@codesmriti.dev",  # Use your actual email
        "password_hash": password_hash,
        "github_pat_encrypted": None,  # Can be added via web UI later
        "repos": [],  # Will populate after discovering from chunks
        "quota_max_repos": 500,  # Generous quota for internal use
        "quota_max_chunks": 1000000,
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
        "last_login": None
    }

    try:
        users_collection.insert(KBHALERAO_USER_ID, user)
        logger.info(f"✓ User account created: kbhalerao")
        logger.info(f"   Email: kaustubh@codesmriti.dev")
        logger.info(f"   Password: {default_password} (CHANGE THIS AFTER FIRST LOGIN!)")
        logger.info(f"   User ID: {KBHALERAO_USER_ID}")
    except DocumentExistsException:
        logger.warning("⚠ User account already exists, skipping creation")

    logger.info("")

    # Step 2: Add user_id to all existing chunks
    logger.info("Step 2: Adding user_id to existing chunks...")
    logger.info("This may take a few minutes for large datasets...")

    update_query = f"""
        UPDATE `{CB_BUCKET_CODE}`
        SET user_id = "{KBHALERAO_USER_ID}"
        WHERE user_id IS MISSING
    """

    try:
        result = cluster.query(update_query)
        metadata = result.metadata()
        if metadata:
            metrics = metadata.metrics()
            mutation_count = metrics.get("mutationCount", 0)
        else:
            mutation_count = 0

        logger.info(f"✓ Updated {mutation_count} chunks with user_id")
    except Exception as e:
        logger.error(f"✗ Failed to update chunks: {e}")
        import traceback
        traceback.print_exc()
        return 1

    logger.info("")

    # Step 3: Discover repos from chunks
    logger.info("Step 3: Discovering repositories from chunks...")

    discover_query = f"""
        SELECT repo_id, COUNT(*) as chunk_count
        FROM `{CB_BUCKET_CODE}`
        WHERE user_id = "{KBHALERAO_USER_ID}"
        GROUP BY repo_id
    """

    try:
        result = cluster.query(discover_query)
        repos_data = list(result)

        repos_list = []
        total_chunks = 0

        for row in repos_data:
            repo_id = row["repo_id"]
            chunk_count = row["chunk_count"]
            total_chunks += chunk_count

            repos_list.append({
                "repo_id": repo_id,
                "added_at": datetime.utcnow().isoformat(),
                "last_synced": datetime.utcnow().isoformat(),
                "chunk_count": chunk_count,
                "status": "synced",
                "sync_error": None
            })

            logger.info(f"  Found: {repo_id} ({chunk_count:,} chunks)")

        logger.info("")
        logger.info(f"✓ Discovered {len(repos_list)} repositories")
        logger.info(f"✓ Total chunks: {total_chunks:,}")

    except Exception as e:
        logger.error(f"✗ Failed to discover repos: {e}")
        return 1

    logger.info("")

    # Step 4: Update system user with discovered repos
    logger.info("Step 4: Updating system user with repository list...")

    try:
        user_doc = users_collection.get(KBHALERAO_USER_ID)
        user_data = user_doc.content_as[dict]

        user_data["repos"] = repos_list
        user_data["updated_at"] = datetime.utcnow().isoformat()

        users_collection.upsert(KBHALERAO_USER_ID, user_data)

        logger.info(f"✓ User account updated with {len(repos_list)} repositories")

    except Exception as e:
        logger.error(f"✗ Failed to update system user: {e}")
        return 1

    logger.info("")
    logger.info("=" * 50)
    logger.info("✓ Migration complete!")
    logger.info("=" * 50)
    logger.info("")
    logger.info("Summary:")
    logger.info(f"  - User: kbhalerao")
    logger.info(f"  - Email: kaustubh@codesmriti.dev")
    logger.info(f"  - User ID: {KBHALERAO_USER_ID}")
    logger.info(f"  - Chunks migrated: {mutation_count:,}")
    logger.info(f"  - Repositories: {len(repos_list)}")
    logger.info(f"  - Total chunks: {total_chunks:,}")
    logger.info("")
    logger.info("Login Credentials:")
    logger.info(f"  - Email: kaustubh@codesmriti.dev")
    logger.info(f"  - Password: changeme123")
    logger.info("  ⚠ IMPORTANT: Change this password after first login!")
    logger.info("")
    logger.info("Next steps:")
    logger.info("  1. Update vector index via Couchbase FTS UI to include user_id")
    logger.info("  2. Test search with user_id filter")
    logger.info("  3. Begin web UI development")
    logger.info("")

    return 0


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.warning("\n⚠ Migration interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"✗ Migration failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
