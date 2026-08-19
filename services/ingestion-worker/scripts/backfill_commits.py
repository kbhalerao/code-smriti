#!/usr/bin/env python3
"""
Backfill Commit History - Extract git commits with velocity metrics.

Creates commit_index documents with:
- commit_hash, commit_date, author
- commit_message (content)
- lines_added, lines_deleted, files_changed
- Optional: embedding for semantic search on commit messages

Usage:
    python backfill_commits.py --all                # All repos
    python backfill_commits.py --repo owner/name    # Single repo
    python backfill_commits.py --all --dry-run      # Preview without storing
    python backfill_commits.py --all --limit 100    # Last 100 commits per repo
    python backfill_commits.py --all --embed        # Generate embeddings (slower)
"""

import argparse
import hashlib
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Optional

from loguru import logger

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import WorkerConfig
from storage.couchbase_client import CouchbaseClient

config = WorkerConfig()

SCHEMA_VERSION = "v4.0"


def make_commit_id(repo_id: str, commit_hash: str) -> str:
    """Generate document_id for commit_index."""
    key = f"commit:{repo_id}:{commit_hash[:12]}"
    return hashlib.sha256(key.encode()).hexdigest()


@dataclass
class CommitIndex:
    """V4 commit_index document for velocity analysis."""
    document_id: str
    repo_id: str
    commit_hash: str
    commit_date: str  # ISO format
    author: str
    content: str  # commit message

    # Velocity metrics
    lines_added: int = 0
    lines_deleted: int = 0
    files_changed: List[str] = field(default_factory=list)

    # Optional embedding
    embedding: Optional[List[float]] = None

    # Version info
    schema_version: str = SCHEMA_VERSION
    created_at: str = ""

    def to_dict(self) -> Dict:
        return {
            "document_id": self.document_id,
            "type": "commit_index",
            "repo_id": self.repo_id,
            "commit_hash": self.commit_hash,
            "commit_date": self.commit_date,
            "author": self.author,
            "content": self.content,
            "metadata": {
                "lines_added": self.lines_added,
                "lines_deleted": self.lines_deleted,
                "files_changed": self.files_changed,
                "file_count": len(self.files_changed),
            },
            "embedding": self.embedding,
            "version": {
                "schema_version": self.schema_version,
                "created_at": self.created_at,
            },
        }


def extract_commits_with_stats(
    repo_path: Path,
    repo_id: str,
    limit: Optional[int] = None
) -> List[CommitIndex]:
    """
    Extract commits with lines added/deleted using git log --numstat.

    Format: commit_hash|author|date|message then numstat lines (add\tdel\tfile)
    """
    commits = []

    # Build git log command
    format_str = "%H|%ae|%aI|%s"  # hash|author|date|subject
    cmd = ["git", "log", f"--format={format_str}", "--numstat"]
    if limit:
        cmd.extend(["-n", str(limit)])

    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=300  # 5 min timeout for large repos
        )

        if result.returncode != 0:
            logger.error(f"git log failed for {repo_id}: {result.stderr}")
            return []

        # Parse output
        lines = result.stdout.strip().split('\n')
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if not line or '\t' in line:
                # Empty line or numstat line, skip
                i += 1
                continue

            # Parse commit line
            parts = line.split('|', 3)
            if len(parts) != 4:
                i += 1
                continue

            commit_hash, author, date, message = parts

            # Collect numstat lines until next commit or end
            lines_added = 0
            lines_deleted = 0
            files_changed = []

            i += 1
            while i < len(lines):
                stat_line = lines[i].strip()
                if not stat_line:
                    i += 1
                    continue
                if '|' in stat_line and '\t' not in stat_line:
                    # Next commit line
                    break

                # Parse numstat: add\tdel\tfilename
                stat_parts = stat_line.split('\t')
                if len(stat_parts) >= 3:
                    try:
                        add = int(stat_parts[0]) if stat_parts[0] != '-' else 0
                        delete = int(stat_parts[1]) if stat_parts[1] != '-' else 0
                        filename = stat_parts[2]
                        lines_added += add
                        lines_deleted += delete
                        files_changed.append(filename)
                    except ValueError:
                        pass
                i += 1

            # Create commit index
            commit = CommitIndex(
                document_id=make_commit_id(repo_id, commit_hash),
                repo_id=repo_id,
                commit_hash=commit_hash,
                commit_date=date,
                author=author,
                content=message,
                lines_added=lines_added,
                lines_deleted=lines_deleted,
                files_changed=files_changed,
                created_at=datetime.now(timezone.utc).isoformat(),
            )
            commits.append(commit)

    except subprocess.TimeoutExpired:
        logger.error(f"git log timed out for {repo_id}")
    except Exception as e:
        logger.error(f"Error extracting commits from {repo_id}: {e}")

    return commits


