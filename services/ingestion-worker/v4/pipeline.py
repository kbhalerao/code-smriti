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
from typing import Callable, List, Dict, Optional, Tuple
from datetime import datetime

from loguru import logger

from .schemas import (
    FileIndex, SymbolIndex, SemanticUnit, ModuleSummary, RepoSummary,
    FailureKind, FileFailure, ProcessedFile,
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
from llm_enricher import LLMConfig, LLM_CONFIG
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
        llm_config: LLMConfig = LLM_CONFIG,
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

        # Populated by process_files: every file-level failure from the last
        # pass, after its end-of-pass retry. This is what the DLQ is built from,
        # and it is deliberately not derivable from the documents — a degraded
        # document records what it IS, not what went wrong producing it.
        self.file_failures: List[FileFailure] = []

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
        on_progress: Optional[Callable[[int, int, List[str]], None]] = None,
    ) -> Tuple[List[FileIndex], List[SymbolIndex], List[SemanticUnit]]:
        """
        Process all files in parallel.

        Args:
            files: List of file paths to process
            repo_path: Repository root path
            repo_id: Repository identifier
            commit_hash: Git commit hash
            concurrency: Number of concurrent file processors

        Returns:
            (file_indices, symbol_indices, semantic_units)
        """
        all_file_indices = []
        all_symbol_indices = []
        all_semantic_units = []
        total_files = len(files)

        # Use semaphore to limit concurrency
        semaphore = asyncio.Semaphore(concurrency)

        # Thread-safe counter for progress
        import threading
        progress_lock = threading.Lock()
        progress = {"completed": 0}

        async def process_one(file_path: Path) -> ProcessedFile:
            async with semaphore:
                result = await self.process_file_bounded(
                    file_path, repo_path, repo_id, commit_hash
                )

            with progress_lock:
                progress["completed"] += 1
                current = progress["completed"]
            relative_path = str(file_path.relative_to(repo_path))
            if on_progress:
                # Advisory only. A reporting hook must never be able to fail the
                # file it is reporting on — a rebuild is hours of work and a
                # dashboard is not worth losing any of it.
                try:
                    on_progress(current, total_files, [relative_path])
                except Exception as e:
                    logger.debug(f"Progress callback failed: {e}")
            status = "ok" if result.file_index else "skip"
            if result.failed:
                status = "degraded"
            logger.info(
                f"[{current}/{total_files}] {relative_path} "
                f"({status}, {len(result.symbols)} symbols, {len(result.semantic_units)} semantic units)"
            )
            return result

        # Process all files concurrently
        tasks = [process_one(f) for f in files]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        processed: List[ProcessedFile] = []
        for file_path, result in zip(files, results):
            if isinstance(result, BaseException):
                # process_one is written not to raise, so this is a bug rather
                # than an LLM problem — but it still must not silently drop a file.
                relative_path = str(file_path.relative_to(repo_path))
                logger.exception(f"Task exception on {relative_path}: {result}")
                processed.append(ProcessedFile(failures=[FileFailure(
                    file_path=relative_path,
                    kind=FailureKind.EXCEPTION,
                    detail=f"task raised {type(result).__name__}: {result}",
                )]))
                continue
            processed.append(result)

        # --- Second chance, once the fan-out has drained -----------------------
        # Deliberately here and not inside process_one. A file usually fails
        # because its own batch saturated the slots, so retrying in place
        # recreates the condition that caused it; by now the slots are free.
        if config.retry_failed_files:
            retry_indices = [i for i, r in enumerate(processed) if r.failed]
            if retry_indices:
                logger.info(
                    f"Retrying {len(retry_indices)} file(s) that failed, now that "
                    f"the file pass has drained"
                )
                for i in retry_indices:
                    file_path = files[i]
                    relative_path = str(file_path.relative_to(repo_path))
                    retried = await self.process_file_bounded(
                        file_path, repo_path, repo_id, commit_hash
                    )
                    if retried.failed:
                        # Still failing with the slots free. This is a bug in the
                        # pipeline, not contention — it goes to the DLQ and a
                        # human looks at it. The repo still completes and still
                        # checkpoints its commit: aborting here would leave the
                        # commit unadvanced and every later tick would reprocess
                        # the whole repo forever over one poison file.
                        for f in retried.failures:
                            f.retried = True
                        logger.error(
                            f"{relative_path}: still failing after retry — "
                            + "; ".join(f"{f.kind.value}: {f.detail}" for f in retried.failures)
                        )
                        processed[i] = retried
                    else:
                        logger.info(f"{relative_path}: recovered on retry")
                        processed[i] = retried

        for result in processed:
            if result.file_index:
                all_file_indices.append(result.file_index)
                all_symbol_indices.extend(result.symbols)
                all_semantic_units.extend(result.semantic_units)

        self.file_failures = [f for r in processed for f in r.failures]

        logger.info(
            f"Processed {len(all_file_indices)} files, "
            f"{len(all_symbol_indices)} symbols, "
            f"{len(all_semantic_units)} semantic units"
        )
        if self.file_failures:
            logger.error(
                f"{len(self.file_failures)} file-level failure(s) across "
                f"{len({f.file_path for f in self.file_failures})} file(s)"
            )

        return all_file_indices, all_symbol_indices, all_semantic_units

    async def process_file_bounded(
        self,
        file_path: Path,
        repo_path: Path,
        repo_id: str,
        commit_hash: str,
        use_llm: Optional[bool] = None,
    ) -> ProcessedFile:
        """
        One wall-clock-bounded attempt at a file. Never raises, never returns nothing.

        Shared by the full-ingest path (process_files) and the incremental path
        (IncrementalUpdater), which processes files directly and would otherwise
        have no bound, no degradation and no failure reporting at all — and
        incremental is the path almost every run takes.
        """
        relative_path = str(file_path.relative_to(repo_path))
        try:
            return await asyncio.wait_for(
                self.file_processor.process(
                    file_path=file_path,
                    repo_path=repo_path,
                    repo_id=repo_id,
                    commit_hash=commit_hash,
                    parent_module_id="",  # Set during aggregation
                    use_llm=use_llm,
                ),
                timeout=config.file_timeout_seconds,
            )
        except asyncio.TimeoutError:
            # wait_for cancels the coroutine, so there are no partial documents
            # to salvage — reprocess without the LLM instead. That is fast and
            # network-free, and produces a COMPLETE basic document rather than
            # half an enriched one. What must not happen is the file disappearing
            # while the commits index advances and declares the repo current.
            logger.error(
                f"{relative_path}: exceeded the {config.file_timeout_seconds}s file budget; "
                f"reprocessing without the LLM"
            )
            degraded = await self._process_without_llm(file_path, repo_path, repo_id, commit_hash)
            degraded.failures.append(FileFailure(
                file_path=relative_path,
                kind=FailureKind.TIMEOUT,
                detail=f"exceeded the {config.file_timeout_seconds}s file budget",
            ))
            return degraded
        except Exception as e:
            logger.exception(f"{relative_path}: unhandled error")
            self.quality_tracker.record_file_failed(relative_path, str(e))
            return ProcessedFile(failures=[FileFailure(
                file_path=relative_path,
                kind=FailureKind.EXCEPTION,
                detail=f"{type(e).__name__}: {e}",
            )])

    async def _process_without_llm(
        self,
        file_path: Path,
        repo_path: Path,
        repo_id: str,
        commit_hash: str,
    ) -> ProcessedFile:
        """
        Reprocess one file with the LLM off, for a file that blew its wall clock.

        Parser symbols keep their real spans and every summary falls back to
        docstring + structure, so the result is a complete document rather than a
        partial one. Marked `timeout_fallback` — distinct from an ordinary
        `fallback` — so these stay findable afterwards without a DLQ lookup.
        """
        try:
            result = await asyncio.wait_for(
                self.file_processor.process(
                    file_path=file_path,
                    repo_path=repo_path,
                    repo_id=repo_id,
                    commit_hash=commit_hash,
                    parent_module_id="",
                    use_llm=False,
                ),
                timeout=config.file_timeout_seconds,
            )
        except Exception as e:
            # No LLM was involved, so this is parsing or IO. Nothing to salvage.
            relative_path = str(file_path.relative_to(repo_path))
            logger.exception(f"{relative_path}: LLM-free reprocessing also failed")
            return ProcessedFile(failures=[FileFailure(
                file_path=relative_path,
                kind=FailureKind.EXCEPTION,
                detail=f"llm-free reprocessing failed: {type(e).__name__}: {e}",
            )])

        for doc in result.documents:
            if getattr(doc, "quality", None) is not None:
                doc.quality.summary_source = "timeout_fallback"
        return result

    async def generate_embeddings(
        self,
        file_indices: List[FileIndex],
        symbol_indices: List[SymbolIndex],
        module_summaries: List[ModuleSummary],
        repo_summary: RepoSummary,
        semantic_units: Optional[List[SemanticUnit]] = None,
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

        # Symbol indices and semantic units both embed summary + code snippet
        for s in [*symbol_indices, *(semantic_units or [])]:
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
        semantic_units: Optional[List[SemanticUnit]] = None,
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
                "semantic_unit": len(semantic_units or []),
                "module_summary": len(module_summaries),
                "repo_summary": 1 if repo_summary else 0,
            }

        counts = {
            "file_index": 0,
            "symbol_index": 0,
            "semantic_unit": 0,
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

        # Store semantic units
        for u in (semantic_units or []):
            try:
                self.storage.collection.upsert(u.document_id, u.to_dict())
                counts["semantic_unit"] += 1
            except Exception as e:
                logger.error(f"Error storing semantic_unit {u.file_path}:{u.start_line}: {e}")

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
            # N1QL executes lazily — the result MUST be consumed or the DELETE
            # never reaches the server (same pitfall fixed in the incremental
            # summary path). Otherwise delete_existing=True silently no-ops.
            _ = list(self.storage.cluster.query(delete_query, repo_id=repo_id))

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
        on_progress: Optional[Callable[[int, int, List[str]], None]] = None,
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
        file_indices, symbol_indices, semantic_units = await self.process_files(
            files=files,
            repo_path=repo_path,
            repo_id=repo_id,
            commit_hash=commit_hash,
            concurrency=file_concurrency,
            on_progress=on_progress,
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
            semantic_units=semantic_units,
        )

        # Phase 5: Store documents
        store_counts = await self.store_documents(
            file_indices=file_indices,
            symbol_indices=symbol_indices,
            module_summaries=module_summaries,
            repo_summary=repo_summary,
            semantic_units=semantic_units,
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
