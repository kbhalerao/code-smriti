#!/usr/bin/env python3
"""
Batch V3 Ingestion Script

Runs V3 ingestion on all repositories, ordered by size (largest first).
Designed to run in tmux for long-running batch processing.

Usage:
    python run_all_v3.py                    # Run all repos with LLM
    python run_all_v3.py --no-llm           # Run without LLM (faster)
    python run_all_v3.py --dry-run          # Preview what would be processed
    python run_all_v3.py --resume kbhalerao/labcore  # Resume from specific repo
    python run_all_v3.py --only kbhalerao/labcore,kbhalerao/evolvechiro  # Only specific repos

Progress is logged to: v3_ingestion_progress.log
"""

import asyncio
import argparse
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from typing import List, Optional

from loguru import logger

from config import WorkerConfig

config = WorkerConfig()

# Configure logging to both console and file
LOG_FILE = Path(__file__).parent / "v3_ingestion_progress.log"


@dataclass
class RepoInfo:
    """Repository info with size metrics"""
    repo_id: str  # owner/repo format
    path: Path
    file_count: int
    total_size: int  # bytes


def get_repo_id(folder_name: str) -> str:
    """Convert folder name to repo_id format (owner/repo)"""
    parts = folder_name.split("_", 1)
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}"
    return folder_name


def scan_repos(repos_path: Path) -> List[RepoInfo]:
    """Scan all repos and gather size info"""
    repos = []

    # Skip test repos
    SKIP_REPOS = {"test_code-smriti", "test_"}

    for item in repos_path.iterdir():
        if not item.is_dir():
            continue
        if item.name.startswith('.'):
            continue
        if item.name.startswith('test_') or item.name in SKIP_REPOS:
            continue

        # Count files and total size
        file_count = 0
        total_size = 0

        try:
            for f in item.rglob("*"):
                if f.is_file() and not any(p.startswith('.') for p in f.parts):
                    file_count += 1
                    try:
                        total_size += f.stat().st_size
                    except:
                        pass
        except Exception as e:
            logger.warning(f"Error scanning {item.name}: {e}")
            continue

        repos.append(RepoInfo(
            repo_id=get_repo_id(item.name),
            path=item,
            file_count=file_count,
            total_size=total_size
        ))

    # Sort by total size descending (largest first)
    repos.sort(key=lambda r: r.total_size, reverse=True)

    return repos


async def run_ingestion(
    repo_id: str,
    enable_llm: bool = True,
    dry_run: bool = False,
    llm_host: str = "macstudio.local",
    llm_port: int = 1234,
    model: str = "qwen/qwen3-30b-a3b-2507"
) -> tuple[bool, str]:
    """
    Run V3 ingestion on a single repo.

    Returns:
        (success: bool, message: str)
    """
    cmd = [
        sys.executable, "ingest_v3.py",
        "--repo", repo_id,
        "--llm-host", llm_host,
        "--llm-port", str(llm_port),
        "--model", model
    ]

    if not enable_llm:
        cmd.append("--no-llm")

    if dry_run:
        cmd.append("--dry-run")

    try:
        # Run without timeout, stream output for progress
        process = subprocess.Popen(
            cmd,
            cwd=Path(__file__).parent,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1  # Line buffered
        )

        output_lines = []
        for line in process.stdout:
            line = line.rstrip()
            output_lines.append(line)

            # Show progress every 100 files
            if "Processed" in line and "files" in line:
                logger.info(f"  {repo_id}: {line.split('|')[-1].strip()}")

        process.wait()

        if process.returncode == 0:
            # Extract summary from output
            summary_lines = []
            in_summary = False
            for line in output_lines:
                if "V3 Ingestion Summary" in line:
                    in_summary = True
                if in_summary:
                    summary_lines.append(line)

            return True, '\n'.join(summary_lines[-10:]) if summary_lines else "Success"
        else:
            return False, f"Exit code {process.returncode}: {output_lines[-5:]}"

    except Exception as e:
        return False, f"Error: {e}"


