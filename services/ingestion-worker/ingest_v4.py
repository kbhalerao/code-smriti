#!/usr/bin/env python3
"""
V4 Ingestion CLI

Usage:
    # Single repo (dry run)
    python ingest_v4.py --repo kbhalerao/labcore --dry-run

    # Single repo (full ingestion)
    python ingest_v4.py --repo kbhalerao/labcore

    # All repos
    python ingest_v4.py --all

    # All repos without LLM (basic summaries only)
    python ingest_v4.py --all --no-llm

    # Skip existing (resume after failure)
    python ingest_v4.py --all --skip-existing
"""

import os
import sys
import asyncio
import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional

from loguru import logger

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import WorkerConfig
from v4.pipeline import V4Pipeline
from llm_enricher import LMSTUDIO_CONFIG, OLLAMA_CONFIG

config = WorkerConfig()


def discover_repositories() -> List[Dict[str, str]]:
    """
    Discover all repositories in the repos path.

    Repository folders are named owner_repo (e.g., kbhalerao_labcore).

    Returns:
        List of dicts with repo_id and repo_path
    """
    repos_path = Path(config.repos_path)
    if not repos_path.exists():
        logger.error(f"Repos path does not exist: {repos_path}")
        return []

    repositories = []

    for repo_dir in repos_path.iterdir():
        if not repo_dir.is_dir():
            continue

        # Skip hidden directories
        if repo_dir.name.startswith('.'):
            continue

        # Convert folder name back to repo_id
        # Folder: owner_repo -> Repo ID: owner/repo
        parts = repo_dir.name.split('_', 1)
        if len(parts) == 2:
            repo_id = f"{parts[0]}/{parts[1]}"
        else:
            repo_id = repo_dir.name

        # Verify it's a git repo
        if not (repo_dir / '.git').exists():
            logger.warning(f"Skipping non-git directory: {repo_dir.name}")
            continue

        repositories.append({
            "repo_id": repo_id,
            "repo_path": str(repo_dir),
        })

    logger.info(f"Discovered {len(repositories)} repositories in {repos_path}")
    return repositories


def check_document_exists(storage, repo_id: str) -> bool:
    """Check if any V4 documents exist for this repo."""
    try:
        query = f"""
            SELECT META().id
            FROM `{config.couchbase_bucket}`
            WHERE repo_id = $repo_id
            AND type IN ['repo_summary', 'module_summary', 'file_index', 'symbol_index']
            AND version.schema_version = 'v4.0'
            LIMIT 1
        """
        result = storage.cluster.query(query, repo_id=repo_id)
        rows = list(result)
        return len(rows) > 0
    except Exception as e:
        logger.debug(f"Error checking existing docs: {e}")
        return False


async def ingest_repository(
    pipeline: V4Pipeline,
    repo_path: str,
    repo_id: str,
    skip_existing: bool = False,
) -> Dict:
    """Ingest a single repository."""
    logger.info(f"\n{'='*60}")
    logger.info(f"Processing: {repo_id}")
    logger.info(f"Path: {repo_path}")
    logger.info(f"{'='*60}")

    # Check if should skip
    if skip_existing and pipeline.storage:
        if check_document_exists(pipeline.storage, repo_id):
            logger.info(f"Skipping {repo_id} - V4 documents already exist")
            return {"repo_id": repo_id, "status": "skipped"}

    try:
        result = await pipeline.ingest_repository(
            repo_path=Path(repo_path),
            repo_id=repo_id,
            delete_existing=True,
        )
        result["status"] = "success"
        return result
    except Exception as e:
        logger.error(f"Failed to ingest {repo_id}: {e}")
        return {"repo_id": repo_id, "status": "failed", "error": str(e)}


async def main():
    parser = argparse.ArgumentParser(
        description="V4 Ingestion Pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    # Target selection
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--repo",
        type=str,
        help="Single repo to ingest (format: owner/name)"
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Ingest all repositories in REPOS_PATH"
    )

    # Options
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process but don't store to database"
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM (use basic summaries only)"
    )
    parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="Disable embedding generation"
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="Skip repos that already have V4 documents"
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Number of concurrent file processors (default: 4)"
    )
    parser.add_argument(
        "--llm-provider",
        choices=["lmstudio", "ollama"],
        default="lmstudio",
        help="LLM provider (default: lmstudio)"
    )
    parser.add_argument(
        "--output",
        type=str,
        help="Output file for results (JSON)"
    )

    args = parser.parse_args()

    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="INFO"
    )

    # Select LLM config
    llm_config = LMSTUDIO_CONFIG if args.llm_provider == "lmstudio" else OLLAMA_CONFIG

    # Initialize pipeline
    pipeline = V4Pipeline(
        enable_llm=not args.no_llm,
        enable_embeddings=not args.no_embeddings,
        dry_run=args.dry_run,
        llm_config=llm_config,
    )

    results = []
    start_time = datetime.now()

    try:
        if args.repo:
            # Single repo mode
            # Convert repo_id to path
            repo_folder = args.repo.replace('/', '_')
            repo_path = Path(config.repos_path) / repo_folder

            if not repo_path.exists():
                logger.error(f"Repository not found: {repo_path}")
                sys.exit(1)

            result = await ingest_repository(
                pipeline=pipeline,
                repo_path=str(repo_path),
                repo_id=args.repo,
                skip_existing=args.skip_existing,
            )
            results.append(result)

        else:
            # All repos mode
            repositories = discover_repositories()

            if not repositories:
                logger.error("No repositories found")
                sys.exit(1)

            logger.info(f"\nWill process {len(repositories)} repositories")
            logger.info(f"LLM: {'enabled' if not args.no_llm else 'disabled'}")
            logger.info(f"Embeddings: {'enabled' if not args.no_embeddings else 'disabled'}")
            logger.info(f"Dry run: {args.dry_run}")
            logger.info("")

            for i, repo in enumerate(repositories, 1):
                logger.info(f"\n[{i}/{len(repositories)}] {repo['repo_id']}")

                result = await ingest_repository(
                    pipeline=pipeline,
                    repo_path=repo['repo_path'],
                    repo_id=repo['repo_id'],
                    skip_existing=args.skip_existing,
                )
                results.append(result)

    finally:
        await pipeline.close()

    # Summary
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()

    success_count = sum(1 for r in results if r.get("status") == "success")
    failed_count = sum(1 for r in results if r.get("status") == "failed")
    skipped_count = sum(1 for r in results if r.get("status") == "skipped")

    print("\n" + "=" * 60)
    print("V4 INGESTION COMPLETE")
    print("=" * 60)
    print(f"  Total repositories: {len(results)}")
    print(f"  Successful: {success_count}")
    print(f"  Failed: {failed_count}")
    print(f"  Skipped: {skipped_count}")
    print(f"  Duration: {duration:.1f} seconds")
    print("=" * 60)

    # Show failures
    if failed_count > 0:
        print("\nFailed repositories:")
        for r in results:
            if r.get("status") == "failed":
                print(f"  - {r['repo_id']}: {r.get('error', 'Unknown error')}")

    # Save results
    if args.output:
        output_data = {
            "timestamp": datetime.now().isoformat(),
            "duration_seconds": duration,
            "summary": {
                "total": len(results),
                "success": success_count,
                "failed": failed_count,
                "skipped": skipped_count,
            },
            "results": results,
        }
        with open(args.output, 'w') as f:
            json.dump(output_data, f, indent=2, default=str)
        print(f"\nResults saved to: {args.output}")


if __name__ == "__main__":
    asyncio.run(main())
