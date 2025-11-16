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

from loguru import logger
import git
from github import Github

# Import custom modules (to be implemented)
from parsers.code_parser import CodeParser
from parsers.document_parser import DocumentParser
from embeddings.generator import EmbeddingGenerator
# from storage.couchbase_client import CouchbaseClient

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

        # Initialize embedding generator
        self.embedding_generator = EmbeddingGenerator()

        # TODO: Initialize Couchbase client
        # self.db = CouchbaseClient()

        # Repository storage path
        self.repos_path = Path("/repos")
        self.repos_path.mkdir(exist_ok=True)

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
                logger.info(f"Updating existing repo: {repo_id}")
                repo = git.Repo(repo_path)
                repo.remotes.origin.pull()
            else:
                logger.info(f"Cloning new repo: {repo_id}")
                clone_url = f"https://github.com/{repo_id}.git"

                # Use token for authentication if available
                if config.github_token:
                    clone_url = f"https://{config.github_token}@github.com/{repo_id}.git"

                git.Repo.clone_from(clone_url, repo_path)

            logger.info(f"✓ Repository ready: {repo_path}")
            return repo_path

        except Exception as e:
            logger.error(f"Error cloning/updating {repo_id}: {e}")
            raise

    async def process_repository(self, repo_id: str):
        """
        Process a repository: clone, parse, embed, and store

        Args:
            repo_id: Repository in owner/repo format
        """
        try:
            logger.info(f"=== Processing repository: {repo_id} ===")
            start_time = time.time()

            # Step 1: Clone or update repository
            repo_path = await self.clone_or_update_repo(repo_id)

            # Step 2: Parse code files
            logger.info("Parsing code files...")
            code_chunks = await self.code_parser.parse_repository(repo_path, repo_id)
            logger.info(f"✓ Parsed {len(code_chunks)} code chunks")

            # Step 3: Parse documentation files
            logger.info("Parsing documentation files...")
            doc_chunks = await self.doc_parser.parse_repository(repo_path, repo_id)
            logger.info(f"✓ Parsed {len(doc_chunks)} document chunks")

            # Step 4: Generate embeddings
            logger.info("Generating embeddings...")
            all_chunks = code_chunks + doc_chunks
            await self.embedding_generator.generate_embeddings(all_chunks)
            logger.info(f"✓ Generated embeddings for {len(all_chunks)} chunks")

            # Step 5: Store in Couchbase
            logger.info("Storing in database...")
            # TODO: Batch upsert to Couchbase
            # await self.db.batch_upsert(all_chunks)
            logger.info(f"✓ Stored {len(all_chunks)} chunks")

            elapsed = time.time() - start_time
            logger.info(f"✓ Repository {repo_id} processed successfully in {elapsed:.2f}s")

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