async def main():
    parser = argparse.ArgumentParser(description="Batch V3 Ingestion")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM summaries")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to database")
    parser.add_argument("--resume", type=str, help="Resume from specific repo_id")
    parser.add_argument("--only", type=str, help="Comma-separated list of repo_ids to process")
    parser.add_argument("--llm-host", type=str, default="macstudio.local")
    parser.add_argument("--llm-port", type=int, default=1234)
    parser.add_argument("--model", type=str, default="qwen/qwen3-30b-a3b-2507")
    args = parser.parse_args()

    # Setup logging
    logger.remove()
    logger.add(sys.stderr, level="INFO", format="{time:HH:mm:ss} | {level} | {message}")
    logger.add(LOG_FILE, level="DEBUG", format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}")

    repos_path = Path(config.repos_path)
    logger.info(f"Scanning repos in {repos_path}")

    # Get all repos sorted by size
    all_repos = scan_repos(repos_path)
    logger.info(f"Found {len(all_repos)} repositories")

    # Filter repos if --only specified
    if args.only:
        only_ids = set(args.only.split(","))
        all_repos = [r for r in all_repos if r.repo_id in only_ids]
        logger.info(f"Filtered to {len(all_repos)} repos: {[r.repo_id for r in all_repos]}")

    # Resume from specific repo if specified
    start_idx = 0
    if args.resume:
        for i, repo in enumerate(all_repos):
            if repo.repo_id == args.resume:
                start_idx = i
                break
        logger.info(f"Resuming from {args.resume} (index {start_idx})")

    # Print repo order
    logger.info("=" * 60)
    logger.info("INGESTION ORDER (by size, largest first):")
    logger.info("=" * 60)
    for i, repo in enumerate(all_repos):
        size_mb = repo.total_size / (1024 * 1024)
        marker = ">>>" if i == start_idx else "   "
        logger.info(f"{marker} {i+1:3d}. {repo.repo_id:<45} {size_mb:8.1f} MB  ({repo.file_count} files)")
    logger.info("=" * 60)

    if args.dry_run:
        logger.info("[DRY RUN MODE - No database writes]")

    # Process repos
    success_count = 0
    fail_count = 0
    failed_repos = []

    start_time = datetime.now()

    for i, repo in enumerate(all_repos[start_idx:], start=start_idx + 1):
        size_mb = repo.total_size / (1024 * 1024)
        logger.info("")
        logger.info(f"[{i}/{len(all_repos)}] Processing: {repo.repo_id} ({size_mb:.1f} MB, {repo.file_count} files)")
        logger.info("-" * 60)

        repo_start = datetime.now()

        success, message = await run_ingestion(
            repo.repo_id,
            enable_llm=not args.no_llm,
            dry_run=args.dry_run,
            llm_host=args.llm_host,
            llm_port=args.llm_port,
            model=args.model
        )

        repo_elapsed = (datetime.now() - repo_start).total_seconds()

        if success:
            success_count += 1
            logger.info(f"✓ Completed {repo.repo_id} in {repo_elapsed:.1f}s")
            logger.debug(message)
        else:
            fail_count += 1
            failed_repos.append(repo.repo_id)
            logger.error(f"✗ Failed {repo.repo_id}: {message[:200]}")

        # Progress update
        total_elapsed = (datetime.now() - start_time).total_seconds()
        remaining = len(all_repos) - i
        if i > start_idx:
            avg_time = total_elapsed / (i - start_idx)
            eta_seconds = avg_time * remaining
            eta_minutes = eta_seconds / 60
            logger.info(f"Progress: {i}/{len(all_repos)} | Success: {success_count} | Failed: {fail_count} | ETA: {eta_minutes:.0f} min")

    # Final summary
    total_elapsed = (datetime.now() - start_time).total_seconds()
    logger.info("")
    logger.info("=" * 60)
    logger.info("BATCH INGESTION COMPLETE")
    logger.info("=" * 60)
    logger.info(f"Total repos: {len(all_repos)}")
    logger.info(f"Successful: {success_count}")
    logger.info(f"Failed: {fail_count}")
    logger.info(f"Total time: {total_elapsed / 60:.1f} minutes")

    if failed_repos:
        logger.warning("Failed repos:")
        for repo_id in failed_repos:
            logger.warning(f"  - {repo_id}")

    logger.info(f"Full log: {LOG_FILE}")


if __name__ == "__main__":
    asyncio.run(main())
