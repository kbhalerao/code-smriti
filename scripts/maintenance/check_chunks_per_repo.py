#!/usr/bin/env python3
"""Check chunks per repository"""
import os
import sys
sys.path.insert(0, 'lib/ingestion-worker')

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions

auth = PasswordAuthenticator(
    os.getenv('COUCHBASE_USERNAME', 'Administrator'),
    os.environ['COUCHBASE_PASSWORD']
)
cluster = Cluster('couchbase://localhost', ClusterOptions(auth))

# Query for chunk counts by repo
query = """
SELECT repo_id, COUNT(*) as chunk_count
FROM code_kosha
WHERE repo_id IS NOT MISSING
GROUP BY repo_id
ORDER BY chunk_count DESC
"""

result = cluster.query(query)
repos = [(row['repo_id'], row['chunk_count']) for row in result]

print(f"\nChunks per Repository ({len(repos)} repos)\n")
print(f"{'Repository':<50} {'Chunks':>10}")
print("=" * 62)

total_chunks = 0
for repo_id, count in repos:
    print(f"{repo_id:<50} {count:>10,}")
    total_chunks += count

print("=" * 62)
print(f"{'TOTAL':<50} {total_chunks:>10,}")
print(f"\nAverage chunks per repo: {total_chunks // len(repos):,}")
