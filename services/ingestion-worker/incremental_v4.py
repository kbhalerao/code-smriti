#!/usr/bin/env python3
"""
Incremental V4 Update - Git-based change detection with surgical updates.

Compares origin HEAD to stored commit, processes only changed files.
Falls back to full re-ingestion if >threshold% of files changed.

Usage:
    python incremental_v4.py                    # All repos
    python incremental_v4.py --repo owner/name  # Single repo
    python incremental_v4.py --dry-run          # Preview changes
    python incremental_v4.py --threshold 0.10   # 10% threshold

Strategy:
    1. git fetch origin for each repo
    2. Compare origin/main HEAD to stored commit (in repo_summary)
    3. If same → skip
    4. If different:
       - Get changed files via git diff
       - If >threshold% changed → full re-ingest
       - Otherwise → surgical update:
         a. Delete docs for deleted files
         b. Process only changed files (reusing V4Pipeline)
         c. Regenerate affected module_summary and repo_summary
    5. Run doc ingestion for changed .md/.rst/.txt files
"""

import argparse
import asyncio
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass

from loguru import logger

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent))

from config import WorkerConfig
from v4.pipeline import V4Pipeline
from v4.ingest_docs import DocumentIngester
from storage.couchbase_client import CouchbaseClient
from llm_enricher import LMSTUDIO_CONFIG, OLLAMA_CONFIG
from parsers.code_parser import should_skip_file

config = WorkerConfig()


@dataclass
class ChangeSet:
    """Files changed between two commits"""
    added: List[str]
    modified: List[str]
    deleted: List[str]

    @property
    def total_changed(self) -> int:
        return len(self.added) + len(self.modified) + len(self.deleted)

    @property
    def files_to_process(self) -> List[str]:
        return self.added + self.modified

    def __bool__(self):
        return self.total_changed > 0


@dataclass
class UpdateResult:
    """Result of processing a single repo"""
    repo_id: str
    status: str  # 'skipped', 'updated', 'full_reingest', 'error'
    reason: Optional[str] = None
    files_processed: int = 0
    files_deleted: int = 0
    error: Optional[str] = None
    duration_seconds: float = 0


