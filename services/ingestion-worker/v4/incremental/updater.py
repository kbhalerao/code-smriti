"""
Incremental Updater - Main orchestrator for git-based incremental updates.
"""

import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from loguru import logger

from .models import (
    ChangeSet, UpdateResult,
    STATUS_SKIPPED, STATUS_EXCLUDED, STATUS_UPDATED,
    STATUS_FULL_REINGEST, STATUS_EMPTY, STATUS_ERROR, STATUS_DELETED
)
from .git_utils import GitOperations

# Fraction of a repo's *indexable* files that must change before a surgical
# update is abandoned for a full rebuild.
#
# This is not a cost trade-off, and treating it as one is what kept it at 5%. A
# full re-ingest processes every file in the repo; an incremental processes only
# the ones that changed, and both regenerate the module and repo summaries
# afterwards. Full is therefore a strict superset of incremental's work at any
# ratio below 100% — there is no crossover to tune toward, only a point at which
# a bulk delete-and-rebuild is worth its own cost for the drift it clears.
#
# At 5% that point was set absurdly early: 41% of all change-bearing repos were
# rebuilt whole, 58% of them for a change under 20%. farmworthdb spent ~3 hours
# rebuilding 622 files to absorb 39 changed ones. And because a rebuild deletes
# the repo's documents before it writes new ones, each of those hours is time the
# repo does not exist in the corpus — the same window that lost wanderers-return
# entirely when a run was killed inside it.
DEFAULT_REINGEST_THRESHOLD = 0.5
from .repo_lifecycle import RepoLifecycle
from .significance import SignificanceChecker
from ..doc_versions import file_key, newest_per_key
from ..dlq import DeadLetterQueue
from ..schemas import FailureKind, FileFailure
from ..doc_loader import rehydrate_file_index


