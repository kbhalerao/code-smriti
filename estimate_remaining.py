#!/usr/bin/env python3
"""Estimate remaining ingestion time"""
import sys
sys.path.insert(0, 'lib/ingestion-worker')

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions

auth = PasswordAuthenticator('Administrator', 'password123')
cluster = Cluster('couchbase://localhost', ClusterOptions(auth))

# Get repos from file
repos_file = "1-config/repos_to_ingest.txt"
all_repos = []
with open(repos_file, 'r') as f:
    for line in f:
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        repo_id = line.split('#')[0].strip()
        if repo_id:
            all_repos.append(repo_id)

# Get repos in database
query = "SELECT DISTINCT repo_id FROM code_kosha WHERE repo_id IS NOT MISSING"
result = cluster.query(query)
existing_repos = {row['repo_id'] for row in result}

# Get chunk counts for existing repos
query2 = """
SELECT repo_id, COUNT(*) as chunk_count
FROM code_kosha
WHERE repo_id IS NOT MISSING
GROUP BY repo_id
"""
result2 = cluster.query(query2)
chunk_counts = {row['repo_id']: row['chunk_count'] for row in result2}

# Categorize
done_repos = [r for r in all_repos if r in existing_repos]
remaining_repos = [r for r in all_repos if r not in existing_repos]

# Stats on completed repos
total_chunks_done = sum(chunk_counts.values())
largest_repos = sorted(chunk_counts.items(), key=lambda x: x[1], reverse=True)[:10]
smallest_repos = sorted(chunk_counts.items(), key=lambda x: x[1])[:10]

# Estimate remaining - assume smaller repos average
# Exclude the 3 giants for average calculation
chunks_without_giants = [c for r, c in chunk_counts.items()
                         if c < 10000]
avg_chunks_small = sum(chunks_without_giants) / len(chunks_without_giants) if chunks_without_giants else 500

print("="*70)
print("INGESTION TIME ESTIMATE")
print("="*70)
print(f"\nProgress: {len(done_repos)}/{len(all_repos)} repos ({len(done_repos)*100//len(all_repos)}%)")
print(f"  Completed: {len(done_repos)} repos")
print(f"  Remaining: {len(remaining_repos)} repos")

top3_chunks = sum(sorted(chunk_counts.values(), reverse=True)[:3])
print(f"\nChunks ingested: {total_chunks_done:,}")
print(f"  Top 3 repos: {top3_chunks:,} chunks (giants)")
print(f"  Average (excluding giants): {avg_chunks_small:.0f} chunks/repo")

print(f"\nLargest repos (already done ✓):")
for repo, count in largest_repos[:5]:
    print(f"  {repo:<45} {count:>8,} chunks")

print(f"\nRemaining repos to process:")
for i, repo in enumerate(remaining_repos[:10], 1):
    print(f"  {i:2}. {repo}")
if len(remaining_repos) > 10:
    print(f"  ... and {len(remaining_repos)-10} more")

# Estimate based on performance
# labcore: 33,623 chunks in 603 seconds = ~55 chunks/second
# But that includes git operations, parsing, embedding
# For smaller repos, overhead is higher per-chunk
chunks_per_sec = 55
overhead_per_repo = 10  # seconds for clone/setup

estimated_chunks_remaining = len(remaining_repos) * avg_chunks_small
estimated_time = (estimated_chunks_remaining / chunks_per_sec) + (len(remaining_repos) * overhead_per_repo)
estimated_minutes = estimated_time / 60
estimated_hours = estimated_minutes / 60

print(f"\n{'='*70}")
print("TIME ESTIMATE")
print("="*70)
print(f"Estimated remaining chunks: {estimated_chunks_remaining:,.0f}")
print(f"  Assuming avg {avg_chunks_small:.0f} chunks per remaining repo")
print(f"\nProcessing speed: ~{chunks_per_sec} chunks/second")
print(f"  (Based on labcore performance)")
print(f"\nEstimated time remaining:")
print(f"  Optimistic:  {estimated_minutes*0.7:.0f} minutes ({estimated_hours*0.7:.1f} hours)")
print(f"  Realistic:   {estimated_minutes:.0f} minutes ({estimated_hours:.1f} hours)")
print(f"  Pessimistic: {estimated_minutes*1.5:.0f} minutes ({estimated_hours*1.5:.1f} hours)")

print(f"\n{'='*70}")
print("RECOMMENDATION")
print("="*70)
print("The 3 largest repos are done (69% of all chunks).")
print("Remaining repos are mostly small utilities.")
print(f"Estimated completion: {estimated_hours:.1f} hours of processing time")
print("\nSuggestion: Let the automated cron job handle the rest!")
print("  → Runs daily at 8 AM Chicago time")
print("  → Will process new repos automatically")