def discover_repositories() -> List[Dict[str, str]]:
    """Discover all repositories in the repos path."""
    repos_path = Path(config.repos_path)
    if not repos_path.exists():
        logger.error(f"Repos path does not exist: {repos_path}")
        return []

    repositories = []

    for repo_dir in repos_path.iterdir():
        if not repo_dir.is_dir():
            continue
        if repo_dir.name.startswith('.'):
            continue

        # Convert folder name to repo_id
        parts = repo_dir.name.split('_', 1)
        if len(parts) == 2:
            repo_id = f"{parts[0]}/{parts[1]}"
        else:
            repo_id = repo_dir.name

        # Verify it's a git repo
        if not (repo_dir / '.git').exists():
            continue

        repositories.append({
            "repo_id": repo_id,
            "repo_path": str(repo_dir),
        })

    logger.info(f"Discovered {len(repositories)} repositories")
    return repositories


def count_existing_commits(cb_client: CouchbaseClient, repo_id: str) -> int:
    """Count existing commit_index documents for a repo."""
    try:
        query = f"""
            SELECT COUNT(*) as cnt
            FROM `{config.couchbase_bucket}`
            WHERE type = 'commit_index' AND repo_id = $repo_id
        """
        result = cb_client.cluster.query(query, repo_id=repo_id)
        for row in result:
            return row.get('cnt', 0)
    except Exception as e:
        logger.warning(f"Could not count existing commits: {e}")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="Backfill commit history with velocity metrics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--repo", type=str, help="Single repo (format: owner/name)")
    group.add_argument("--all", action="store_true", help="All repositories")

    parser.add_argument("--dry-run", action="store_true", help="Preview without storing")
    parser.add_argument("--limit", type=int, help="Max commits per repo (default: all)")
    parser.add_argument("--no-embed", action="store_true", help="Skip embedding generation")
    parser.add_argument("--skip-existing", action="store_true", help="Skip repos with commits")

    args = parser.parse_args()

    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"
    )

    # Initialize storage
    cb_client = None
    if not args.dry_run:
        cb_client = CouchbaseClient()

    # Initialize embedding generator (default: enabled)
    embedding_generator = None
    if not args.no_embed:
        from embeddings.local_generator import LocalEmbeddingGenerator
        embedding_generator = LocalEmbeddingGenerator()
        logger.info("Embedding generator initialized")

    # Get repositories
    if args.repo:
        repo_folder = args.repo.replace('/', '_')
        repo_path = Path(config.repos_path) / repo_folder
        if not repo_path.exists():
            logger.error(f"Repository not found: {repo_path}")
            sys.exit(1)
        repositories = [{"repo_id": args.repo, "repo_path": str(repo_path)}]
    else:
        repositories = discover_repositories()

    # Process
    total_commits = 0
    total_stored = 0

    for i, repo in enumerate(repositories, 1):
        repo_id = repo["repo_id"]
        repo_path = Path(repo["repo_path"])

        logger.info(f"[{i}/{len(repositories)}] {repo_id}")

        # Check existing
        if args.skip_existing and cb_client:
            existing = count_existing_commits(cb_client, repo_id)
            if existing > 0:
                logger.info(f"  Skipping - {existing} commits exist")
                continue

        # Extract commits
        commits = extract_commits_with_stats(repo_path, repo_id, args.limit)
        logger.info(f"  Extracted {len(commits)} commits")

        if not commits:
            continue

        total_commits += len(commits)

        # Generate embeddings if requested
        if embedding_generator and commits:
            logger.info(f"  Generating embeddings...")
            for commit in commits:
                # Embed commit message
                # Prefixing belongs to `embeddings.convention`; doing it here
                # too is what double-prefixed the commit embeddings.
                commit.embedding = embedding_generator.generate_embedding(commit.content)

        # Store
        if cb_client and not args.dry_run:
            stored = 0
            for commit in commits:
                try:
                    doc = commit.to_dict()
                    cb_client.collection.upsert(commit.document_id, doc)
                    stored += 1
                except Exception as e:
                    logger.error(f"  Failed to store {commit.commit_hash[:8]}: {e}")

            logger.info(f"  Stored {stored} commits")
            total_stored += stored
        else:
            # Dry run - show sample
            if commits:
                sample = commits[0]
                logger.info(f"  Sample: {sample.commit_hash[:8]} | +{sample.lines_added}/-{sample.lines_deleted} | {len(sample.files_changed)} files")

    # Summary
    print("\n" + "=" * 60)
    print("COMMIT BACKFILL COMPLETE")
    print("=" * 60)
    print(f"  Repositories: {len(repositories)}")
    print(f"  Total commits: {total_commits}")
    if not args.dry_run:
        print(f"  Stored: {total_stored}")
    print("=" * 60)


if __name__ == "__main__":
    main()
