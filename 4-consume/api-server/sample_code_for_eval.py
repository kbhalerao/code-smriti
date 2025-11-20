#!/usr/bin/env python3
"""
Sample code from repositories in the database to create evaluation questions.
"""

import json
import sys
from pathlib import Path
from collections import defaultdict
from datetime import datetime

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from app.database.couchbase_client import get_cluster
from app.config import settings

def sample_repos():
    """Sample code chunks from specified repositories."""

    target_repos = [
        "kbhalerao/labcore",
        "JessiePBhalerao/firstseedtests",
        "kbhalerao/ask-kev-2026",
        "kbhalerao/smartbarn2025",
        "kbhalerao/508hCoverCrop"
    ]

    cluster = get_cluster()
    bucket = cluster.bucket(settings.couchbase_bucket_code)

    # First, check what repos are actually in the database
    print(f"\n{'='*70}")
    print("Checking available repositories in database...")
    print('='*70)

    check_query = f"""
    SELECT DISTINCT repo_id
    FROM `{settings.couchbase_bucket_code}`
    WHERE type = 'chunk'
    LIMIT 100
    """

    try:
        result = cluster.query(check_query)
        available_repos = [row['repo_id'] for row in result]
        print(f"Found {len(available_repos)} repositories:")
        for repo in sorted(available_repos):
            print(f"  - {repo}")
    except Exception as e:
        print(f"Error checking repos: {e}")
        available_repos = []

    repo_samples = {}

    for repo_id in target_repos:
        print(f"\n{'='*70}")
        print(f"Sampling from: {repo_id}")
        print('='*70)

        # Query to get diverse code samples from this repo
        query = f"""
        SELECT
            file_path,
            content,
            `language`,
            start_line,
            end_line,
            chunk_index,
            metadata
        FROM `{settings.couchbase_bucket_code}`
        WHERE type = 'chunk'
        AND repo_id = $repo_id
        ORDER BY file_path, chunk_index
        LIMIT 200
        """

        try:
            result = cluster.query(query, repo_id=repo_id)
            chunks = list(result)

            print(f"Found {len(chunks)} chunks")

            # Group by file
            files = defaultdict(list)
            for chunk in chunks:
                files[chunk['file_path']].append(chunk)

            print(f"Across {len(files)} files")

            # Sample diverse files
            sampled_files = {}
            for file_path, file_chunks in sorted(files.items())[:20]:
                sampled_files[file_path] = {
                    'language': file_chunks[0].get('language', 'unknown'),
                    'chunks': file_chunks[:3],  # First 3 chunks per file
                }

            repo_samples[repo_id] = {
                'total_chunks': len(chunks),
                'total_files': len(files),
                'sampled_files': sampled_files
            }

            # Print sample
            print(f"\nSample files:")
            for i, file_path in enumerate(list(sampled_files.keys())[:10], 1):
                lang = sampled_files[file_path]['language']
                print(f"  {i}. {file_path} ({lang})")

        except Exception as e:
            print(f"Error querying {repo_id}: {e}")
            repo_samples[repo_id] = {'error': str(e)}

    return repo_samples


def main():
    print("\n" + "="*70)
    print("CODE SAMPLING FOR EVALUATION SUITE")
    print("="*70)

    samples = sample_repos()

    # Save samples
    output_file = Path(__file__).parent / "sampled_code.json"

    with open(output_file, 'w') as f:
        json.dump(samples, f, indent=2)

    print(f"\n{'='*70}")
    print(f"Saved samples to: {output_file}")
    print('='*70)

    # Summary
    print("\nSummary:")
    for repo_id, data in samples.items():
        if 'error' in data:
            print(f"  {repo_id}: ERROR - {data['error']}")
        else:
            print(f"  {repo_id}: {data['total_files']} files, {data['total_chunks']} chunks")


if __name__ == "__main__":
    main()
