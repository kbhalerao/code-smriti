#!/usr/bin/env python3
"""
Extract summaries from Couchbase for Akriti LoRA training.

Pulls repo_summary, module_summary, and doc-type documents for the target repos.
Outputs JSONL for downstream QA generation.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path
from typing import Iterator

from couchbase.cluster import Cluster
from couchbase.auth import PasswordAuthenticator
from couchbase.options import ClusterOptions
from dotenv import load_dotenv
import os

# Load .env from project root
load_dotenv(Path(__file__).parent.parent.parent.parent / ".env")

# Target repos for initial training
TARGET_REPOS = [
    "kbhalerao/labcore",
    "kbhalerao/agkit.io-backend",
    "kbhalerao/agkit.io-ui",
]

# Document types to extract (Akriti-level content)
DOC_TYPES = [
    "repo_summary",
    "module_summary",
    # "file_index",  # Optional: can add later for more detail
]


def get_cluster() -> Cluster:
    """Connect to Couchbase cluster."""
    host = os.getenv("COUCHBASE_HOST", "localhost")
    username = os.getenv("COUCHBASE_USERNAME", "Administrator")
    password = os.getenv("COUCHBASE_PASSWORD", "")

    auth = PasswordAuthenticator(username, password)
    cluster = Cluster(f"couchbase://{host}", ClusterOptions(auth))
    cluster.wait_until_ready(timedelta(seconds=10))
    return cluster


def extract_summaries(
    cluster: Cluster,
    repos: list[str],
    doc_types: list[str],
    bucket_name: str = "code_kosha"
) -> Iterator[dict]:
    """
    Extract summaries from Couchbase.

    Yields documents with: repo_id, type, content, module_path (if applicable)
    """
    for repo_id in repos:
        for doc_type in doc_types:
            query = f"""
                SELECT
                    repo_id,
                    type,
                    content,
                    CASE
                        WHEN type = 'module_summary' THEN module_path
                        ELSE NULL
                    END as module_path,
                    CASE
                        WHEN type = 'repo_summary' THEN metadata.modules
                        WHEN type = 'module_summary' THEN metadata.key_files
                        ELSE NULL
                    END as key_items
                FROM `{bucket_name}`
                WHERE repo_id = $repo_id
                AND type = $doc_type
                AND content IS NOT NULL
                AND content != ""
            """

            result = cluster.query(query, repo_id=repo_id, doc_type=doc_type)

            for row in result:
                yield {
                    "repo_id": row.get("repo_id"),
                    "type": row.get("type"),
                    "content": row.get("content"),
                    "module_path": row.get("module_path"),
                    "key_items": row.get("key_items"),
                }


def extract_docs(
    cluster: Cluster,
    repos: list[str],
    bucket_name: str = "code_kosha"
) -> Iterator[dict]:
    """
    Extract documentation files (MD, RST) from Couchbase.
    These are file_index documents with doc extensions.
    """
    for repo_id in repos:
        query = f"""
            SELECT
                repo_id,
                file_path,
                content,
                metadata.language as language
            FROM `{bucket_name}`
            WHERE repo_id = $repo_id
            AND type = "file_index"
            AND (
                file_path LIKE "%.md"
                OR file_path LIKE "%.rst"
                OR file_path LIKE "%.txt"
            )
            AND content IS NOT NULL
            AND content != ""
        """

        result = cluster.query(query, repo_id=repo_id)

        for row in result:
            yield {
                "repo_id": row.get("repo_id"),
                "type": "doc",
                "file_path": row.get("file_path"),
                "content": row.get("content"),
                "language": row.get("language"),
            }


def main():
    """Extract summaries and save to JSONL."""
    output_dir = Path(__file__).parent.parent / "data"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "summaries.jsonl"

    print(f"Connecting to Couchbase...")
    cluster = get_cluster()

    print(f"Extracting summaries for repos: {TARGET_REPOS}")
    print(f"Document types: {DOC_TYPES}")

    count = 0
    with open(output_file, "w") as f:
        # Extract repo and module summaries
        for doc in extract_summaries(cluster, TARGET_REPOS, DOC_TYPES):
            f.write(json.dumps(doc) + "\n")
            count += 1
            if count % 100 == 0:
                print(f"  Extracted {count} documents...")

        # Extract documentation files
        print("Extracting documentation files...")
        for doc in extract_docs(cluster, TARGET_REPOS):
            f.write(json.dumps(doc) + "\n")
            count += 1
            if count % 100 == 0:
                print(f"  Extracted {count} documents...")

    print(f"\nExtracted {count} total documents to {output_file}")

    # Print summary
    print("\nSummary by type:")
    type_counts = {}
    with open(output_file) as f:
        for line in f:
            doc = json.loads(line)
            doc_type = doc.get("type", "unknown")
            type_counts[doc_type] = type_counts.get(doc_type, 0) + 1

    for doc_type, c in sorted(type_counts.items()):
        print(f"  {doc_type}: {c}")


if __name__ == "__main__":
    main()
