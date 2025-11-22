#!/usr/bin/env python3
"""Test commit parsing on labcore repository"""

from pathlib import Path
from parsers.commit_parser import CommitParser

# Use labcore repository for testing
REPO_PATH = Path("/Users/kaustubh/Documents/codesmriti-repos/kbhalerao_labcore")
REPO_ID = "kbhalerao/labcore"

def main():
    parser = CommitParser()

    print("=" * 80)
    print("COMMIT PARSING TEST")
    print("=" * 80)
    print(f"Repository: {REPO_PATH}")
    print(f"Repo ID: {REPO_ID}\n")

    # Test 1: Extract recent commits from git history
    print("Test 1: Extracting recent commits from git history")
    print("-" * 80)

    recent_commits = parser.extract_all_commits(
        repo_path=REPO_PATH,
        repo_id=REPO_ID,
        max_commits=10  # Get last 10 commits
    )

    print(f"\n✓ Extracted {len(recent_commits)} recent commits\n")

    # Show details of each commit
    for i, commit in enumerate(recent_commits, 1):
        print(f"Commit {i}:")
        print(f"  Hash: {commit.commit_hash[:12]}...")
        print(f"  Date: {commit.commit_date}")
        print(f"  Author: {commit.author}")
        print(f"  Message: {commit.commit_message[:80]}...")
        print(f"  Files changed: {len(commit.files_changed)}")
        if commit.files_changed:
            # Show first 3 files
            for file in commit.files_changed[:3]:
                print(f"    - {file}")
            if len(commit.files_changed) > 3:
                print(f"    ... and {len(commit.files_changed) - 3} more")
        print()

    # Test 2: Verify commit chunk structure
    print("=" * 80)
    print("Test 2: Verifying commit chunk structure")
    print("-" * 80)

    if recent_commits:
        sample_commit = recent_commits[0]
        commit_dict = sample_commit.to_dict()

        print(f"\nSample commit dictionary keys:")
        for key in commit_dict.keys():
            value = commit_dict[key]
            if isinstance(value, str) and len(value) > 60:
                print(f"  {key}: {value[:60]}...")
            elif isinstance(value, list) and len(value) > 3:
                print(f"  {key}: [{', '.join(value[:3])}, ...] ({len(value)} total)")
            else:
                print(f"  {key}: {value}")

        # Check embedding field
        print(f"\n✓ Has embedding field: {commit_dict.get('embedding') is not None}")
        print(f"✓ Has type field: {commit_dict.get('type')}")
        print(f"✓ Chunk ID generated: {commit_dict.get('chunk_id') is not None}")

    # Test 3: Check for duplicates
    print("\n" + "=" * 80)
    print("Test 3: Checking for duplicate commits")
    print("-" * 80)

    commit_hashes = [c.commit_hash for c in recent_commits]
    unique_hashes = set(commit_hashes)

    print(f"\nTotal commits: {len(commit_hashes)}")
    print(f"Unique hashes: {len(unique_hashes)}")

    if len(commit_hashes) == len(unique_hashes):
        print("✓ No duplicates detected")
    else:
        print(f"⚠️  Warning: {len(commit_hashes) - len(unique_hashes)} duplicate(s) found")

    # Test 4: Verify commit message sizes
    print("\n" + "=" * 80)
    print("Test 4: Analyzing commit message sizes")
    print("-" * 80)

    message_sizes = [len(c.commit_message) for c in recent_commits]
    avg_size = sum(message_sizes) / len(message_sizes) if message_sizes else 0
    max_size = max(message_sizes) if message_sizes else 0
    min_size = min(message_sizes) if message_sizes else 0

    print(f"\nCommit message statistics:")
    print(f"  Average: {avg_size:.0f} chars (~{avg_size * 0.75:.0f} tokens)")
    print(f"  Largest: {max_size} chars (~{max_size * 0.75:.0f} tokens)")
    print(f"  Smallest: {min_size} chars")

    # Check if any are too large for embedding
    large_messages = [c for c in recent_commits if len(c.commit_message) > 6000]
    if large_messages:
        print(f"\n⚠️  WARNING: {len(large_messages)} commit message(s) exceed 6000 char limit")
        for commit in large_messages:
            print(f"  - {commit.commit_hash[:12]}: {len(commit.commit_message)} chars")
    else:
        print(f"\n✓ All commit messages under 6000 char limit")

    print("\n" + "=" * 80)
    print("COMMIT PARSING TEST COMPLETE")
    print("=" * 80)

if __name__ == "__main__":
    main()
