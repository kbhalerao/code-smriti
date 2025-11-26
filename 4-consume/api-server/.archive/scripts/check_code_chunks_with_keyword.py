#!/usr/bin/env python3
"""
Check if there are actual code_chunk documents containing "job_counter"
"""
import asyncio
from app.database.couchbase_client import CouchbaseClient

async def main():
    print("=" * 80)
    print("CHECKING: Do code_chunk documents with 'job_counter' exist?")
    print("=" * 80)

    db = CouchbaseClient()

    # Search for code chunks containing "job_counter"
    n1ql = """
        SELECT repo_id, file_path, type,
               LENGTH(content) as content_length,
               SUBSTR(content, 0, 200) as content_preview
        FROM `code_kosha`
        WHERE type = 'code_chunk'
          AND LOWER(content) LIKE '%job_counter%'
        LIMIT 10
    """

    result = db.cluster.query(n1ql)
    rows = list(result)

    print(f"\nFound {len(rows)} code_chunk documents with 'job_counter'\n")

    if rows:
        print("✓ Code chunks with 'job_counter' DO exist!")
        print("\nDocuments:")
        for i, row in enumerate(rows, 1):
            print(f"\n{i}. {row['repo_id']}/{row['file_path']}")
            print(f"   Type: {row['type']}")
            print(f"   Length: {row['content_length']} chars")
            print(f"   Preview: {row['content_preview']}...")
    else:
        print("✗ NO code_chunk documents contain 'job_counter'!")
        print("\nThis means:")
        print("  - Either the code doesn't use @job_counter decorator")
        print("  - Or those files weren't ingested as code_chunks")
        print("  - Or the content field doesn't have that text")

    # Also check document type distribution for job_counter
    print(f"\n{'-' * 80}")
    print("Document type distribution for 'job_counter':")
    print("-" * 80)

    n1ql_types = """
        SELECT type, COUNT(*) as count
        FROM `code_kosha`
        WHERE LOWER(content) LIKE '%job_counter%'
        GROUP BY type
    """

    result = db.cluster.query(n1ql_types)
    for row in result:
        print(f"  {row['type']}: {row['count']}")

    print("\n" + "=" * 80)

asyncio.run(main())
