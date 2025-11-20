#!/usr/bin/env python3
"""Check which repos are in the database"""
import sys
sys.path.insert(0, 'lib/ingestion-worker')

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions

auth = PasswordAuthenticator('Administrator', 'password123')
cluster = Cluster('couchbase://localhost', ClusterOptions(auth))

# Query for distinct repo_ids
result = cluster.query('SELECT DISTINCT repo_id FROM code_kosha WHERE repo_id IS NOT MISSING ORDER BY repo_id')
repos = [row['repo_id'] for row in result]
print(f'Total repos in DB: {len(repos)}')
for repo in repos:
    print(f'  - {repo}')
