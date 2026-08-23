"""
Dead-letter queue for ingestion.

**No silent failures.** Anything the pipeline could not complete and could not
resolve by retrying lands here, and a human works out why. Nothing drains it
automatically — an entry exists precisely because trying harder is not the
answer. The file's in-task retry (`pipeline.process_file_bounded`, run at the
end of the pass) has already happened by the time anything reaches this module.

Why this is a store and not an alert:

`cos doc create` embeds every note for vector search — ~1s on an idle box and far
worse under load, which is the exact 2026-08-21 failure where the alert path blew
its own 120s watchdog while ingestion was still draining off the GPU. One note per
failed file would be a self-inflicted outage, and the first run after the chunker
started reporting failures honestly is precisely when the volume could be large.
So entries go to Couchbase, which is cheap and queryable, and the run posts ONE
aggregated alert pointing at them.

Entries clear when the file next processes successfully. The alert has already
gone out by then, so nothing is lost by clearing, and the queue stays a "what is
broken now" view rather than an append-only log that stops being read.
"""

from datetime import datetime
from typing import Dict, Iterable, List, Optional

from couchbase.n1ql import QueryScanConsistency
from couchbase.options import QueryOptions
from loguru import logger

from config import WorkerConfig
from .schemas import FileFailure, make_dlq_id

config = WorkerConfig()

DLQ_TYPE = "ingestion_dlq"

# Bound on how many entries one run may create. A cap exists for the same reason
# ALERT_SPOOL_MAX does: if something systemic breaks, the hundredth entry tells
# you nothing the first ten did not, and writing them all is its own outage.
DEFAULT_MAX_ENTRIES_PER_RUN = 200


class DeadLetterQueue:
    """Couchbase-backed record of files that failed and were not recovered."""

    def __init__(self, cb_client, max_entries_per_run: int = DEFAULT_MAX_ENTRIES_PER_RUN):
        self.cb = cb_client
        self.max_entries_per_run = max_entries_per_run
        self._written_this_run = 0
        self._suppressed = 0

    # --- writing ----------------------------------------------------------

    def record(
        self,
        repo_id: str,
        failures: Iterable[FileFailure],
        run_id: str = "",
        commit: str = "",
    ) -> int:
        """
        Record failures for one repo. Returns how many entries were written.

        Best-effort by design: a DLQ write that raised would take down the repo
        whose failure it is describing, which is a worse outcome than a lost
        entry. The failure is already in the log by this point.
        """
        written = 0
        now = datetime.now().isoformat()

        for failure in failures:
            if self._written_this_run >= self.max_entries_per_run:
                self._suppressed += 1
                continue

            doc_id = make_dlq_id(repo_id, failure.file_path, failure.kind.value)
            try:
                existing = self._get(doc_id)
                doc = {
                    "document_id": doc_id,
                    "type": DLQ_TYPE,
                    "repo_id": repo_id,
                    "file_path": failure.file_path,
                    "kind": failure.kind.value,
                    "detail": failure.detail[:2000],
                    "retried": failure.retried,
                    "run_id": run_id,
                    "commit": commit,
                    "count": (existing or {}).get("count", 0) + 1,
                    "first_seen": (existing or {}).get("first_seen", now),
                    "last_seen": now,
                }
                self.cb.collection.upsert(doc_id, doc)
                written += 1
                self._written_this_run += 1
            except Exception as e:
                logger.error(f"Could not record DLQ entry for {repo_id} {failure.file_path}: {e}")

        if self._suppressed:
            logger.error(
                f"DLQ cap of {self.max_entries_per_run} reached; {self._suppressed} further "
                f"failure(s) this run were not recorded. Something systemic is wrong."
            )
        return written

    def clear(self, repo_id: str, file_paths: Iterable[str]) -> int:
        """
        Drop every entry for files that have now processed cleanly.

        Called with the files a repo just succeeded on, so a transient failure
        that has since resolved stops occupying the "needs attention" view. The
        alert for it already went out when it happened.
        """
        removed = 0
        for file_path in file_paths:
            for kind in ("chunker", "summary", "timeout", "exception"):
                doc_id = make_dlq_id(repo_id, file_path, kind)
                try:
                    self.cb.collection.remove(doc_id)
                    removed += 1
                except Exception:
                    # Overwhelmingly the common case: there was no entry.
                    pass
        if removed:
            logger.info(f"Cleared {removed} resolved DLQ entry/entries for {repo_id}")
        return removed

    # --- reading ----------------------------------------------------------

    def open_entries(self, repo_id: Optional[str] = None, limit: int = 500) -> List[dict]:
        """Every entry currently needing attention, newest first."""
        bucket = config.couchbase_bucket
        where = "d.type = $dlq_type"
        params = {"dlq_type": DLQ_TYPE, "limit": int(limit)}
        if repo_id:
            where += " AND d.repo_id = $repo_id"
            params["repo_id"] = repo_id
        query = (
            f"SELECT d.* FROM `{bucket}` AS d WHERE {where} "
            f"ORDER BY d.last_seen DESC LIMIT $limit"
        )
        try:
            # REQUEST_PLUS, not the default. N1QL is eventually consistent, and
            # measured lag here was ~4s — while both readers of this queue run
            # IMMEDIATELY after the write: the runner takes its summary at the end
            # of the run, and run_incremental.sh posts its notice seconds later.
            # At the default consistency both would report an empty queue and the
            # alert would never fire, which is the exact failure mode this whole
            # change exists to remove.
            return [
                row for row in self.cb.cluster.query(
                    query,
                    QueryOptions(scan_consistency=QueryScanConsistency.REQUEST_PLUS),
                    **params,
                )
            ]
        except Exception as e:
            logger.error(f"Could not read DLQ: {e}")
            return []

    def summary(self) -> Dict[str, int]:
        """Counts by failure kind, for the one aggregated alert a run posts."""
        counts: Dict[str, int] = {}
        for entry in self.open_entries(limit=1000):
            kind = entry.get("kind", "unknown")
            counts[kind] = counts.get(kind, 0) + 1
        return counts

    # --- internals --------------------------------------------------------

    def _get(self, doc_id: str) -> Optional[dict]:
        try:
            return self.cb.collection.get(doc_id).content_as[dict]
        except Exception:
            return None
