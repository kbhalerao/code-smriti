#!/usr/bin/env python3
"""
Check the actual type of markdown files appearing in search results
"""
import asyncio
from app.database.couchbase_client import CouchbaseClient

async def main():
    print("=" * 80)
    print("CHECKING: Types of markdown files appearing in results")
    print("=" * 80)

    db = CouchbaseClient()

    files_to_check = [
        '%OPTIMIZATION_REPORT.md',
        '%RESPONSE_QUALITY_ISSUES.md',
        '%CASE-STUDY%',
    ]

    for file_pattern in files_to_check:
        print(f"\nSearching for: {file_pattern}")
        print("-" * 80)

        n1ql = """
            SELECT repo_id, file_path, type, LENGTH(content) as len
            FROM `code_kosha`
            WHERE file_path LIKE $pattern
            LIMIT 3
        """

        from couchbase.options import QueryOptions
        result = db.cluster.query(n1ql, QueryOptions(named_parameters={"pattern": file_pattern}))

        found = False
        for row in result:
            found = True
            print(f"  {row['repo_id']}/{row['file_path']}")
            print(f"    Type: {row['type']}, Length: {row['len']}")

        if not found:
            print(f"  No matches found")

    print("\n" + "=" * 80)
    print("If these markdown files are type='document', the filter IS working!")
    print("If they are type='code_chunk', we need to investigate further")
    print("=" * 80)

asyncio.run(main())