class IncrementalUpdater:
    """
    Git-based incremental updater for V4 pipeline.

    Full lifecycle:
    1. Get canonical repo list (GitHub API or config file)
    2. Clone new repos not on disk
    3. Delete docs for repos no longer in canonical list
    4. For each repo: fetch, compare commits, update incrementally or full re-ingest
    """

    def __init__(
        self,
        threshold: float = 0.05,
        dry_run: bool = False,
        enable_llm: bool = True,
        llm_config = None
    ):
        self.threshold = threshold
        self.dry_run = dry_run
        self.enable_llm = enable_llm
        self.llm_config = llm_config or LMSTUDIO_CONFIG
        self.repos_path = Path(config.repos_path)

        # Initialize storage
        self.cb_client = CouchbaseClient()

        # Full pipeline for processing
        self.pipeline = V4Pipeline(
            enable_llm=enable_llm,
            enable_embeddings=True,
            dry_run=dry_run,
            llm_config=self.llm_config
        )

    # =========================================================================
    # Repository Discovery
    # =========================================================================

    def get_canonical_repo_list(self) -> List[str]:
        """
        Get the canonical list of repos that should be indexed.

        Sources (in order of preference):
        1. GitHub API (if GITHUB_TOKEN is set)
        2. repos_to_ingest.txt config file
        3. Existing repos on disk (fallback)

        Returns:
            List of repo_ids (owner/name format)
        """
        # Try GitHub API first
        if config.github_token:
            repos = self._get_repos_from_github()
            if repos:
                logger.info(f"Got {len(repos)} repos from GitHub API")
                return repos

        # Try config file
        config_file = self.repos_path.parent / "repos_to_ingest.txt"
        if config_file.exists():
            repos = self._get_repos_from_config(config_file)
            if repos:
                logger.info(f"Got {len(repos)} repos from config file")
                return repos

        # Fallback to disk
        logger.warning("No GitHub token or config file - using repos on disk")
        return [r['repo_id'] for r in self.discover_repos_on_disk()]

    def _get_repos_from_github(self) -> List[str]:
        """Query GitHub API for user's repos"""
        try:
            import httpx

            headers = {
                "Authorization": f"token {config.github_token}",
                "Accept": "application/vnd.github.v3+json"
            }

            repos = []
            page = 1

            while True:
                response = httpx.get(
                    f"https://api.github.com/user/repos?per_page=100&page={page}",
                    headers=headers,
                    timeout=30
                )

                if response.status_code != 200:
                    logger.error(f"GitHub API error: {response.status_code}")
                    return []

                data = response.json()
                if not data:
                    break

                for repo in data:
                    repos.append(repo['full_name'])

                page += 1

            return repos

        except Exception as e:
            logger.error(f"Failed to query GitHub API: {e}")
            return []

    def _get_repos_from_config(self, config_file: Path) -> List[str]:
        """Read repos from config file (one per line, # comments)"""
        repos = []
        try:
            with open(config_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    # Handle inline comments
                    repo_id = line.split('#')[0].strip()
                    if repo_id and '/' in repo_id:
                        repos.append(repo_id)
        except Exception as e:
            logger.error(f"Failed to read config file: {e}")
        return repos

    def discover_repos_on_disk(self) -> List[Dict[str, str]]:
        """Discover all repos currently on disk"""
        if not self.repos_path.exists():
            logger.error(f"Repos path does not exist: {self.repos_path}")
            return []

        repositories = []
        for repo_dir in sorted(self.repos_path.iterdir()):
            if not repo_dir.is_dir() or repo_dir.name.startswith('.'):
                continue

            # Convert folder name to repo_id: owner_repo -> owner/repo
            parts = repo_dir.name.split('_', 1)
            if len(parts) == 2:
                repo_id = f"{parts[0]}/{parts[1]}"
                repositories.append({
                    'repo_id': repo_id,
                    'repo_path': str(repo_dir)
                })

        return repositories

    def get_repos_in_database(self) -> Set[str]:
        """Get all repo_ids that have documents in Couchbase"""
        try:
            query = """
                SELECT DISTINCT repo_id
                FROM `code_kosha`
                WHERE repo_id IS NOT MISSING
            """
            result = self.cb_client.cluster.query(query)
            return {row['repo_id'] for row in result}
        except Exception as e:
            logger.error(f"Failed to query repos from database: {e}")
            return set()

    # =========================================================================
    # Repository Cloning
    # =========================================================================

    def repo_id_to_path(self, repo_id: str) -> Path:
        """Convert repo_id to disk path: owner/repo -> owner_repo"""
        folder_name = repo_id.replace('/', '_')
        return self.repos_path / folder_name

    def clone_repo(self, repo_id: str) -> bool:
        """Clone a repo from GitHub"""
        repo_path = self.repo_id_to_path(repo_id)

        if repo_path.exists():
            logger.debug(f"Repo already exists: {repo_path}")
            return True

        if self.dry_run:
            logger.info(f"[DRY RUN] Would clone {repo_id}")
            return True

        try:
            # Construct clone URL
            if config.github_token:
                clone_url = f"https://{config.github_token}@github.com/{repo_id}.git"
            else:
                clone_url = f"https://github.com/{repo_id}.git"

            logger.info(f"Cloning {repo_id}...")
            result = subprocess.run(
                ['git', 'clone', '--depth', '1', clone_url, str(repo_path)],
                capture_output=True,
                text=True,
                timeout=300  # 5 min timeout
            )

            if result.returncode != 0:
                logger.error(f"Clone failed for {repo_id}: {result.stderr}")
                return False

            logger.info(f"Cloned {repo_id} to {repo_path}")
            return True

        except subprocess.TimeoutExpired:
            logger.error(f"Clone timed out for {repo_id}")
            return False
        except Exception as e:
            logger.error(f"Clone failed for {repo_id}: {e}")
            return False

    # =========================================================================
    # Repository Deletion
    # =========================================================================

    def delete_repo_docs(self, repo_id: str) -> int:
        """Delete all documents for a repo from Couchbase"""
        if self.dry_run:
            logger.info(f"[DRY RUN] Would delete all docs for {repo_id}")
            return 0

        try:
            query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
            """
            result = self.cb_client.cluster.query(query, repo_id=repo_id)
            metrics = result.metadata().metrics()
            deleted = metrics.mutation_count() if metrics else 0
            logger.info(f"Deleted {deleted} documents for {repo_id}")
            return deleted
        except Exception as e:
            logger.error(f"Failed to delete docs for {repo_id}: {e}")
            return 0

    def delete_repo_from_disk(self, repo_id: str) -> bool:
        """Delete a repo from disk"""
        repo_path = self.repo_id_to_path(repo_id)

        if not repo_path.exists():
            return True

        if self.dry_run:
            logger.info(f"[DRY RUN] Would delete {repo_path}")
            return True

        try:
            import shutil
            shutil.rmtree(repo_path)
            logger.info(f"Deleted {repo_path}")
            return True
        except Exception as e:
            logger.error(f"Failed to delete {repo_path}: {e}")
            return False

    def get_stored_commit(self, repo_id: str) -> Optional[str]:
        """Get stored commit hash from repo_summary document"""
        try:
            query = """
                SELECT commit_hash
                FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type = 'repo_summary'
                LIMIT 1
            """
            result = self.cb_client.cluster.query(query, repo_id=repo_id)
            for row in result:
                return row.get('commit_hash')
            return None
        except Exception as e:
            logger.warning(f"Could not get stored commit for {repo_id}: {e}")
            return None

    def get_repo_file_count(self, repo_id: str) -> int:
        """Get total file_index count for threshold calculation"""
        try:
            query = """
                SELECT COUNT(*) as count
                FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type = 'file_index'
            """
            result = self.cb_client.cluster.query(query, repo_id=repo_id)
            for row in result:
                return row.get('count', 0)
            return 0
        except Exception:
            return 0

    def git_fetch(self, repo_path: Path) -> bool:
        """Fetch latest from origin"""
        try:
            result = subprocess.run(
                ['git', 'fetch', 'origin'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=60
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Git fetch failed: {e}")
            return False

    def git_pull(self, repo_path: Path) -> bool:
        """Pull latest changes"""
        try:
            result = subprocess.run(
                ['git', 'pull', '--ff-only'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=120
            )
            return result.returncode == 0
        except Exception as e:
            logger.warning(f"Git pull failed: {e}")
            return False

    def get_head_commit(self, repo_path: Path, ref: str = 'HEAD') -> Optional[str]:
        """Get commit hash for a ref"""
        try:
            result = subprocess.run(
                ['git', 'rev-parse', ref],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
            return None
        except Exception:
            return None

    def get_origin_head(self, repo_path: Path, branch: str = 'main') -> Optional[str]:
        """Get origin's HEAD commit"""
        # Try main, then master
        for b in [branch, 'master']:
            commit = self.get_head_commit(repo_path, f'origin/{b}')
            if commit:
                return commit
        return None

    def get_default_branch(self, repo_path: Path) -> str:
        """Detect default branch (main or master)"""
        try:
            result = subprocess.run(
                ['git', 'symbolic-ref', 'refs/remotes/origin/HEAD'],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                # refs/remotes/origin/main -> main
                return result.stdout.strip().split('/')[-1]
        except Exception:
            pass
        return 'main'

    def get_changed_files(self, repo_path: Path, old_commit: str, new_commit: str) -> ChangeSet:
        """Get list of changed files between two commits"""
        try:
            result = subprocess.run(
                ['git', 'diff', '--name-status', old_commit, new_commit],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                logger.warning(f"Git diff failed: {result.stderr}")
                return ChangeSet([], [], [])

            added, modified, deleted = [], [], []

            for line in result.stdout.strip().split('\n'):
                if not line:
                    continue

                parts = line.split('\t')
                status = parts[0]

                if status.startswith('A'):
                    added.append(parts[1])
                elif status.startswith('M'):
                    modified.append(parts[1])
                elif status.startswith('D'):
                    deleted.append(parts[1])
                elif status.startswith('R'):
                    # Rename = delete old + add new
                    deleted.append(parts[1])
                    added.append(parts[2])
                elif status.startswith('C'):
                    # Copy = just add new
                    added.append(parts[2])

            return ChangeSet(added, modified, deleted)

        except Exception as e:
            logger.error(f"Error getting changed files: {e}")
            return ChangeSet([], [], [])

    def filter_supported_files(self, files: List[str], repo_path: Path) -> Tuple[List[str], List[str]]:
        """
        Filter files into code and doc files.
        Returns (code_files, doc_files)
        """
        code_extensions = set(config.supported_code_extensions)
        doc_extensions = {'.md', '.rst', '.txt'}

        code_files = []
        doc_files = []

        for f in files:
            ext = Path(f).suffix.lower()
            full_path = repo_path / f

            # Skip files that should be skipped (vendor, node_modules, etc.)
            if should_skip_file(full_path):
                continue

            if ext in code_extensions:
                code_files.append(f)
            elif ext in doc_extensions:
                doc_files.append(f)

        return code_files, doc_files

    def delete_file_docs(self, repo_id: str, file_path: str) -> int:
        """Delete all documents for a specific file"""
        if self.dry_run:
            logger.info(f"  [DRY RUN] Would delete docs for {file_path}")
            return 0

        try:
            # Delete file_index and symbol_index for this file
            query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND file_path = $file_path
                  AND type IN ['file_index', 'symbol_index']
            """
            result = self.cb_client.cluster.query(
                query,
                repo_id=repo_id,
                file_path=file_path
            )
            # Get mutation count
            metrics = result.metadata().metrics()
            deleted = metrics.mutation_count() if metrics else 0
            return deleted
        except Exception as e:
            logger.error(f"Error deleting docs for {file_path}: {e}")
            return 0

    def delete_doc_chunks(self, repo_id: str, file_path: str) -> int:
        """Delete document chunks for a specific file"""
        if self.dry_run:
            return 0

        try:
            query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND file_path = $file_path
                  AND type = 'document'
            """
            result = self.cb_client.cluster.query(
                query,
                repo_id=repo_id,
                file_path=file_path
            )
            metrics = result.metadata().metrics()
            return metrics.mutation_count() if metrics else 0
        except Exception as e:
            logger.error(f"Error deleting doc chunks for {file_path}: {e}")
            return 0

    def get_affected_modules(self, file_paths: List[str]) -> Set[str]:
        """Get all module paths affected by file changes"""
        modules = set()
        for fp in file_paths:
            parts = Path(fp).parts[:-1]  # Directory path
            # Add all ancestor directories
            for i in range(len(parts) + 1):
                module_path = '/'.join(parts[:i]) if i > 0 else ''
                modules.add(module_path)
        return modules

    def process_repo(self, repo_id: str, repo_path: Path) -> UpdateResult:
        """Process a single repository with incremental update logic"""
        start_time = datetime.now()

        logger.info(f"\nProcessing {repo_id}")

        # 1. Fetch latest from origin
        if not self.git_fetch(repo_path):
            return UpdateResult(
                repo_id=repo_id,
                status='error',
                error='Git fetch failed'
            )

        # 2. Get commits
        local_head = self.get_head_commit(repo_path)
        origin_head = self.get_origin_head(repo_path)
        stored_commit = self.get_stored_commit(repo_id)

        if not origin_head:
            return UpdateResult(
                repo_id=repo_id,
                status='error',
                error='Could not determine origin HEAD'
            )

        # 3. Check if update needed
        if stored_commit and local_head == origin_head == stored_commit:
            logger.info(f"  Skipping - no changes (commit: {stored_commit[:8]})")
            return UpdateResult(
                repo_id=repo_id,
                status='skipped',
                reason='no_changes',
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 4. Determine base commit for diff
        if stored_commit:
            base_commit = stored_commit
        else:
            # New repo - need full ingestion
            logger.info(f"  New repo - full ingestion")
            if not self.dry_run:
                self.git_pull(repo_path)
                result = self.pipeline.process_repository(repo_id, str(repo_path))
            return UpdateResult(
                repo_id=repo_id,
                status='full_reingest',
                reason='new_repo',
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 5. Get changed files
        changes = self.get_changed_files(repo_path, base_commit, origin_head)

        if not changes:
            logger.info(f"  Skipping - no file changes")
            return UpdateResult(
                repo_id=repo_id,
                status='skipped',
                reason='no_file_changes',
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 6. Check threshold
        total_files = self.get_repo_file_count(repo_id)
        if total_files == 0:
            total_files = 1  # Avoid division by zero for new repos

        change_ratio = changes.total_changed / total_files

        if change_ratio > self.threshold:
            logger.info(f"  {changes.total_changed} files changed ({change_ratio:.1%}) > {self.threshold:.0%} threshold - full re-ingestion")
            if not self.dry_run:
                self.git_pull(repo_path)
                self.pipeline.process_repository(repo_id, str(repo_path))
            return UpdateResult(
                repo_id=repo_id,
                status='full_reingest',
                reason=f'threshold_exceeded ({change_ratio:.1%})',
                files_processed=changes.total_changed,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 7. Surgical incremental update
        logger.info(f"  Incremental: +{len(changes.added)} ~{len(changes.modified)} -{len(changes.deleted)}")

        # Pull to get latest files
        if not self.dry_run:
            self.git_pull(repo_path)

        # Filter to supported files
        code_to_process, docs_to_process = self.filter_supported_files(
            changes.files_to_process, repo_path
        )
        code_deleted, docs_deleted = self.filter_supported_files(
            changes.deleted, repo_path
        )

        files_deleted = 0
        files_processed = 0

        # 7a. Delete docs for deleted files
        for file_path in code_deleted:
            deleted = self.delete_file_docs(repo_id, file_path)
            files_deleted += 1
            logger.debug(f"    Deleted {deleted} docs for {file_path}")

        for file_path in docs_deleted:
            self.delete_doc_chunks(repo_id, file_path)
            files_deleted += 1

        if self.dry_run:
            logger.info(f"  [DRY RUN] Would process {len(code_to_process)} code files, {len(docs_to_process)} doc files")
            return UpdateResult(
                repo_id=repo_id,
                status='updated',
                reason='dry_run',
                files_processed=len(code_to_process) + len(docs_to_process),
                files_deleted=files_deleted,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 7b. Process changed code files
        file_indices = []
        all_symbol_indices = []

        for file_path in code_to_process:
            full_path = repo_path / file_path
            if not full_path.exists():
                continue

            # Delete existing docs for atomic replace
            self.delete_file_docs(repo_id, file_path)

            # Process file
            try:
                file_index, symbol_indices = self.file_processor.process(
                    file_path=full_path,
                    repo_path=repo_path,
                    repo_id=repo_id,
                    commit_hash=origin_head
                )

                if file_index:
                    file_indices.append(file_index)
                    all_symbol_indices.extend(symbol_indices)
                    files_processed += 1
                    logger.debug(f"    Processed {file_path} ({len(symbol_indices)} symbols)")

            except Exception as e:
                logger.error(f"    Error processing {file_path}: {e}")

        # 7c. Generate embeddings for new docs
        all_docs = file_indices + all_symbol_indices
        if all_docs:
            self.embedding_generator.generate_embeddings_batch(all_docs)

            # Store documents
            for doc in all_docs:
                self.cb_client.upsert_v4_document(doc)

        # 7d. Regenerate affected module summaries
        affected_modules = self.get_affected_modules(
            code_to_process + code_deleted
        )
        logger.debug(f"    Regenerating {len(affected_modules)} module summaries")

        # Get all file_indices for affected modules to regenerate summaries
        # This is the "bubbling up" - regenerate from changed files upward
        self.regenerate_summaries(repo_id, origin_head, affected_modules)

        # 7e. Process changed doc files
        if docs_to_process or docs_deleted:
            doc_ingester = DocumentIngester(dry_run=self.dry_run)
            # Process only changed docs
            for file_path in docs_to_process:
                full_path = repo_path / file_path
                if full_path.exists():
                    self.delete_doc_chunks(repo_id, file_path)
                    asyncio.run(doc_ingester.process_doc(
                        full_path, repo_path, repo_id
                    ))
                    files_processed += 1

        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"  Completed in {duration:.1f}s: {files_processed} processed, {files_deleted} deleted")

        return UpdateResult(
            repo_id=repo_id,
            status='updated',
            files_processed=files_processed,
            files_deleted=files_deleted,
            duration_seconds=duration
        )

    def get_old_repo_summary(self, repo_id: str) -> Optional[str]:
        """Fetch existing repo_summary from database for comparison"""
        try:
            query = """
                SELECT summary
                FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type = 'repo_summary'
                LIMIT 1
            """
            result = self.cb_client.cluster.query(query, repo_id=repo_id)
            for row in result:
                return row.get('summary', '')
            return None
        except Exception:
            return None

    def regenerate_summaries(self, repo_id: str, commit_hash: str, affected_modules: Set[str]):
        """Regenerate module_summary and repo_summary for affected paths"""
        try:
            # Get all file_indices for this repo
            query = """
                SELECT META().id, *
                FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type = 'file_index'
            """
            result = self.cb_client.cluster.query(query, repo_id=repo_id)
            file_indices = [row for row in result]

            if not file_indices:
                return

            # For dry-run: get old summary for comparison
            old_repo_summary = None
            if self.dry_run:
                old_repo_summary = self.get_old_repo_summary(repo_id)

            # Convert to schema objects for aggregator
            from v4.schemas import FileIndex
            file_index_objects = []
            for row in file_indices:
                # Reconstruct FileIndex from DB row
                fi = FileIndex(
                    repo_id=row.get('repo_id'),
                    file_path=row.get('file_path'),
                    language=row.get('language'),
                    summary=row.get('summary', ''),
                    symbols=row.get('symbols', []),
                    imports=row.get('imports', []),
                    line_count=row.get('line_count', 0),
                    commit_hash=commit_hash
                )
                file_index_objects.append(fi)

            # Regenerate summaries (LLM runs even in dry-run mode)
            module_summaries, repo_summary = self.pipeline.aggregator.aggregate_all(
                file_index_objects, repo_id, commit_hash
            )

            # Dry-run: Show summary comparison instead of storing
            if self.dry_run:
                logger.info("\n" + "=" * 70)
                logger.info("DRY RUN: Summary Comparison")
                logger.info("=" * 70)

                if old_repo_summary:
                    logger.info("\n--- OLD repo_summary ---")
                    logger.info(old_repo_summary[:500] + "..." if len(old_repo_summary) > 500 else old_repo_summary)

                logger.info("\n--- NEW repo_summary ---")
                new_summary = repo_summary.summary if hasattr(repo_summary, 'summary') else str(repo_summary)
                logger.info(new_summary[:500] + "..." if len(new_summary) > 500 else new_summary)

                # Show affected modules
                logger.info(f"\n--- Affected modules ({len(affected_modules)}) ---")
                for m in sorted(affected_modules)[:10]:
                    logger.info(f"  {m or '(root)'}")
                if len(affected_modules) > 10:
                    logger.info(f"  ... and {len(affected_modules) - 10} more")

                return

            # Production: Delete old summaries and store new ones
            delete_query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type IN ['module_summary', 'repo_summary']
            """
            self.cb_client.cluster.query(delete_query, repo_id=repo_id)

            # Generate embeddings and store new summaries
            all_summaries = module_summaries + [repo_summary]
            if self.pipeline.embedding_generator:
                self.pipeline.embedding_generator.generate_embeddings_batch(all_summaries)

            for summary in all_summaries:
                doc = summary.to_dict()
                self.cb_client.collection.upsert(summary.document_id, doc)

        except Exception as e:
            logger.error(f"Error regenerating summaries: {e}")

    def run(self, repo_filter: Optional[str] = None) -> List[UpdateResult]:
        """
        Run incremental update for all repos.

        Full lifecycle:
        1. Get canonical repo list
        2. Identify: new repos, existing repos, orphaned repos
        3. Clone new repos
        4. Delete orphaned repo docs
        5. Process each repo (incremental or full)
        """
        logger.info("=" * 70)
        logger.info("INCREMENTAL V4 UPDATE")
        logger.info("=" * 70)
        logger.info(f"Threshold: {self.threshold:.0%}")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info(f"LLM enabled: {self.enable_llm}")

        # Phase 1: Get canonical repo list
        logger.info("\n" + "-" * 70)
        logger.info("Phase 1: Repository Discovery")
        logger.info("-" * 70)

        if repo_filter:
            canonical_repos = {repo_filter}
            logger.info(f"Single repo mode: {repo_filter}")
        else:
            canonical_repos = set(self.get_canonical_repo_list())
            logger.info(f"Canonical repo list: {len(canonical_repos)} repos")

        repos_on_disk = {r['repo_id'] for r in self.discover_repos_on_disk()}
        repos_in_db = self.get_repos_in_database()

        # Categorize repos
        new_repos = canonical_repos - repos_on_disk  # Need to clone
        orphaned_in_db = repos_in_db - canonical_repos  # Need to delete from DB
        orphaned_on_disk = repos_on_disk - canonical_repos  # Could delete from disk
        repos_to_process = canonical_repos & repos_on_disk  # Existing, check for updates

        logger.info(f"\nRepository status:")
        logger.info(f"  To clone (new):     {len(new_repos)}")
        logger.info(f"  To process:         {len(repos_to_process)}")
        logger.info(f"  Orphaned in DB:     {len(orphaned_in_db)}")
        logger.info(f"  Orphaned on disk:   {len(orphaned_on_disk)}")

        results = []
        stats = {
            'cloned': 0, 'skipped': 0, 'updated': 0,
            'full_reingest': 0, 'deleted': 0, 'error': 0
        }

        # Phase 2: Clone new repos
        if new_repos:
            logger.info("\n" + "-" * 70)
            logger.info(f"Phase 2: Cloning {len(new_repos)} New Repos")
            logger.info("-" * 70)

            for repo_id in sorted(new_repos):
                if self.clone_repo(repo_id):
                    repos_to_process.add(repo_id)
                    stats['cloned'] += 1
                else:
                    results.append(UpdateResult(
                        repo_id=repo_id,
                        status='error',
                        error='Clone failed'
                    ))
                    stats['error'] += 1

        # Phase 3: Delete orphaned repos from DB
        if orphaned_in_db and not repo_filter:
            logger.info("\n" + "-" * 70)
            logger.info(f"Phase 3: Cleaning {len(orphaned_in_db)} Orphaned Repos")
            logger.info("-" * 70)

            for repo_id in sorted(orphaned_in_db):
                deleted = self.delete_repo_docs(repo_id)
                results.append(UpdateResult(
                    repo_id=repo_id,
                    status='deleted',
                    reason='orphaned',
                    files_deleted=deleted
                ))
                stats['deleted'] += 1

        # Phase 4: Process repos (incremental update)
        logger.info("\n" + "-" * 70)
        logger.info(f"Phase 4: Processing {len(repos_to_process)} Repos")
        logger.info("-" * 70)

        for repo_id in sorted(repos_to_process):
            repo_path = self.repo_id_to_path(repo_id)

            try:
                result = self.process_repo(repo_id, repo_path)
                results.append(result)
                stats[result.status] = stats.get(result.status, 0) + 1
            except Exception as e:
                logger.error(f"Failed to process {repo_id}: {e}")
                results.append(UpdateResult(
                    repo_id=repo_id,
                    status='error',
                    error=str(e)
                ))
                stats['error'] += 1

        # Summary
        logger.info("\n" + "=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Total repos processed: {len(results)}")
        logger.info(f"  Cloned:        {stats['cloned']}")
        logger.info(f"  Skipped:       {stats['skipped']}")
        logger.info(f"  Updated:       {stats['updated']}")
        logger.info(f"  Full reingest: {stats['full_reingest']}")
        logger.info(f"  Deleted:       {stats['deleted']}")
        logger.info(f"  Errors:        {stats['error']}")

        total_files = sum(r.files_processed for r in results)
        total_deleted = sum(r.files_deleted for r in results)
        total_time = sum(r.duration_seconds for r in results)

        logger.info(f"\nFiles processed: {total_files}")
        logger.info(f"Files deleted:   {total_deleted}")
        logger.info(f"Total time:      {total_time:.1f}s")

        return results


def main():
    parser = argparse.ArgumentParser(
        description="Incremental V4 Update - Git-based change detection",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__
    )

    parser.add_argument(
        "--repo",
        type=str,
        help="Single repo to update (format: owner/name)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing"
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.05,
        help="Change threshold for full re-ingestion (default: 0.05 = 5%%)"
    )
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="Disable LLM (basic summaries only)"
    )
    parser.add_argument(
        "--llm-provider",
        choices=["lmstudio", "ollama"],
        default="lmstudio",
        help="LLM provider (default: lmstudio)"
    )

    args = parser.parse_args()

    # Configure logging
    logger.remove()
    logger.add(
        sys.stderr,
        format="<green>{time:HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>",
        level="INFO"
    )

    # Select LLM config
    llm_config = LMSTUDIO_CONFIG if args.llm_provider == "lmstudio" else OLLAMA_CONFIG

    updater = IncrementalUpdater(
        threshold=args.threshold,
        dry_run=args.dry_run,
        enable_llm=not args.no_llm,
        llm_config=llm_config
    )

    results = updater.run(repo_filter=args.repo)

    # Exit with error if any failures
    if any(r.status == 'error' for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
