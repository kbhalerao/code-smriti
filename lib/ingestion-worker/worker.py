"""
CodeSmriti Ingestion Worker
Background service for processing and indexing code repositories
Smriti (स्मृति): Sanskrit for "memory, remembrance"
"""

import os
import asyncio
import time
from typing import List, Optional
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from loguru import logger
import git
from github import Github

# Import custom modules
from parsers.code_parser import CodeParser
from parsers.document_parser import DocumentParser
from parsers.commit_parser import CommitParser
from embeddings.generator import EmbeddingGenerator
from embeddings.local_generator import LocalEmbeddingGenerator
from storage.couchbase_client import CouchbaseClient

# Configuration
from config import WorkerConfig

config = WorkerConfig()


class IngestionWorker:
    """
    Background worker for ingesting and indexing repositories
    """

    def __init__(self):
        """Initialize the ingestion worker"""
        logger.info("Initializing CodeSmriti Ingestion Worker")

        # Initialize GitHub client
        self.github = Github(config.github_token) if config.github_token else None

        # Initialize parsers
        self.code_parser = CodeParser()
        self.doc_parser = DocumentParser()
        self.commit_parser = CommitParser()

        # Initialize embedding generator based on config
        if config.embedding_backend == "local":
            logger.info("Using local sentence-transformers for embeddings")
            self.embedding_generator = LocalEmbeddingGenerator()
        elif config.embedding_backend == "ollama":
            logger.info("Using Ollama API for embeddings")
            self.embedding_generator = EmbeddingGenerator()
        else:
            logger.error(f"Unknown embedding backend: {config.embedding_backend}")
            raise ValueError(f"Invalid embedding_backend: {config.embedding_backend}. Use 'local' or 'ollama'")

        # Initialize Couchbase client
        self.db = CouchbaseClient()

        # Concurrency control for file-atomic processing
        self.file_semaphore = asyncio.Semaphore(config.max_concurrent_files)

        # Repository storage path
        # Support both Docker (/repos) and native execution (configurable via REPOS_PATH)
        repos_base = os.getenv("REPOS_PATH", "/repos")
        self.repos_path = Path(repos_base).resolve()
        self.repos_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Worker initialized. Repos path: {self.repos_path}")
        logger.info(f"Async pipeline: {config.max_concurrent_files} concurrent files, {config.max_parsing_threads} parsing threads")

    async def clone_or_update_repo(self, repo_id: str) -> Path:
        """
        Clone or update a GitHub repository

        Args:
            repo_id: Repository in owner/repo format

        Returns:
            Path to the local repository
        """
        try:
            repo_path = self.repos_path / repo_id.replace("/", "_")

            if repo_path.exists():
                logger.info(f"Repository already exists: {repo_id}")
                try:
                    # Try to update, but don't fail if git credentials aren't configured
                    repo = git.Repo(repo_path)
                    logger.info(f"Pulling latest changes for {repo_id}...")
                    repo.remotes.origin.pull()
                    logger.info(f"✓ Repository updated: {repo_id}")
                except Exception as e:
                    logger.warning(f"Could not update repo (using existing version): {e}")
                    # Continue with existing version of the repo
            else:
                logger.info(f"Cloning new repo: {repo_id}")

                # Configure git environment to prevent interactive prompts
                env = os.environ.copy()
                env['GIT_TERMINAL_PROMPT'] = '0'  # Disable git credential prompts
                env['GIT_ASKPASS'] = 'echo'  # Prevent password prompts

                # Use token for authentication if available
                if config.github_token:
                    # Validate token format
                    token = config.github_token.strip()
                    if not token:
                        raise ValueError("GITHUB_TOKEN is empty or whitespace")

                    # GitHub tokens should start with ghp_, gho_, ghu_, ghs_, or ghr_ (or github_pat_ for fine-grained)
                    if not any(token.startswith(prefix) for prefix in ['ghp_', 'gho_', 'ghu_', 'ghs_', 'ghr_', 'github_pat_']):
                        logger.warning(f"GitHub token doesn't match expected format. Token starts with: {token[:8]}...")

                    # URL encode the token to handle any special characters
                    encoded_token = quote(token, safe='')

                    # Use token as password with 'x-access-token' as username (recommended by GitHub)
                    # This is the official GitHub HTTPS authentication method for tokens
                    clone_url = f"https://x-access-token:{encoded_token}@github.com/{repo_id}.git"
                    logger.debug(f"Using authenticated clone URL (token starts with {token[:8]}...)")
                else:
                    logger.warning("No GitHub token provided. Attempting anonymous clone (may fail for private repos)")
                    clone_url = f"https://github.com/{repo_id}.git"

                git.Repo.clone_from(clone_url, repo_path, env=env)

            logger.info(f"✓ Repository ready: {repo_path}")
            return repo_path

        except Exception as e:
            logger.error(f"Error cloning/updating {repo_id}: {e}")
            if config.github_token:
                logger.error(f"Token format check: starts with '{config.github_token[:4]}...', length: {len(config.github_token)}")
            else:
                logger.error("No GitHub token configured")
            raise

    def filter_chunks_by_file_changes(self, chunks: List, repo_id: str, repo_path: Path) -> tuple:
        """
        Filter chunks to only include those from new or changed files
        Uses set differences for efficient change detection (1 query vs N queries)
        Returns (new_chunks, stats)

        Args:
            chunks: List of parsed chunks
            repo_id: Repository identifier
            repo_path: Path to repository

        Returns:
            Tuple of (filtered_chunks, stats_dict)
        """
        if not chunks:
            return [], {"skipped": 0, "new": 0, "updated": 0, "deleted": 0}

        # Optimization: Get all stored files + commits in one query
        stored_file_commits = self.db.get_repo_file_commits(repo_id)

        if not stored_file_commits:
            # First run - no files in DB yet
            logger.info(f"First ingestion for {repo_id} - marking all {len(chunks)} chunks as new")
            file_paths = set(chunk.file_path for chunk in chunks)
            return chunks, {
                "skipped": 0,
                "new": len(file_paths),
                "updated": 0,
                "deleted": 0
            }

        # Build current file -> commit mapping
        current_file_commits = {}
        file_paths = set(chunk.file_path for chunk in chunks)
        for file_path in file_paths:
            git_metadata = self.code_parser.get_git_metadata(repo_path, file_path)
            current_file_commits[file_path] = git_metadata.get("commit_hash", "")

        # Use set differences to categorize files
        stored_files = set(stored_file_commits.keys())
        current_files = set(current_file_commits.keys())

        files_new = current_files - stored_files  # In current but not in stored
        files_deleted = stored_files - current_files  # In stored but not in current
        files_potentially_unchanged = current_files & stored_files  # In both

        # Check which files actually changed (compare commit hashes)
        files_updated = set()
        files_unchanged = set()
        for file_path in files_potentially_unchanged:
            stored_commit = stored_file_commits[file_path]
            current_commit = current_file_commits[file_path]

            if stored_commit != current_commit:
                files_updated.add(file_path)
            else:
                files_unchanged.add(file_path)

        # Delete chunks for updated and deleted files
        deleted_chunks = 0
        for file_path in files_updated | files_deleted:
            deleted = self.db.delete_file_chunks(repo_id, file_path)
            deleted_chunks += deleted

        # Files to process: new + updated
        files_to_process = files_new | files_updated

        # Filter chunks to only include files that need processing
        filtered_chunks = [c for c in chunks if c.file_path in files_to_process]

        stats = {
            "skipped": len(files_unchanged),
            "new": len(files_new),
            "updated": len(files_updated),
            "deleted": deleted_chunks
        }

        return filtered_chunks, stats

    async def process_file_atomic(
        self,
        file_path: Path,
        repo_path: Path,
        repo_id: str,
        file_type: str,
        stored_file_commits: dict
    ) -> Optional[List]:
        """
        Process a single file atomically: parse → check → embed → upsert

        Args:
            file_path: Path to the file
            repo_path: Path to the repository root
            repo_id: Repository identifier
            file_type: "code" or "doc"
            stored_file_commits: Dictionary of file_path → commit_hash from DB

        Returns:
            List of chunks (for commit extraction) or None if skipped
        """
        async with self.file_semaphore:  # Limit concurrency
            try:
                relative_path = str(file_path.relative_to(repo_path))

                # Step 1: Parse file (async - parsers handle their own CPU-bound work)
                if file_type == "code":
                    chunks = await self.code_parser.parse_file(file_path, repo_path, repo_id)
                else:  # doc
                    chunks = await self.doc_parser.parse_file(file_path, repo_path, repo_id)

                if not chunks:
                    return None

                # Step 2: Check if file needs processing (incremental update check)
                if config.enable_incremental_updates and stored_file_commits:
                    # Get current commit for this file
                    if file_type == "code":
                        git_metadata = self.code_parser.get_git_metadata(repo_path, relative_path)
                    else:
                        git_metadata = self.doc_parser.get_git_metadata(repo_path, relative_path)

                    current_commit = git_metadata.get("commit_hash", "")
                    cached_commit = stored_file_commits.get(relative_path, "")

                    if current_commit == cached_commit:
                        # File unchanged - skip
                        logger.debug(f"Skipping unchanged file: {relative_path}")
                        return None

                # Step 3: Delete old chunks if this is an update
                if config.enable_incremental_updates and relative_path in stored_file_commits:
                    deleted = self.db.delete_file_chunks(repo_id, relative_path)
                    if deleted > 0:
                        logger.debug(f"Deleted {deleted} old chunks for {relative_path}")

                # Step 4: Generate embeddings (GPU-bound - run in thread to not block event loop)
                texts = [self.embedding_generator.prepare_text_for_embedding(c) for c in chunks]
                try:
                    embeddings = await asyncio.to_thread(
                        lambda: self.embedding_generator.model.encode(
                            texts,
                            batch_size=config.embedding_batch_size
                        )
                    )

                    # Assign embeddings to chunks
                    for chunk, embedding in zip(chunks, embeddings):
                        chunk.embedding = embedding.tolist()
                except (RuntimeError, ValueError) as emb_err:
                    logger.error(f"Embedding failed for {relative_path}: {emb_err}")
                    return None  # Skip this file if embedding fails

                # Step 5: Upsert chunks (I/O-bound - async)
                result = await self.db.batch_upsert(chunks)

                if result['success'] > 0:
                    logger.info(f"✓ {relative_path}: {result['success']} chunks")

                return chunks  # Return for commit extraction

            except Exception as e:
                logger.error(f"Error processing file {file_path}: {e}")
                return None

    async def process_repository(self, repo_id: str):
        """
        Process a repository using file-atomic async pipeline
        Each file is processed independently: parse → check → embed → upsert

        Args:
            repo_id: Repository in owner/repo format
        """
        try:
            logger.info(f"=== Processing repository: {repo_id} ===")
            start_time = time.time()

            # Step 1: Clone or update repository
            repo_path = await self.clone_or_update_repo(repo_id)

            # Step 2: Get list of all files to process
            logger.info("Collecting files to process...")
            code_files = []
            for ext in config.supported_code_extensions:
                for file_path in repo_path.rglob(f"*{ext}"):
                    # Import here to avoid circular dependency
                    from parsers.code_parser import should_skip_file
                    if not should_skip_file(file_path):
                        code_files.append(file_path)

            doc_files = []
            for ext in config.supported_doc_extensions:
                for file_path in repo_path.rglob(f"*{ext}"):
                    from parsers.code_parser import should_skip_file
                    if not should_skip_file(file_path):
                        doc_files.append(file_path)

            total_files = len(code_files) + len(doc_files)
            logger.info(f"Found {len(code_files)} code files, {len(doc_files)} doc files ({total_files} total)")

            if total_files == 0:
                logger.info("No files to process")
                elapsed = time.time() - start_time
                logger.info(f"✓ Repository {repo_id} processed in {elapsed:.2f}s (empty)")
                return

            # Step 3: Get file→commit cache from DB (single query for incremental updates)
            stored_file_commits = {}
            if config.enable_incremental_updates:
                logger.info("Loading file→commit cache for incremental updates...")
                stored_file_commits = self.db.get_repo_file_commits(repo_id)
                logger.info(f"Loaded {len(stored_file_commits)} cached file commits")

            # Step 4: Process all files concurrently (file-atomic)
            logger.info(f"Processing files with {config.max_concurrent_files} concurrent workers...")

            # Create tasks for all files
            tasks = []
            for file_path in code_files:
                tasks.append(self.process_file_atomic(
                    file_path, repo_path, repo_id, "code", stored_file_commits
                ))
            for file_path in doc_files:
                tasks.append(self.process_file_atomic(
                    file_path, repo_path, repo_id, "doc", stored_file_commits
                ))

            # Run all tasks concurrently (with semaphore limiting concurrency)
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Step 5: Collect chunks for commit extraction (filter out None and exceptions)
            all_chunks = []
            files_processed = 0
            files_skipped = 0
            files_failed = 0

            for result in results:
                if isinstance(result, Exception):
                    logger.error(f"File processing failed: {result}")
                    files_failed += 1
                elif result is None:
                    files_skipped += 1
                else:
                    all_chunks.extend(result)
                    files_processed += 1

            logger.info(f"File processing complete: {files_processed} processed, {files_skipped} skipped, {files_failed} failed")

            if not all_chunks:
                logger.info("No chunks to process (all files skipped or failed)")
                elapsed = time.time() - start_time
                logger.info(f"✓ Repository {repo_id} processed in {elapsed:.2f}s (no changes)")
                return

            # Step 6: Extract and process commit chunks
            logger.info("Extracting commit metadata...")
            commit_chunks = self.commit_parser.extract_commits_from_chunks(all_chunks, repo_id)
            logger.info(f"✓ Extracted {len(commit_chunks)} unique commits")

            if commit_chunks:
                logger.info(f"Processing {len(commit_chunks)} commit messages...")
                # Generate embeddings for commits
                texts = [self.embedding_generator.prepare_text_for_embedding(c) for c in commit_chunks]
                embeddings = await asyncio.to_thread(
                    lambda: self.embedding_generator.model.encode(
                        texts,
                        batch_size=config.embedding_batch_size
                    )
                )

                for chunk, embedding in zip(commit_chunks, embeddings):
                    chunk.embedding = embedding.tolist()

                # Upsert commits
                result = await self.db.batch_upsert(commit_chunks)
                logger.info(f"✓ Stored {result['success']} commit chunks")

            elapsed = time.time() - start_time
            logger.info(f"✓ Repository {repo_id} processed successfully in {elapsed:.2f}s")
            logger.info(f"   Files: {files_processed} processed, {files_skipped} skipped, {files_failed} failed")

        except Exception as e:
            logger.error(f"Error processing repository {repo_id}: {e}")
            raise

    async def process_all_repos(self):
        """Process all configured repositories"""
        if not config.github_repos:
            logger.warning("No repositories configured. Set GITHUB_REPOS environment variable.")
            return

        repos = [r.strip() for r in config.github_repos.split(",")]
        logger.info(f"Processing {len(repos)} repositories: {repos}")

        for repo_id in repos:
            try:
                await self.process_repository(repo_id)
            except Exception as e:
                logger.error(f"Failed to process {repo_id}: {e}")
                # Continue with next repo

        logger.info("✓ All repositories processed")

    async def run_continuous(self, interval_hours: int = 24):
        """
        Run the worker continuously, re-processing repos at intervals

        Args:
            interval_hours: Hours between re-indexing runs
        """
        logger.info(f"Starting continuous mode (interval: {interval_hours}h)")

        while True:
            try:
                await self.process_all_repos()
            except Exception as e:
                logger.error(f"Error in continuous run: {e}")

            # Wait for next run
            logger.info(f"Waiting {interval_hours} hours until next run...")
            await asyncio.sleep(interval_hours * 3600)

    async def run_once(self):
        """Run the worker once and exit"""
        logger.info("Running in single-shot mode")
        await self.process_all_repos()
        logger.info("Worker completed")


async def main():
    """Main entry point"""
    logger.add(
        "logs/ingestion-worker.log",
        rotation="100 MB",
        retention="30 days",
        level=config.log_level
    )

    logger.info("=== CodeSmriti Ingestion Worker ===")
    logger.info(f"Log level: {config.log_level}")
    logger.info(f"Couchbase: {config.couchbase_host}:{config.couchbase_port}")

    worker = IngestionWorker()

    # Check run mode
    run_mode = os.getenv("RUN_MODE", "once")

    if run_mode == "continuous":
        await worker.run_continuous()
    else:
        await worker.run_once()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Worker stopped by user")
    except Exception as e:
        logger.error(f"Worker crashed: {e}")
        raise
