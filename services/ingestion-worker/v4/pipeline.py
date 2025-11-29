"""
V4 Pipeline Orchestrator

Coordinates the full ingestion pipeline:
1. Discover files in repository
2. Process files in parallel (FileProcessor)
3. Generate embeddings
4. Aggregate summaries bottom-up (BottomUpAggregator)
5. Store all documents

Uses existing components:
- CodeParser for tree-sitter parsing
- LLMEnricher for summary generation
- LocalEmbeddingGenerator for embeddings
- CouchbaseClient for storage
"""

import os
import asyncio
import subprocess
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime

from loguru import logger

from .schemas import (
    FileIndex, SymbolIndex, ModuleSummary, RepoSummary,
    make_repo_id, SCHEMA_VERSION,
)
from .quality import QualityTracker
from .file_processor import FileProcessor
from .aggregator import BottomUpAggregator
from .llm_enricher import V4LLMEnricher

# Import existing components
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from parsers.code_parser import CodeParser, should_skip_file
from llm_enricher import LLMConfig, LMSTUDIO_CONFIG
from embeddings.local_generator import LocalEmbeddingGenerator
from storage.couchbase_client import CouchbaseClient
from config import WorkerConfig

config = WorkerConfig()


class V4Pipeline:
    """
    V4 Ingestion Pipeline.

    Processes a repository from files to stored documents with embeddings.
    """

    def __init__(
        self,
        enable_llm: bool = True,
        enable_embeddings: bool = True,
        dry_run: bool = False,
        llm_config: LLMConfig = LMSTUDIO_CONFIG,
    ):
        """
        Initialize the V4 pipeline.

        Args:
            enable_llm: Whether to use LLM for summaries (fallback to basic if False)
            enable_embeddings: Whether to generate embeddings
            dry_run: If True, don't store to database
            llm_config: LLM configuration (Ollama or LM Studio)
        """
        self.enable_llm = enable_llm
        self.enable_embeddings = enable_embeddings
        self.dry_run = dry_run

        # Initialize components
        self.code_parser = CodeParser()
        self.quality_tracker = QualityTracker()

        if enable_llm:
            self.llm_enricher = V4LLMEnricher(llm_config)
        else:
            self.llm_enricher = None

        self.file_processor = FileProcessor(
            code_parser=self.code_parser,
            llm_enricher=self.llm_enricher,
            quality_tracker=self.quality_tracker,
            enable_llm=enable_llm,
        )

        self.aggregator = BottomUpAggregator(
            llm_enricher=self.llm_enricher,
            quality_tracker=self.quality_tracker,
            enable_llm=enable_llm,
        )

        if enable_embeddings:
            self.embedding_generator = LocalEmbeddingGenerator()
        else:
            self.embedding_generator = None

        if not dry_run:
            self.storage = CouchbaseClient()
        else:
            self.storage = None

        logger.info(
            f"V4 Pipeline initialized: "
            f"llm={'enabled' if enable_llm else 'disabled'}, "
            f"embeddings={'enabled' if enable_embeddings else 'disabled'}, "
            f"dry_run={dry_run}"
        )

    def get_current_commit(self, repo_path: Path) -> Optional[str]:
        """Get the current HEAD commit hash."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception as e:
            logger.warning(f"Could not get commit hash: {e}")
        return None

    def discover_files(self, repo_path: Path) -> List[Path]:
        """
        Discover all code files in the repository.

        Uses the same filtering as V3 (should_skip_file).
        """
        files = []

        for ext in config.supported_code_extensions:
            for file_path in repo_path.rglob(f"*{ext}"):
                if should_skip_file(file_path):
                    continue
                files.append(file_path)

        logger.info(f"Discovered {len(files)} code files in {repo_path}")
        return files

    async def process_files(
        self,
        files: List[Path],
        repo_path: Path,
        repo_id: str,
        commit_hash: str,
        concurrency: int = 4,
    ) -> Tuple[List[FileIndex], List[SymbolIndex]]:
        """
        Process all files in parallel.

        Args:
            files: List of file paths to process
            repo_path: Repository root path
            repo_id: Repository identifier
            commit_hash: Git commit hash
            concurrency: Number of concurrent file processors

        Returns:
            (file_indices, symbol_indices)
        """
        all_file_indices = []
        all_symbol_indices = []
        total_files = len(files)

        # Use semaphore to limit concurrency
        semaphore = asyncio.Semaphore(concurrency)

        # Thread-safe counter for progress
        import threading
        progress_lock = threading.Lock()
        progress = {"completed": 0}

        async def process_one(file_path: Path) -> Tuple[Optional[FileIndex], List[SymbolIndex]]:
            async with semaphore:
                try:
                    # Parent module ID will be set during aggregation
                    file_doc, symbol_docs = await self.file_processor.process(
                        file_path=file_path,
                        repo_path=repo_path,
                        repo_id=repo_id,
                        commit_hash=commit_hash,
                        parent_module_id=""  # Set during aggregation
                    )

                    # Update and log progress
                    with progress_lock:
                        progress["completed"] += 1
                        current = progress["completed"]
                    relative_path = str(file_path.relative_to(repo_path))
                    symbols_count = len(symbol_docs) if symbol_docs else 0
                    status = "ok" if file_doc else "skip"
                    logger.info(f"[{current}/{total_files}] {relative_path} ({status}, {symbols_count} symbols)")

                    return file_doc, symbol_docs
                except Exception as e:
                    with progress_lock:
                        progress["completed"] += 1
                        current = progress["completed"]
                    relative_path = str(file_path.relative_to(repo_path))
                    logger.error(f"[{current}/{total_files}] {relative_path} - ERROR: {e}")
                    self.quality_tracker.record_file_failed(relative_path, str(e))
                    return None, []

        # Process all files concurrently
        tasks = [process_one(f) for f in files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Task exception: {result}")
                continue
            file_doc, symbol_docs = result
            if file_doc:
                all_file_indices.append(file_doc)
                all_symbol_indices.extend(symbol_docs)

        logger.info(
            f"Processed {len(all_file_indices)} files, "
            f"{len(all_symbol_indices)} symbols"
        )

        return all_file_indices, all_symbol_indices

    async def generate_embeddings(
        self,
        file_indices: List[FileIndex],
        symbol_indices: List[SymbolIndex],
        module_summaries: List[ModuleSummary],
        repo_summary: RepoSummary,
    ) -> None:
        """
        Generate embeddings for all documents.

        Uses the _embedding_text or content field for embedding generation.
        """
        if not self.embedding_generator:
            logger.info("Embeddings disabled, skipping")
            return

        # Prepare texts for embedding
        texts = []
        docs = []

        # File indices
        for f in file_indices:
            text = getattr(f, '_embedding_text', None) or f.content
            if text:
                texts.append(text)
                docs.append(f)

        # Symbol indices
        for s in symbol_indices:
            # Use code + summary for symbol embedding
            code = getattr(s, '_code_for_embedding', '')
            text = f"{s.content}\n\nCode:\n{code}" if code else s.content
            if text:
                texts.append(text)
                docs.append(s)

        # Module summaries
        for m in module_summaries:
            if m.content:
                texts.append(m.content)
                docs.append(m)

        # Repo summary
        if repo_summary and repo_summary.content:
            texts.append(repo_summary.content)
            docs.append(repo_summary)

        logger.info(f"Generating embeddings for {len(texts)} documents")

        # Generate embeddings in batches
        batch_size = 64
        for i in range(0, len(texts), batch_size):
            batch_texts = texts[i:i + batch_size]
            batch_docs = docs[i:i + batch_size]

            # Generate embeddings
            for j, text in enumerate(batch_texts):
                embedding = self.embedding_generator.generate_embedding(text)
                batch_docs[j].embedding = embedding
                self.quality_tracker.record_embedding()

            # Log progress
            progress = min(i + batch_size, len(texts))
            if progress % 100 == 0 or progress == len(texts):
                logger.info(f"Generated {progress}/{len(texts)} embeddings")

    async def store_documents(
        self,
        file_indices: List[FileIndex],
        symbol_indices: List[SymbolIndex],
        module_summaries: List[ModuleSummary],
        repo_summary: RepoSummary,
    ) -> Dict[str, int]:
        """
        Store all documents to Couchbase.

        Returns:
            Dictionary with counts by document type
        """
        if self.dry_run or not self.storage:
            logger.info("Dry run mode, skipping storage")
            return {
                "file_index": len(file_indices),
                "symbol_index": len(symbol_indices),
                "module_summary": len(module_summaries),
                "repo_summary": 1 if repo_summary else 0,
            }

        counts = {
            "file_index": 0,
            "symbol_index": 0,
            "module_summary": 0,
            "repo_summary": 0,
        }

        # Store file indices
        for f in file_indices:
            try:
                doc = f.to_dict()
                self.storage.collection.upsert(f.document_id, doc)
                counts["file_index"] += 1
            except Exception as e:
                logger.error(f"Error storing file_index {f.file_path}: {e}")

        # Store symbol indices
        for s in symbol_indices:
            try:
                doc = s.to_dict()
                self.storage.collection.upsert(s.document_id, doc)
                counts["symbol_index"] += 1
            except Exception as e:
                logger.error(f"Error storing symbol_index {s.symbol_name}: {e}")

        # Store module summaries
        for m in module_summaries:
            try:
                doc = m.to_dict()
                self.storage.collection.upsert(m.document_id, doc)
                counts["module_summary"] += 1
            except Exception as e:
                logger.error(f"Error storing module_summary {m.module_path}: {e}")

        # Store repo summary
        if repo_summary:
            try:
                doc = repo_summary.to_dict()
                self.storage.collection.upsert(repo_summary.document_id, doc)
                counts["repo_summary"] = 1
            except Exception as e:
                logger.error(f"Error storing repo_summary: {e}")

        logger.info(
            f"Stored documents: "
            f"{counts['file_index']} files, "
            f"{counts['symbol_index']} symbols, "
            f"{counts['module_summary']} modules, "
            f"{counts['repo_summary']} repo"
        )

        return counts

    def delete_v3_documents(self, repo_id: str) -> int:
        """
        Delete all V3 documents for a repository.

        V3 documents have type in ['code_chunk', 'document', 'commit_chunk',
        'repo_summary', 'module_summary', 'file_index'].
        """
        if self.dry_run or not self.storage:
            logger.info("Dry run mode, skipping V3 deletion")
            return 0

        try:
            # Count existing documents
            count_query = f"""
                SELECT COUNT(*) as count
                FROM `{config.couchbase_bucket}`
                WHERE repo_id = $repo_id
            """
            result = self.storage.cluster.query(count_query, repo_id=repo_id)
            rows = list(result)
            count = rows[0]['count'] if rows else 0

            if count == 0:
                return 0

            # Delete all documents for this repo
            delete_query = f"""
                DELETE FROM `{config.couchbase_bucket}`
                WHERE repo_id = $repo_id
            """
            self.storage.cluster.query(delete_query, repo_id=repo_id)

            logger.info(f"Deleted {count} V3 documents for {repo_id}")
            return count

        except Exception as e:
            logger.error(f"Error deleting V3 documents: {e}")
            return 0

    async def ingest_repository(
        self,
        repo_path: Path,
        repo_id: str,
        delete_existing: bool = True,
        file_concurrency: int = 4,
    ) -> Dict:
        """
        Ingest a complete repository.

        Args:
            repo_path: Path to the repository
            repo_id: Repository identifier (owner/name)
            delete_existing: Whether to delete existing documents first
            file_concurrency: Number of concurrent file processors

        Returns:
            Dictionary with ingestion results
        """
        logger.info(f"Starting V4 ingestion for {repo_id}")
        self.quality_tracker.start_run(repo_id)

        # Get commit hash
        commit_hash = self.get_current_commit(repo_path)
        if not commit_hash:
            commit_hash = "unknown"
            logger.warning("Could not determine commit hash, using 'unknown'")

        # Delete existing documents if requested
        if delete_existing:
            deleted = self.delete_v3_documents(repo_id)
            logger.info(f"Cleaned up {deleted} existing documents")

        # Phase 1: Discover files
        files = self.discover_files(repo_path)
        if not files:
            logger.warning(f"No code files found in {repo_path}")
            return {"error": "No code files found"}

        # Phase 2: Process files
        file_indices, symbol_indices = await self.process_files(
            files=files,
            repo_path=repo_path,
            repo_id=repo_id,
            commit_hash=commit_hash,
            concurrency=file_concurrency,
        )

        if not file_indices:
            logger.warning("No files were successfully processed")
            return {"error": "No files processed"}

        # Phase 3: Bottom-up aggregation
        module_summaries, repo_summary = await self.aggregator.aggregate_all(
            file_indices=file_indices,
            repo_id=repo_id,
            commit_hash=commit_hash,
        )

        # Phase 4: Generate embeddings
        await self.generate_embeddings(
            file_indices=file_indices,
            symbol_indices=symbol_indices,
            module_summaries=module_summaries,
            repo_summary=repo_summary,
        )

        # Phase 5: Store documents
        store_counts = await self.store_documents(
            file_indices=file_indices,
            symbol_indices=symbol_indices,
            module_summaries=module_summaries,
            repo_summary=repo_summary,
        )

        # End tracking
        self.quality_tracker.end_run()

        # Print summary
        self.quality_tracker.print_summary()

        return {
            "repo_id": repo_id,
            "commit_hash": commit_hash,
            "files_discovered": len(files),
            "documents_stored": store_counts,
            "quality": self.quality_tracker.get_summary(),
        }

    async def close(self):
        """Clean up resources."""
        if self.llm_enricher:
            await self.llm_enricher.close()
        if self.storage:
            self.storage.close()


async def ingest_single_repo(
    repo_path: str,
    repo_id: str,
    enable_llm: bool = True,
    dry_run: bool = False,
) -> Dict:
    """
    Convenience function to ingest a single repository.

    Args:
        repo_path: Path to the repository
        repo_id: Repository identifier (owner/name)
        enable_llm: Whether to use LLM for summaries
        dry_run: If True, don't store to database

    Returns:
        Ingestion results
    """
    pipeline = V4Pipeline(
        enable_llm=enable_llm,
        enable_embeddings=True,
        dry_run=dry_run,
    )

    try:
        result = await pipeline.ingest_repository(
            repo_path=Path(repo_path),
            repo_id=repo_id,
        )
        return result
    finally:
        await pipeline.close()


# For testing
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="V4 Pipeline Test")
    parser.add_argument("--repo-path", required=True, help="Path to repository")
    parser.add_argument("--repo-id", required=True, help="Repository ID (owner/name)")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM")
    parser.add_argument("--dry-run", action="store_true", help="Don't store to database")

    args = parser.parse_args()

    result = asyncio.run(ingest_single_repo(
        repo_path=args.repo_path,
        repo_id=args.repo_id,
        enable_llm=not args.no_llm,
        dry_run=args.dry_run,
    ))

    print("\n=== INGESTION RESULT ===")
    import json
    print(json.dumps(result, indent=2, default=str))