class IncrementalUpdater:
    """
    Git-based incremental updater for V4 pipeline.

    Full lifecycle:
    1. Get canonical repo list (GitHub API or config file)
    2. Clone new repos not on disk
    3. Delete docs for repos no longer in canonical list
    4. For each repo: fetch, sync worktree to the default branch, compare
       commits, update incrementally or full re-ingest
    """

    def __init__(
        self,
        threshold: float = DEFAULT_REINGEST_THRESHOLD,
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
        from llm_enricher import LLM_CONFIG

        config = WorkerConfig()
        self.threshold = threshold
        self.dry_run = dry_run
        self.enable_llm = enable_llm
        self.llm_config = llm_config or LLM_CONFIG

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
        # Dead-letter queue. Failures that survive their end-of-pass retry land
        # here for a human; nothing drains it automatically.
        self.dlq = DeadLetterQueue(self.cb_client)

        # Set by IngestionRunner so a DLQ entry names the run that produced it.
        self.run_id = ""

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

    def _reconcile_file_children(self, repo_id: str, file_path: str, live_ids: List[str]):
        """
        Drop child docs of a file that the current parse no longer produces.

        Document identity is per-symbol and per-span, not per-commit, so
        reprocessing a file *supersedes* its children by upserting over them. The
        only leftovers are children that stopped existing — a renamed or deleted
        symbol, or a semantic region whose span moved. Those have no new document
        to overwrite them, so they are deleted here by exclusion.

        This replaces a delete keyed on `commit_hash != current`, which was the
        mechanism that let full re-ingests strand whole generations: it only ran
        on the incremental path, so any write that did not go through it left its
        predecessors behind for good.
        """
        try:
            from couchbase.options import QueryOptions
            query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND file_path = $file_path
                  AND type IN ['symbol_index', 'semantic_unit']
                  AND document_id NOT IN $live_ids
            """
            result = self.cb_client.cluster.query(
                query,
                QueryOptions(named_parameters={
                    "repo_id": repo_id,
                    "file_path": file_path,
                    "live_ids": live_ids,
                })
            )
            # N1QL is lazy — the DELETE does not reach the server unless the
            # result is consumed. Silently skipping this is what let the original
            # generational cleanup no-op for months.
            _ = list(result)
        except Exception as e:
            logger.warning(f"Could not reconcile children of {file_path}: {e}")

    def _dead_letter_rebuild_failures(self, repo_id: str, commit: str) -> None:
        """
        Carry a full rebuild's file failures into the DLQ.

        The rebuild path runs through `pipeline.process_files`, which does its own
        end-of-pass retry and leaves what survived on `pipeline.file_failures`.
        Without this the incremental path would report failures and the rebuild
        path would not — and a rebuild touches every file in the repo, so it is
        the larger exposure of the two.
        """
        failures = getattr(self.pipeline, "file_failures", None)
        if not failures:
            return
        self.dlq.record(repo_id, failures, run_id=self.run_id, commit=commit)

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

        # 2. Put the worktree on origin's default branch before anything reads
        #    it. Done here rather than beside each ingestion call below, so that
        #    local_head is always the default branch's head and step 3 compares
        #    like with like. See GitOperations.sync_to_default_branch.
        local_head = self.git.sync_to_default_branch(repo_path)
        if local_head is None:
            # A remote nobody has pushed to yet is not a broken sync. The
            # STATUS_EMPTY branch below is unreachable for these repos, because
            # origin/<branch> cannot resolve without a branch to resolve to, so
            # the distinction has to be made here or six never-populated repos
            # report as errors on every run.
            if self.git.remote_has_branches(repo_path) is False:
                return UpdateResult(
                    repo_id=repo_id, status=STATUS_EMPTY, reason='no_origin_head'
                )
            return UpdateResult(repo_id=repo_id, status=STATUS_ERROR, error='Git sync failed')

        # 3. Get commits
        origin_head = self.git.get_origin_head(repo_path)
        stored_commit = self.repo_lifecycle.get_stored_commit(repo_id)

        if not origin_head:
            logger.info(f"  Empty repo - no branches found")
            return UpdateResult(repo_id=repo_id, status=STATUS_EMPTY, reason='no_origin_head')

        # 4. Check if update needed
        if stored_commit and local_head == origin_head == stored_commit:
            logger.info(f"  Skipping - no changes (commit: {stored_commit[:8]})")
            return UpdateResult(
                repo_id=repo_id,
                status=STATUS_SKIPPED,
                reason='no_changes',
                commit=stored_commit,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 5. New repo - full ingestion
        if not stored_commit:
            logger.info(f"  New repo - full ingestion")
            if not self.dry_run:
                owns_loop = loop is None
                if owns_loop:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self.pipeline.ingest_repository(repo_path, repo_id))
                finally:
                    if owns_loop:
                        loop.close()
                self._dead_letter_rebuild_failures(repo_id, origin_head)
                # Ingest all commits for new repo
                self._ingest_commits(repo_id, repo_path)
            return UpdateResult(
                repo_id=repo_id,
                status=STATUS_FULL_REINGEST,
                reason='new_repo',
                commit=origin_head,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 6. Get changed files
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

        # 7. Decide between a surgical update and a rebuild.
        #
        # The comparison has to be between like and like, and for a long time it
        # was not. `changes.total_changed` counts every path git reports —
        # images, lock files, .po catalogues, CSV fixtures — while the denominator
        # counts `file_index` documents, which exist only for files the pipeline
        # actually ingests. affiliate-sites logged "1064 files changed (765.5%)"
        # against a corpus of 488: a ratio over a population the corpus never
        # contained. Filtering first is also free, because step 8 needs these
        # exact lists anyway and used to recompute them.
        code_to_process, docs_to_process = self.filter_supported_files(changes.files_to_process, repo_path)
        code_deleted, docs_deleted = self.filter_supported_files(changes.deleted, repo_path)
        indexable_changed = (
            len(code_to_process) + len(docs_to_process) + len(code_deleted) + len(docs_deleted)
        )

        # Nothing the corpus holds has moved. The repo is at a new commit, so the
        # pointer has to advance or this is re-evaluated on every run, but there
        # is no work to do: the embeddings already match the repo state.
        if indexable_changed == 0:
            logger.info(
                f"  Skipping - {changes.total_changed} changed file(s), none indexable "
                f"(commit: {origin_head[:8]})"
            )
            return UpdateResult(
                repo_id=repo_id,
                status=STATUS_SKIPPED,
                reason='no_indexable_changes',
                commit=origin_head,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        corpus_files = self.repo_lifecycle.get_repo_file_count(repo_id)

        # An absent corpus is a rebuild, not a ratio. This used to fall out of a
        # `or 1` divide-by-zero guard, which reached the right answer by accident
        # and made every such repo log an absurd percentage (3 files changed,
        # "150.0%"). Say it directly.
        if corpus_files == 0:
            logger.info(f"  No documents in corpus - full ingestion ({indexable_changed} indexable changes)")
            rebuild_reason = 'empty_corpus'
        else:
            change_ratio = indexable_changed / corpus_files
            if change_ratio > self.threshold:
                logger.info(f"  {indexable_changed} indexable of {changes.total_changed} changed files "
                            f"({change_ratio:.1%} of {corpus_files}) > {self.threshold:.0%} threshold "
                            f"- full re-ingestion")
                rebuild_reason = f'threshold_exceeded ({change_ratio:.1%})'
            else:
                rebuild_reason = None

        if rebuild_reason:
            if not self.dry_run:
                owns_loop = loop is None
                if owns_loop:
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self.pipeline.ingest_repository(repo_path, repo_id))
                finally:
                    if owns_loop:
                        loop.close()
                self._dead_letter_rebuild_failures(repo_id, origin_head)
                # Ingest new commits since last stored
                self._ingest_commits(repo_id, repo_path, since_commit=stored_commit)
            return UpdateResult(
                repo_id=repo_id,
                status=STATUS_FULL_REINGEST,
                reason=rebuild_reason,
                commit=origin_head,
                files_processed=changes.total_changed,
                duration_seconds=(datetime.now() - start_time).total_seconds()
            )

        # 8. Surgical incremental update. The filtered lists come from step 7,
        # which needed them to make the decision in the first place.
        logger.info(
            f"  Incremental: +{len(changes.added)} ~{len(changes.modified)} -{len(changes.deleted)} "
            f"({indexable_changed} indexable of {changes.total_changed})"
        )

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
        all_semantic_units = []

        # Use a SINGLE event loop for ALL async operations in this surgical update
        # This is critical because httpx.AsyncClient binds to the loop it's used with
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        # Child doc ids produced this run, per file — the input to reconciliation
        live_children: Dict[str, List[str]] = {}

        # Files whose first attempt reported a failure. Retried once at the end of
        # the pass rather than in place: a file usually fails because the LLM was
        # saturated at that moment, and retrying immediately recreates exactly the
        # condition that caused it.
        needs_retry: List[str] = []
        failures_by_file: Dict[str, List[FileFailure]] = {}

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
                    # Bounded, degraded-not-dropped, and it reports what went
                    # wrong. Calling file_processor.process directly — as this did
                    # — gave the incremental path no wall clock, no fallback and
                    # no failure signal, on the path almost every run takes.
                    result = loop.run_until_complete(
                        self.pipeline.process_file_bounded(
                            file_path=full_path,
                            repo_path=repo_path,
                            repo_id=repo_id,
                            commit_hash=origin_head,
                        )
                    )
                    file_index = result.file_index
                    symbol_indices = result.symbols
                    semantic_units = result.semantic_units
                    if result.failed:
                        needs_retry.append(file_path)
                        failures_by_file[file_path] = result.failures

                    if file_index:
                        file_indices.append(file_index)
                        all_symbol_indices.extend(symbol_indices)
                        all_semantic_units.extend(semantic_units)
                        live_children[file_path] = [
                            d.document_id for d in (*symbol_indices, *semantic_units)
                        ]
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
                    needs_retry.append(file_path)
                    failures_by_file[file_path] = [FileFailure(
                        file_path=file_path,
                        kind=FailureKind.EXCEPTION,
                        detail=f"{type(e).__name__}: {e}",
                    )]

            # 7b-ii. Second chance for files that failed, now that the pass is done.
            # Deliberately not in place: a file usually fails because the LLM was
            # saturated at that moment, so an immediate retry recreates the exact
            # condition that caused it.
            if needs_retry and self.config.retry_failed_files:
                logger.info(f"    Retrying {len(needs_retry)} failed file(s) after the pass")
                for rel_path in needs_retry:
                    full_path = repo_path / rel_path
                    try:
                        retried = loop.run_until_complete(
                            self.pipeline.process_file_bounded(
                                file_path=full_path,
                                repo_path=repo_path,
                                repo_id=repo_id,
                                commit_hash=origin_head,
                            )
                        )
                    except Exception as e:
                        logger.error(f"    Retry of {rel_path} raised: {e}")
                        continue

                    # Replace this file's documents wholesale. Identity is derived
                    # from the path, so the retry produces the same ids and a
                    # stale copy left behind would be upserted over anyway — but
                    # children can differ, and those would leak.
                    file_indices[:] = [d for d in file_indices if d.file_path != rel_path]
                    all_symbol_indices[:] = [d for d in all_symbol_indices if d.file_path != rel_path]
                    all_semantic_units[:] = [d for d in all_semantic_units if d.file_path != rel_path]

                    if retried.file_index:
                        file_indices.append(retried.file_index)
                        all_symbol_indices.extend(retried.symbols)
                        all_semantic_units.extend(retried.semantic_units)
                        live_children[rel_path] = [
                            d.document_id for d in (*retried.symbols, *retried.semantic_units)
                        ]

                    if retried.failed:
                        # Still failing with the LLM quiet. That is a bug in the
                        # pipeline rather than contention, and it goes to the DLQ
                        # for a human. The repo still completes and still
                        # checkpoints — aborting would leave the commit unadvanced
                        # and every later run would reprocess the whole repo over
                        # one poison file.
                        for f in retried.failures:
                            f.retried = True
                        failures_by_file[rel_path] = retried.failures
                        logger.error(
                            f"    {rel_path}: still failing after retry — "
                            + "; ".join(f"{f.kind.value}: {f.detail}" for f in retried.failures)
                        )
                    else:
                        failures_by_file.pop(rel_path, None)
                        logger.info(f"    {rel_path}: recovered on retry")

            # 7b-iii. Dead-letter what is still broken, and clear what is not.
            # A file that came through cleanly drops any stale entry, so the queue
            # stays a "what is broken now" view rather than an append-only log.
            if not self.dry_run:
                recovered = [f for f in code_to_process if f not in failures_by_file]
                if recovered:
                    self.dlq.clear(repo_id, recovered)
                if failures_by_file:
                    self.dlq.record(
                        repo_id,
                        [f for fs in failures_by_file.values() for f in fs],
                        run_id=self.run_id,
                        commit=origin_head,
                    )

            # 7c. Generate embeddings and store
            all_docs = file_indices + all_symbol_indices + all_semantic_units
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

                # Now drop children that no longer exist — safe because the docs
                # that survive have already been upserted above.
                for file_path, live_ids in live_children.items():
                    self._reconcile_file_children(repo_id, file_path, live_ids)

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

        # Ingest new commits since last stored
        self._ingest_commits(repo_id, repo_path, since_commit=base_commit)

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

    def _existing_module_summaries(self, repo_id: str) -> dict:
        """Prior LLM module summaries keyed by module_path, for carry-forward.

        Returns {module_path: (content, EnrichmentLevel.LLM_SUMMARY, source)}.
        Only LLM-enriched summaries are returned — fallback ones aren't worth
        preserving. The stored summary_source travels with the text so a carried
        summary is not relabelled as something it isn't. Keys are normalized to
        the aggregator's loop convention (root folder is "" there but stored as
        "(root)"). Must be called before the old summaries are deleted.
        """
        from v4.schemas import EnrichmentLevel
        query = """
            SELECT module_path, content, quality.enrichment_level AS lvl,
                   quality.summary_source AS src
            FROM `code_kosha`
            WHERE repo_id = $repo_id AND type = 'module_summary'
        """
        existing: dict = {}
        try:
            for row in self.cb_client.cluster.query(query, repo_id=repo_id):
                path = row.get('module_path')
                content = row.get('content')
                if path is None or not content:
                    continue
                if row.get('lvl') != EnrichmentLevel.LLM_SUMMARY.value:
                    continue
                if path == "(root)":
                    path = ""
                existing[path] = (
                    content, EnrichmentLevel.LLM_SUMMARY, row.get('src') or "",
                )
        except Exception as e:
            logger.warning(f"Could not load existing module summaries for {repo_id}: {e}")
        return existing

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

            # SELECT * nests fields under the bucket name.
            docs = [row.get('code_kosha', row) for row in file_indices]

            # This query is deliberately unscoped by commit — a file doc carries
            # the commit at which that file was last processed, so unchanged
            # files legitimately sit behind HEAD and filtering on commit_hash
            # would drop nearly all of them. But nothing purges superseded
            # versions on the full-reingest path either, so the raw result holds
            # several generations of the same path. Aggregating those directly
            # counted gislayers' 7 files as 21 and let stale summaries of deleted
            # code into module summaries; collapse to the newest per path first.
            docs = newest_per_key(docs, file_key)
            if len(docs) != len(file_indices):
                logger.debug(
                    f"    Collapsed {len(file_indices)} file_index docs to "
                    f"{len(docs)} current (superseded versions ignored)"
                )

            # For dry-run: show comparison
            old_repo_summary = None
            if self.dry_run:
                old_repo_summary = self.repo_lifecycle.get_old_repo_summary(repo_id)

            # Convert to schema objects. rehydrate_file_index carries
            # metadata.symbols across — the hand-rolled conversion this replaced
            # dropped them, which left the symbol-aware module context with
            # nothing to work from on the incremental path.
            from v4.schemas import make_file_id
            file_index_objects = []
            for doc in docs:
                fi = rehydrate_file_index(doc, commit_hash=commit_hash)
                if not fi.document_id:
                    fi.document_id = make_file_id(
                        doc.get('repo_id'), doc.get('file_path')
                    )
                file_index_objects.append(fi)

            # Load prior LLM module summaries so unaffected modules keep their
            # good summary instead of being overwritten with the fallback when
            # they aren't LLM-regenerated this run (see aggregate_module_summary).
            existing_summaries = self._existing_module_summaries(repo_id)

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
                        affected_modules=affected_modules,  # Only LLM for these
                        existing_summaries=existing_summaries,
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

            # Delete old summaries. N1QL executes lazily, so the result MUST be
            # consumed or the DELETE never reaches the server — without this the
            # prior commit's module/repo summaries accumulate as orphans in the
            # bucket and FTS index (see _delete_old_file_docs for the same pattern).
            delete_query = """
                DELETE FROM `code_kosha`
                WHERE repo_id = $repo_id
                  AND type IN ['module_summary', 'repo_summary']
            """
            _ = list(self.cb_client.cluster.query(delete_query, repo_id=repo_id))

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

    def _ingest_commits(
        self,
        repo_id: str,
        repo_path: Path,
        since_commit: Optional[str] = None
    ) -> int:
        """
        Ingest commits for a repository.

        Args:
            repo_id: Repository identifier
            repo_path: Path to repository
            since_commit: If provided, only ingest commits after this commit

        Returns:
            Number of commits ingested
        """
        import subprocess
        import hashlib
        from datetime import timezone

        if self.dry_run:
            return 0

        # Build git log command
        format_str = "%H|%ae|%aI|%s"  # hash|author|date|subject
        cmd = ["git", "log", f"--format={format_str}", "--numstat"]
        if since_commit:
            cmd.append(f"{since_commit}..HEAD")

        try:
            result = subprocess.run(
                cmd,
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=300
            )

            if result.returncode != 0:
                logger.warning(f"git log failed for {repo_id}: {result.stderr[:100]}")
                return 0

            # Parse output
            commits = []
            lines = result.stdout.strip().split('\n')
            i = 0

            while i < len(lines):
                line = lines[i].strip()
                if not line or '\t' in line:
                    i += 1
                    continue

                parts = line.split('|', 3)
                if len(parts) != 4:
                    i += 1
                    continue

                commit_hash, author, date, message = parts
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
                        break
                    stat_parts = stat_line.split('\t')
                    if len(stat_parts) >= 3:
                        try:
                            add = int(stat_parts[0]) if stat_parts[0] != '-' else 0
                            delete = int(stat_parts[1]) if stat_parts[1] != '-' else 0
                            lines_added += add
                            lines_deleted += delete
                            files_changed.append(stat_parts[2])
                        except ValueError:
                            pass
                    i += 1

                # Create document
                doc_key = f"commit:{repo_id}:{commit_hash[:12]}"
                doc_id = hashlib.sha256(doc_key.encode()).hexdigest()

                commits.append({
                    "document_id": doc_id,
                    "type": "commit_index",
                    "repo_id": repo_id,
                    "commit_hash": commit_hash,
                    "commit_date": date,
                    "author": author,
                    "content": message,
                    "metadata": {
                        "lines_added": lines_added,
                        "lines_deleted": lines_deleted,
                        "files_changed": files_changed,
                        "file_count": len(files_changed),
                    },
                    "embedding": None,
                    "version": {
                        "schema_version": "v4.0",
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    },
                })

            if not commits:
                return 0

            # Generate embeddings
            if self.pipeline.embedding_generator:
                for commit in commits:
                    # No prefix here: `embeddings.convention` applies whatever
                    # the model expects. Prepending one manually double-prefixed
                    # every commit embedding in the corpus under the old model.
                    commit['embedding'] = self.pipeline.embedding_generator.generate_embedding(
                        commit['content']
                    )

            # Store
            for commit in commits:
                self.cb_client.collection.upsert(commit['document_id'], commit)

            logger.info(f"  Ingested {len(commits)} commits")
            return len(commits)

        except subprocess.TimeoutExpired:
            logger.warning(f"git log timed out for {repo_id}")
            return 0
        except Exception as e:
            logger.warning(f"Error ingesting commits for {repo_id}: {e}")
            return 0

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
            loop.run_until_complete(doc_ingester.initialize())
            for file_path in docs_to_process:
                full_path = repo_path / file_path
                if full_path.exists():
                    self.repo_lifecycle.delete_doc_chunks(repo_id, file_path, self.dry_run)
                    loop.run_until_complete(doc_ingester.process_doc(full_path, repo_path, repo_id))
        finally:
            loop.run_until_complete(doc_ingester.close())
            if owns_loop:
                loop.close()

    # Statuses that mean "this repo's corpus now reflects `result.commit`".
    # An error or an empty repo has nothing to record.
    _CHECKPOINTABLE = (STATUS_UPDATED, STATUS_FULL_REINGEST)

    # Skips are usually a no-op for the index — 'no_changes' means the stored
    # commit is already the right one. These two are not: the repo moved to a
    # new commit and we decided there was nothing to ingest, so the pointer still
    # has to advance or the same decision is re-made, and re-logged, every run.
    _CHECKPOINTABLE_SKIPS = ('no_file_changes', 'no_indexable_changes')

    def _checkpoint_commit(self, result: UpdateResult) -> None:
        """
        Record one repo's new commit as soon as that repo finishes.

        The run-level write in IngestionRunner._save_run_record is not enough on
        its own. It runs off `results`, which only exists once this whole loop
        has returned, so a run killed mid-loop banks nothing — and the watchdog
        kills runs by design. On 2026-08-21 a 10h timeout discarded six fully
        ingested repos that way: their documents were in the corpus, their
        repo_summary docs carried the right commit, and the index still pointed
        at the previous day. The next run would have redone all six, timed out in
        the same place, and banked nothing again — a backlog that can never
        drain. Checkpointing here is what makes the run resumable.

        Deliberately best-effort: a repo that is ingested but not checkpointed
        is merely re-done next time, whereas an exception raised here would lose
        the whole remaining loop.
        """
        if self.dry_run or not result.commit:
            return
        banks = (
            result.status in self._CHECKPOINTABLE
            or (result.status == STATUS_SKIPPED and result.reason in self._CHECKPOINTABLE_SKIPS)
        )
        if not banks:
            return
        try:
            self.repo_lifecycle.update_commits_index({result.repo_id: result.commit})
        except Exception as e:
            logger.warning(f"Could not checkpoint {result.repo_id}@{result.commit[:8]}: {e}")

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
                self._checkpoint_commit(result)
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
