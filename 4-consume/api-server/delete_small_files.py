#!/usr/bin/env python3
"""
Delete small files (< 100 chars) from Couchbase database.

This cleans up empty __init__.py files and other boilerplate that pollutes search results.
"""
import sys
from pathlib import Path

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from app.database.couchbase_client import CouchbaseClient
from couchbase.options import QueryOptions
from loguru import logger

def main():
    print("=" * 80)
    print("DELETE SMALL FILES FROM COUCHBASE")
    print("=" * 80)

    db = CouchbaseClient()

    # First, count how many small files exist
    print("\n1. Counting small files (< 100 chars)...")

    count_n1ql = """
        SELECT COUNT(*) as count
        FROM `code_kosha`
        WHERE type = 'code_chunk'
          AND metadata.file_size < 100
    """

    result = db.cluster.query(count_n1ql)
    for row in result:
        count = row['count']

    print(f"   Found {count:,} small files")

    if count == 0:
        print("\n✓ No small files to delete!")
        return

    # Show sample of what will be deleted
    print("\n2. Sample of files to delete:")

    sample_n1ql = """
        SELECT META().id, file_path, repo_id, metadata.file_size as len
        FROM `code_kosha`
        WHERE type = 'code_chunk'
          AND metadata.file_size < 100
        LIMIT 20
    """

    result = db.cluster.query(sample_n1ql)
    for i, row in enumerate(result, 1):
        print(f"   {i}. {row['file_path']} ({row['len']} chars) - {row['repo_id']}")

    # Confirm deletion
    print("\n" + "-" * 80)
    response = input(f"\nDelete {count:,} small files? (yes/no): ")

    if response.lower() != 'yes':
        print("Aborted by user")
        return

    # Delete in batches
    print("\n3. Deleting small files...")

    batch_size = 1000
    deleted = 0

    while True:
        # Get batch of IDs to delete
        batch_n1ql = f"""
            SELECT META().id
            FROM `code_kosha`
            WHERE type = 'code_chunk'
              AND metadata.file_size < 100
            LIMIT {batch_size}
        """

        result = db.cluster.query(batch_n1ql)
        ids = [row['id'] for row in result]

        if not ids:
            break

        # Delete batch using KV operations (N1QL DELETE with IN doesn't work)
        bucket = db.cluster.bucket("code_kosha")
        collection = bucket.default_collection()

        for doc_id in ids:
            try:
                collection.remove(doc_id)
            except Exception as e:
                logger.warning(f"Failed to delete {doc_id}: {e}")

        deleted += len(ids)
        print(f"   Deleted {deleted:,} / {count:,} files...")

    print(f"\n✓ Deleted {deleted:,} small files total")

    # Verify deletion
    print("\n4. Verifying deletion...")

    result = db.cluster.query(count_n1ql)
    for row in result:
        remaining = row['count']

    if remaining == 0:
        print(f"   ✓ All small files deleted successfully!")
    else:
        print(f"   ⚠️  {remaining} small files still remain")

    print("\n" + "=" * 80)
    print("NEXT STEPS:")
    print("=" * 80)
    print("1. Rebuild FTS index to remove deleted documents from search")
    print("2. Test search quality to verify improvement")
    print("=" * 80)

if __name__ == "__main__":
    main()
