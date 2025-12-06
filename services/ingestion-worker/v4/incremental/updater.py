"""
Incremental Updater - Main orchestrator for git-based incremental updates.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set

from loguru import logger

from .models import (
    ChangeSet, UpdateResult,
    STATUS_SKIPPED, STATUS_EXCLUDED, STATUS_UPDATED,
    STATUS_FULL_REINGEST, STATUS_EMPTY, STATUS_ERROR, STATUS_DELETED
)
from .git_utils import GitOperations
from .repo_lifecycle import RepoLifecycle
from .significance import SignificanceChecker


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
        llm_config=None
    ):
        """
        Args:
            threshold: Percentage of files changed to trigger full re-ingest (default 5%)
            dry_run: If True, show changes but don't write to DB
            enable_llm: If True, use LLM for summaries
            llm_config: LLM configuration
        """
        # Import here to avoid circular imports
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))

        from config import WorkerConfig
        from v4.pipeline import V4Pipeline
        from storage.couchbase_client import CouchbaseClient
        from llm_enricher import LMSTUDIO_CONFIG

        config = WorkerConfig()
        self.threshold = threshold
        self.dry_run = dry_run
        self.enable_llm = enable_llm
        self.llm_config = llm_config or LMSTUDIO_CONFIG

        # Initialize storage
        self.cb_client = CouchbaseClient()

        # Initialize pipeline
        self.pipeline = V4Pipeline(
            enable_llm=enable_llm,
            enable_embeddings=True,
            dry_run=dry_run,
            llm_config=self.llm_config
        )

        # Initialize helpers
        self.git = GitOperations()
        self.repo_lifecycle = RepoLifecycle(
            repos_path=Path(config.repos_path),
            cb_client=self.cb_client,
            github_token=config.github_token
        )
        self.significance = SignificanceChecker(
            embedding_generator=self.pipeline.embedding_generator,
            enabled=enable_llm
        )

        # Store config reference
        self.config = config

        # Load exclusions
        self.exclusions = self.repo_lifecycle.load_exclusions()

    def filter_supported_files(
        self,
        files: List[str],
        repo_path: Path
    ) -> tuple[List[str], List[str]]:
        """Filter files into code and doc files."""
        import sys
        sys.path.insert(0, str(Path(__file__).parent.parent.parent))
        from parsers.code_parser import should_skip_file

        code_extensions = set(self.config.supported_code_extensions)
        doc_extensions = {'.md', '.rst', '.txt'}

        code_files = []
        doc_files = []

        for f in files:
            ext = Path(f).suffix.lower()
            full_path = repo_path / f

            if should_skip_file(full_path):
                continue

            if ext in code_extensions:
                code_files.append(f)
            elif ext in doc_extensions:
                doc_files.append(f)

        return code_files, doc_files

    def get_affected_modules(self, file_paths: List[str]) -> Set[str]:
        """Get all module paths affected by file changes."""
        modules = set()
        for fp in file_paths:
            parts = Path(fp).parts[:-1]
            for i in range(len(parts) + 1):
                module_path = '/'.join(parts[:i]) if i > 0 else ''
                modules.add(module_path)
        return modules

    def _delete_old_file_docs(self, repo_id: str, file_path: str, new_commit: str):
        """Delete old file/symbol docs, excluding the newly inserted commit."""
        try:
            from couchbase.options import QueryOptions
            query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND file_path = $file_path
                  AND type IN ['file_index', 'symbol_index']
                  AND commit_hash != $new_commit
            """
            result = self.cb_client.cluster.query(
                query,
                QueryOptions(named_parameters={
                    "repo_id": repo_id,
                    "file_path": file_path,
                    "new_commit": new_commit
                })
            )
            # Consume to ensure execution
            _ = list(result)
        except Exception as e:
            logger.warning(f"Could not delete old docs for {file_path}: {e}")

    def process_repo(self, repo_id: str, repo_path: Path, loop=None) -> UpdateResult:
        """Process a single repository with incremental update logic."""
        start_time = datetime.now()
        logger.info(f"\nProcessing {repo_id}")

        # 0. Check exclusion list
        if repo_id in self.exclusions:
            logger.info(f"  Skipping - excluded in config")
            return UpdateResult(
                repo_id=repo_id,
                status=STATUS_EXCLUDED,
                reason='in_exclusion_list',
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 1. Fetch latest from origin
        if not self.git.fetch(repo_path):
            return UpdateResult(repo_id=repo_id, status=STATUS_ERROR, error='Git fetch failed')

        # 2. Get commits
        local_head = self.git.get_head_commit(repo_path)
        origin_head = self.git.get_origin_head(repo_path)
        stored_commit = self.repo_lifecycle.get_stored_commit(repo_id)

        if not origin_head:
            return UpdateResult(repo_id=repo_id, status=STATUS_ERROR, error='Could not determine origin HEAD')

        # 3. Check if update needed
        if stored_commit and local_head == origin_head == stored_commit:
            logger.info(f"  Skipping - no changes (commit: {stored_commit[:8]})")
            return UpdateResult(
                repo_id=repo_id,
                status=STATUS_SKIPPED,
                reason='no_changes',
                commit=stored_commit,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 4. New repo - full ingestion
        if not stored_commit:
            logger.info(f"  New repo - full ingestion")
            if not self.dry_run:
                self.git.pull(repo_path)
                owns_loop = loop is None
                if owns_loop:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self.pipeline.ingest_repository(repo_path, repo_id))
                finally:
                    if owns_loop:
                        loop.close()
            return UpdateResult(
                repo_id=repo_id,
                status=STATUS_FULL_REINGEST,
                reason='new_repo',
                commit=origin_head,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 5. Get changed files
        base_commit = stored_commit
        changes = self.git.get_changed_files(repo_path, base_commit, origin_head)

        if not changes:
            logger.info(f"  Skipping - no file changes")
            return UpdateResult(
                repo_id=repo_id,
                status=STATUS_SKIPPED,
                reason='no_file_changes',
                commit=origin_head,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 6. Check threshold
        total_files = self.repo_lifecycle.get_repo_file_count(repo_id) or 1
        change_ratio = changes.total_changed / total_files

        if change_ratio > self.threshold:
            logger.info(f"  {changes.total_changed} files changed ({change_ratio:.1%}) > {self.threshold:.0%} threshold - full re-ingestion")
            if not self.dry_run:
                self.git.pull(repo_path)
                owns_loop = loop is None
                if owns_loop:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self.pipeline.ingest_repository(repo_path, repo_id))
                finally:
                    if owns_loop:
                        loop.close()
            return UpdateResult(
                repo_id=repo_id,
                status=STATUS_FULL_REINGEST,
                reason=f'threshold_exceeded ({change_ratio:.1%})',
                commit=origin_head,
                files_processed=changes.total_changed,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 7. Surgical incremental update
        logger.info(f"  Incremental: +{len(changes.added)} ~{len(changes.modified)} -{len(changes.deleted)}")

        if not self.dry_run:
            self.git.pull(repo_path)

        # Filter to supported files
        code_to_process, docs_to_process = self.filter_supported_files(changes.files_to_process, repo_path)
        code_deleted, docs_deleted = self.filter_supported_files(changes.deleted, repo_path)

        files_deleted = 0
        files_processed = 0
        any_significant_change = False

        # 7a. Delete docs for deleted files
        for file_path in code_deleted:
            self.repo_lifecycle.delete_file_docs(repo_id, file_path, self.dry_run)
            files_deleted += 1

        for file_path in docs_deleted:
            self.repo_lifecycle.delete_doc_chunks(repo_id, file_path, self.dry_run)
            files_deleted += 1

        if self.dry_run:
            logger.info(f"  [DRY RUN] Would process {len(code_to_process)} code files, {len(docs_to_process)} doc files")
            return UpdateResult(
                repo_id=repo_id,
                status=STATUS_UPDATED,
                reason='dry_run',
                commit=origin_head,
                files_processed=len(code_to_process) + len(docs_to_process),
                files_deleted=files_deleted,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 7b. Process changed code files
        file_indices = []
        all_symbol_indices = []

        # Use a SINGLE event loop for ALL async operations in this surgical update
        # This is critical because httpx.AsyncClient binds to the loop it's used with
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Track files we successfully process (for deferred delete)
        processed_files = []

        try:
            for file_path in code_to_process:
                full_path = repo_path / file_path
                if not full_path.exists():
                    continue

                # Get old summary and embedding for significance check
                old_file_summary = self.repo_lifecycle.get_old_file_summary(repo_id, file_path)
                old_file_embedding = self.repo_lifecycle.get_old_file_embedding(repo_id, file_path)
                file_diff = self.git.get_file_diff(repo_path, base_commit, origin_head, file_path)

                try:
                    file_index, symbol_indices = loop.run_until_complete(
                        self.pipeline.file_processor.process(
                            file_path=full_path,
                            repo_path=repo_path,
                            repo_id=repo_id,
                            commit_hash=origin_head,
                            parent_module_id=""
                        )
                    )

                    if file_index:
                        file_indices.append(file_index)
                        all_symbol_indices.extend(symbol_indices)
                        processed_files.append(file_path)
                        files_processed += 1

                        # Check if significant (using embedding similarity when available)
                        new_summary = file_index.content if hasattr(file_index, 'content') else ""
                        if self.significance.is_significant(
                            old_file_summary or "",
                            new_summary,
                            file_diff,
                            "file",
                            old_embedding=old_file_embedding
                        ):
                            any_significant_change = True
                            logger.info(f"    Processed {file_path} (significant change)")
                        else:
                            logger.info(f"    Processed {file_path} (minor change)")

                except Exception as e:
                    logger.error(f"    Error processing {file_path}: {e}")

            # 7c. Generate embeddings and store
            all_docs = file_indices + all_symbol_indices
            if all_docs:
                if self.pipeline.embedding_generator:
                    for doc in all_docs:
                        # Get text for embedding
                        text = getattr(doc, '_embedding_text', None) or getattr(doc, 'content', '')
                        if text:
                            doc.embedding = self.pipeline.embedding_generator.generate_embedding(text)

                # Upsert new docs first
                for doc in all_docs:
                    doc_dict = doc.to_dict()
                    self.cb_client.collection.upsert(doc.document_id, doc_dict)

                # Now delete old versions (excluding new commit) - safe because new docs are already saved
                for file_path in processed_files:
                    self._delete_old_file_docs(repo_id, file_path, origin_head)

            # 7d. Regenerate summaries only if significant changes (INSIDE same loop)
            if any_significant_change or code_deleted:
                affected_modules = self.get_affected_modules(code_to_process + code_deleted)
                logger.info(f"    Regenerating summaries ({len(affected_modules)} modules affected)")
                self._regenerate_summaries(repo_id, origin_head, affected_modules, loop)
            else:
                logger.info(f"    Skipping summary regeneration (no significant file changes)")

            # 7e. Process changed doc files (INSIDE same loop)
            if docs_to_process or docs_deleted:
                self._process_doc_changes(repo_id, repo_path, docs_to_process, docs_deleted, loop)
                files_processed += len(docs_to_process)

        finally:
            loop.close()

        duration = (datetime.now() - start_time).total_seconds()
        logger.info(f"  Completed in {duration:.1f}s: {files_processed} processed, {files_deleted} deleted")

        return UpdateResult(
            repo_id=repo_id,
            status=STATUS_UPDATED,
            commit=origin_head,
            files_processed=files_processed,
            files_deleted=files_deleted,
            docs_created=len(file_indices) + len(all_symbol_indices),
            duration_seconds=duration
        )

    def _regenerate_summaries(self, repo_id: str, commit_hash: str, affected_modules: Set[str], loop=None):
        """Regenerate module_summary and repo_summary."""
        try:
            # Get all file_indices for this repo
            query = """
                SELECT META().id, *
                FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type = 'file_index'
            """
            result = self.cb_client.cluster.query(query, repo_id=repo_id)
            file_indices = list(result)

            if not file_indices:
                return

            # For dry-run: show comparison
            old_repo_summary = None
            if self.dry_run:
                old_repo_summary = self.repo_lifecycle.get_old_repo_summary(repo_id)

            # Convert to schema objects
            from v4.schemas import FileIndex, make_file_id
            file_index_objects = []
            for row in file_indices:
                # SELECT * nests fields under bucket name 'code_kosha'
                doc = row.get('code_kosha', row)
                metadata = doc.get('metadata', {})
                fi = FileIndex(
                    document_id=doc.get('document_id') or make_file_id(
                        doc.get('repo_id'), doc.get('file_path'), commit_hash
                    ),
                    repo_id=doc.get('repo_id'),
                    file_path=doc.get('file_path'),
                    commit_hash=commit_hash,
                    content=doc.get('content', ''),
                    language=metadata.get('language', doc.get('language', 'unknown')),
                    line_count=metadata.get('line_count', doc.get('line_count', 0)),
                    imports=metadata.get('imports', doc.get('imports', [])),
                )
                file_index_objects.append(fi)

            # Regenerate summaries (async method) - use passed loop or create new one
            # OPTIMIZATION: Only regenerate affected modules, reuse existing for others
            owns_loop = loop is None
            if owns_loop:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            try:
                module_summaries, repo_summary = loop.run_until_complete(
                    self.pipeline.aggregator.aggregate_all(
                        file_index_objects, repo_id, commit_hash,
                        affected_modules=affected_modules  # Only LLM for these
                    )
                )
            finally:
                if owns_loop:
                    loop.close()

            # Dry-run: show comparison
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

                logger.info(f"\n--- Affected modules ({len(affected_modules)}) ---")
                for m in sorted(affected_modules)[:10]:
                    logger.info(f"  {m or '(root)'}")
                if len(affected_modules) > 10:
                    logger.info(f"  ... and {len(affected_modules) - 10} more")
                return

            # Delete old summaries
            delete_query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type IN ['module_summary', 'repo_summary']
            """
            self.cb_client.cluster.query(delete_query, repo_id=repo_id)

            # Generate embeddings and store
            all_summaries = module_summaries + [repo_summary]
            if self.pipeline.embedding_generator:
                for summary in all_summaries:
                    text = getattr(summary, 'content', '')
                    if text:
                        summary.embedding = self.pipeline.embedding_generator.generate_embedding(text)

            for summary in all_summaries:
                doc = summary.to_dict()
                self.cb_client.collection.upsert(summary.document_id, doc)

        except Exception as e:
            logger.error(f"Error regenerating summaries: {e}")

    def _process_doc_changes(
        self,
        repo_id: str,
        repo_path: Path,
        docs_to_process: List[str],
        docs_deleted: List[str],
        loop=None
    ):
        """Process documentation file changes."""
        from v4.ingest_docs import DocumentIngester

        doc_ingester = DocumentIngester(dry_run=self.dry_run)

        # Use passed loop or create new one
        owns_loop = loop is None
        if owns_loop:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        try:
            for file_path in docs_to_process:
                full_path = repo_path / file_path
                if full_path.exists():
                    self.repo_lifecycle.delete_doc_chunks(repo_id, file_path, self.dry_run)
                    loop.run_until_complete(doc_ingester.process_doc(full_path, repo_path, repo_id))
        finally:
            if owns_loop:
                loop.close()

    def run(self, repo_filter: Optional[str] = None) -> List[UpdateResult]:
        """Run incremental update for all repos."""
        logger.info("=" * 70)
        logger.info("INCREMENTAL V4 UPDATE")
        logger.info("=" * 70)
        logger.info(f"Threshold: {self.threshold:.0%}")
        logger.info(f"Dry run: {self.dry_run}")
        logger.info(f"LLM enabled: {self.enable_llm}")

        # Phase 1: Repository Discovery
        logger.info("\n" + "-" * 70)
        logger.info("Phase 1: Repository Discovery")
        logger.info("-" * 70)

        if repo_filter:
            canonical_repos = {repo_filter}
            logger.info(f"Single repo mode: {repo_filter}")
        else:
            canonical_repos = set(self.repo_lifecycle.get_canonical_repo_list())
            logger.info(f"Canonical repo list: {len(canonical_repos)} repos")

        repos_on_disk = {r['repo_id'] for r in self.repo_lifecycle.discover_repos_on_disk()}
        repos_in_db = self.repo_lifecycle.get_repos_in_database()

        # Categorize
        new_repos = canonical_repos - repos_on_disk
        orphaned_in_db = repos_in_db - canonical_repos
        repos_to_process = canonical_repos & repos_on_disk

        logger.info(f"\nRepository status:")
        logger.info(f"  To clone (new):     {len(new_repos)}")
        logger.info(f"  To process:         {len(repos_to_process)}")
        logger.info(f"  Orphaned in DB:     {len(orphaned_in_db)}")

        results = []
        stats = {'cloned': 0, 'skipped': 0, 'excluded': 0, 'updated': 0, 'full_reingest': 0, 'empty': 0, 'deleted': 0, 'error': 0}

        # Phase 2: Clone new repos
        if new_repos:
            logger.info("\n" + "-" * 70)
            logger.info(f"Phase 2: Cloning {len(new_repos)} New Repos")
            logger.info("-" * 70)

            for repo_id in sorted(new_repos):
                target_path = self.repo_lifecycle.repo_id_to_path(repo_id)
                if self.git.clone(repo_id, target_path, self.config.github_token):
                    repos_to_process.add(repo_id)
                    stats['cloned'] += 1
                else:
                    results.append(UpdateResult(repo_id=repo_id, status=STATUS_ERROR, error='Clone failed'))
                    stats['error'] += 1

        # Phase 3: Delete orphaned repos
        if orphaned_in_db and not repo_filter:
            logger.info("\n" + "-" * 70)
            logger.info(f"Phase 3: Cleaning {len(orphaned_in_db)} Orphaned Repos")
            logger.info("-" * 70)

            for repo_id in sorted(orphaned_in_db):
                deleted = self.repo_lifecycle.delete_repo_docs(repo_id, self.dry_run)
                results.append(UpdateResult(repo_id=repo_id, status=STATUS_DELETED, reason='orphaned', files_deleted=deleted))
                stats['deleted'] += 1

        # Phase 4: Process repos
        logger.info("\n" + "-" * 70)
        logger.info(f"Phase 4: Processing {len(repos_to_process)} Repos")
        logger.info("-" * 70)

        for repo_id in sorted(repos_to_process):
            repo_path = self.repo_lifecycle.repo_id_to_path(repo_id)

            try:
                result = self.process_repo(repo_id, repo_path)
                results.append(result)
                stats[result.status] = stats.get(result.status, 0) + 1
            except Exception as e:
                logger.error(f"Failed to process {repo_id}: {e}")
                results.append(UpdateResult(repo_id=repo_id, status=STATUS_ERROR, error=str(e)))
                stats['error'] += 1

        # Summary
        logger.info("\n" + "=" * 70)
        logger.info("SUMMARY")
        logger.info("=" * 70)
        logger.info(f"Total repos processed: {len(results)}")
        logger.info(f"  Cloned:        {stats['cloned']}")
        logger.info(f"  Skipped:       {stats['skipped']}")
        logger.info(f"  Excluded:      {stats['excluded']}")
        logger.info(f"  Updated:       {stats['updated']}")
        logger.info(f"  Full reingest: {stats['full_reingest']}")
        logger.info(f"  Empty:         {stats['empty']}")
        logger.info(f"  Deleted:       {stats['deleted']}")
        logger.info(f"  Errors:        {stats['error']}")

        total_files = sum(r.files_processed for r in results)
        total_deleted = sum(r.files_deleted for r in results)
        total_time = sum(r.duration_seconds for r in results)

        logger.info(f"\nFiles processed: {total_files}")
        logger.info(f"Files deleted:   {total_deleted}")
        logger.info(f"Total time:      {total_time:.1f}s")

        return results
