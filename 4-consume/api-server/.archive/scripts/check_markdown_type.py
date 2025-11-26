#!/usr/bin/env python3
"""
Check if markdown files showing up in results are actually code_chunks
"""
import asyncio
from app.database.couchbase_client import CouchbaseClient

async def main():
    print("=" * 80)
    print("CHECKING: Are .md files in results actually code_chunks?")
    print("=" * 80)

    db = CouchbaseClient()

    # Check files that appeared in test results
    test_files = [
        "fwreporting/OPTIMIZATION_REPORT.md",
        "__init__.py",
        "RESPONSE_QUALITY_ISSUES.md"
    ]

    for file_pattern in test_files:
        print(f"\nSearching for: *{file_pattern}")
        print("-" * 80)

        n1ql = """
            SELECT repo_id, file_path, type, LENGTH(content) as len
            FROM `code_kosha`
            WHERE file_path LIKE $pattern
            LIMIT 5
        """

        from couchbase.options import QueryOptions
        result = db.cluster.query(n1ql, QueryOptions(named_parameters={"pattern": f"%{file_pattern}"}))

        for row in result:
            print(f"  {row['repo_id']}/{row['file_path']}")
            print(f"    Type: {row['type']}, Length: {row['len']}")

    print("\n" + "=" * 80)
    print("If markdown files have type='code_chunk', that's correct behavior")
    print("(Markdown docs are chunked just like code)")
    print("=" * 80)

asyncio.run(main())
