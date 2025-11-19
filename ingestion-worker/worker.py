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

        # Repository storage path
        # Support both Docker (/repos) and native execution (configurable via REPOS_PATH)
        repos_base = os.getenv("REPOS_PATH", "/repos")
        self.repos_path = Path(repos_base).resolve()
        self.repos_path.mkdir(parents=True, exist_ok=True)

        logger.info(f"Worker initialized. Repos path: {self.repos_path}")

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

        files_to_process = set()
        files_unchanged = set()
        files_updated = set()
        files_new = set()
        deleted_chunks = 0

        # Check each unique file in the chunks
        file_paths = set(chunk.file_path for chunk in chunks)

        for file_path in file_paths:
            # Get current commit hash for this file
            git_metadata = self.code_parser.get_git_metadata(repo_path, file_path)
            current_commit = git_metadata.get("commit_hash", "")

            # Check stored commit hash
            stored_commit = self.db.check_file_commit(repo_id, file_path)

            if not stored_commit:
                # File is new
                files_new.add(file_path)
                files_to_process.add(file_path)
            elif stored_commit != current_commit:
                # File has changed - delete old chunks
                deleted = self.db.delete_file_chunks(repo_id, file_path)
                deleted_chunks += deleted
                files_updated.add(file_path)
                files_to_process.add(file_path)
            else:
                # File unchanged - skip
                files_unchanged.add(file_path)

        # Filter chunks to only include files that need processing
        filtered_chunks = [c for c in chunks if c.file_path in files_to_process]

        stats = {
            "skipped": len(files_unchanged),
            "new": len(files_new),
            "updated": len(files_updated),
            "deleted": deleted_chunks
        }

        return filtered_chunks, stats

    async def process_repository(self, repo_id: str):
        """
        Process a repository: clone, parse, embed, and store
        Uses incremental updates to skip unchanged files

        Args:
            repo_id: Repository in owner/repo format
        """
        try:
            logger.info(f"=== Processing repository: {repo_id} ===")
            start_time = time.time()

            # Step 1: Clone or update repository
            repo_path = await self.clone_or_update_repo(repo_id)

            # Step 2: Parse all code files
            logger.info("Parsing code files...")
            all_code_chunks = await self.code_parser.parse_repository(repo_path, repo_id)
            logger.info(f"Parsed {len(all_code_chunks)} total code chunks")

            # Step 3: Parse all documentation files
            logger.info("Parsing documentation files...")
            all_doc_chunks = await self.doc_parser.parse_repository(repo_path, repo_id)
            logger.info(f"Parsed {len(all_doc_chunks)} total document chunks")

            # Step 4: Filter chunks - only keep new or changed files (if enabled)
            if config.enable_incremental_updates:
                logger.info("Checking for file changes...")
                code_chunks, code_stats = self.filter_chunks_by_file_changes(
                    all_code_chunks, repo_id, repo_path
                )
                doc_chunks, doc_stats = self.filter_chunks_by_file_changes(
                    all_doc_chunks, repo_id, repo_path
                )

                # Combine stats
                total_skipped = code_stats["skipped"] + doc_stats["skipped"]
                total_new = code_stats["new"] + doc_stats["new"]
                total_updated = code_stats["updated"] + doc_stats["updated"]
                total_deleted = code_stats["deleted"] + doc_stats["deleted"]

                logger.info(
                    f"Incremental update: {total_skipped} files skipped (unchanged), "
                    f"{total_new} new, {total_updated} updated, {total_deleted} old chunks deleted"
                )

                # Step 5: Prepare chunks for processing
                all_chunks = code_chunks + doc_chunks
            else:
                logger.info("Incremental updates disabled - processing all files")
                all_chunks = all_code_chunks + all_doc_chunks

            if not all_chunks:
                logger.info("No new or updated files to process")
                elapsed = time.time() - start_time
                logger.info(f"✓ Repository {repo_id} processed in {elapsed:.2f}s (no changes)")
                return

            # Step 6: Extract unique commits for separate storage
            logger.info("Extracting commit metadata...")
            commit_chunks = self.commit_parser.extract_commits_from_chunks(all_chunks, repo_id)
            logger.info(f"✓ Extracted {len(commit_chunks)} unique commits")

            # Step 7: Generate embeddings (for code/doc chunks only, not commits)
            logger.info(f"Generating embeddings for {len(all_chunks)} code/doc chunks...")
            await self.embedding_generator.generate_embeddings(all_chunks)
            logger.info(f"✓ Generated embeddings for {len(all_chunks)} chunks")

            # Step 8: Store in Couchbase (code/doc chunks + commit chunks)
            logger.info("Storing in database...")

            # Store code and doc chunks with embeddings
            code_doc_result = await self.db.batch_upsert(all_chunks)
            logger.info(f"✓ Stored {code_doc_result['success']} code/doc chunks ({code_doc_result['failed']} failed)")

            # Store commit chunks (no embeddings needed)
            commit_result = {"success": 0, "failed": 0}
            if commit_chunks:
                commit_result = await self.db.batch_upsert(commit_chunks)
                logger.info(f"✓ Stored {commit_result['success']} commit chunks ({commit_result['failed']} failed)")

            total_stored = code_doc_result['success'] + commit_result['success']
            total_failed = code_doc_result['failed'] + commit_result['failed']

            elapsed = time.time() - start_time
            logger.info(f"✓ Repository {repo_id} processed successfully in {elapsed:.2f}s")
            logger.info(f"   Total: {total_stored} chunks stored, {total_failed} failed")

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
