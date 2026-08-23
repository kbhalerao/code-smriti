"""
The durable ingestion queue.

Until now the queue was `sorted(repos_to_process)` — a list computed at the top of
`updater.run`, held in one process's memory, and discarded when that process
ended. Three things followed from that, and this module exists to end all three:

- **A killed run lost its backlog.** The watchdog is how a wedged run is supposed
  to end, and it threw away the plan every time.
- **Alphabetical order starved the tail.** The same repos ran first every night,
  so a kill always ate the same end of the alphabet. Items here are claimed
  oldest-enqueued first, and because the queue survives ticks, a repo that has
  been waiting three ticks is genuinely at the head rather than back at 'a'.
- **Nothing could see the work but the process doing it.** The dashboard read a
  projection published from inside that loop; if the publisher stopped, the queue
  became unobservable. Now the queue is a fact in the corpus, and publishing is a
  read of it.

Items are `RepoPlan`s (see plan.py) — decisions pinned to a commit range, made by
the scan and consumed by the processor. The processor never re-derives them,
which is what lets the two run as separate processes on the same tick without
fighting over `.git`.

**Leases need no timeout.** The processor holds the same exclusive flock the old
run did, so exactly one can be draining at any moment. An item found `leased` at
drain start therefore belongs to a processor that is gone, and reclaiming it is
safe by construction rather than by guessing how long a lease should live.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from couchbase.n1ql import QueryScanConsistency
from couchbase.options import QueryOptions
from loguru import logger

from config import WorkerConfig
from .plan import RepoPlan
from ..schemas import _hash_id

config = WorkerConfig()

QUEUE_TYPE = "ingestion_queue_item"

STATE_QUEUED = "queued"
STATE_LEASED = "leased"
STATE_FAILED = "failed"

# How many times a repo may fail before it stops being retried automatically and
# becomes a thing a human looks at. Three ticks of the same error is enough to
# know it is not transient.
DEFAULT_MAX_ATTEMPTS = 3


def make_queue_id(repo_id: str) -> str:
    """One document per repo, forever — a re-enqueue updates, never appends."""
    return _hash_id(f"ingestion_queue:{repo_id}")


class IngestionQueue:
    """Couchbase-backed queue of decided, pinned repository work."""

    def __init__(self, cb_client, max_attempts: int = DEFAULT_MAX_ATTEMPTS):
        self.cb = cb_client
        self.max_attempts = max_attempts

    # --- writing (scan side) ----------------------------------------------

    def enqueue(self, plan: RepoPlan) -> bool:
        """Queue a repo's work, or refresh it if the plan has moved on.

        Keyed on the repo, so a repo that changes again while still queued has
        its plan replaced rather than queued twice. `enqueued_at` is preserved
        across a refresh: the repo has been waiting since it first changed, and
        resetting the clock would send it to the back of the queue every time
        someone pushed to it — the starvation this ordering exists to prevent.
        """
        doc_id = make_queue_id(plan.repo_id)
        now = datetime.now().isoformat()
        existing = self._get(doc_id) or {}

        doc = {
            "document_id": doc_id,
            "type": QUEUE_TYPE,
            "repo_id": plan.repo_id,
            "state": STATE_QUEUED,
            "plan": plan.to_dict(),
            "attempts": existing.get("attempts", 0),
            "last_error": existing.get("last_error"),
            "enqueued_at": existing.get("enqueued_at") or now,
            "updated_at": now,
            "leased_at": None,
        }
        try:
            self.cb.collection.upsert(doc_id, doc)
            return True
        except Exception as e:
            logger.error(f"Could not enqueue {plan.repo_id}: {e}")
            return False

    def drop(self, repo_id: str) -> None:
        """Remove a repo's item — it has nothing to do, or it is done."""
        try:
            self.cb.collection.remove(make_queue_id(repo_id))
        except Exception:
            # Overwhelmingly the common case: there was no item.
            pass

    # --- reading and claiming (drain side) --------------------------------

    def reclaim_leased(self) -> int:
        """Return every leased item to the queue. Called at drain start.

        Safe without a lease timeout because the processor holds an exclusive
        flock: if an item is leased and we are starting, the processor that
        leased it is gone.
        """
        items = self._query(f"j.state = '{STATE_LEASED}'")
        for item in items:
            item["state"] = STATE_QUEUED
            item["leased_at"] = None
            try:
                self.cb.collection.upsert(item["document_id"], item)
            except Exception as e:
                logger.error(f"Could not reclaim {item.get('repo_id')}: {e}")
        if items:
            logger.warning(
                f"Reclaimed {len(items)} item(s) leased by a processor that did not finish"
            )
        return len(items)

    def claim_next(self) -> Optional[RepoPlan]:
        """Take the longest-waiting queued item and mark it leased."""
        items = self._query(
            f"j.state = '{STATE_QUEUED}' AND j.attempts < {int(self.max_attempts)}",
            order="j.enqueued_at ASC",
            limit=1,
        )
        if not items:
            return None

        item = items[0]
        item["state"] = STATE_LEASED
        item["leased_at"] = datetime.now().isoformat()
        try:
            self.cb.collection.upsert(item["document_id"], item)
        except Exception as e:
            logger.error(f"Could not lease {item.get('repo_id')}: {e}")
            return None

        return RepoPlan.from_dict(item["plan"])

    def record_failure(self, repo_id: str, error: str) -> int:
        """Count a failed attempt. Returns the new attempt count.

        The item goes back to `queued` until it has spent its attempts, so a
        transient failure is retried on a later tick rather than needing a human.
        Once spent it becomes `failed` and stops being claimed — at which point
        it is the caller's business to dead-letter it.
        """
        doc_id = make_queue_id(repo_id)
        item = self._get(doc_id)
        if not item:
            return 0

        item["attempts"] = item.get("attempts", 0) + 1
        item["last_error"] = str(error)[:2000]
        item["updated_at"] = datetime.now().isoformat()
        item["leased_at"] = None
        item["state"] = (
            STATE_FAILED if item["attempts"] >= self.max_attempts else STATE_QUEUED
        )
        try:
            self.cb.collection.upsert(doc_id, item)
        except Exception as e:
            logger.error(f"Could not record failure for {repo_id}: {e}")
        return item["attempts"]

    # --- observation -------------------------------------------------------

    def pending(self, limit: int = 500) -> List[dict]:
        """Everything still to do, longest-waiting first."""
        return self._query(
            f"j.state IN ['{STATE_QUEUED}', '{STATE_LEASED}']",
            order="j.enqueued_at ASC",
            limit=limit,
        )

    def counts(self) -> Dict[str, int]:
        """Counts by state, for the dashboard and for `--queue`."""
        fqn = self._fqn()
        # `j.state IS NOT MISSING` is not redundant. The covering index leads on
        # `state`, and N1QL will not use an index whose leading key has no
        # predicate — so without this the count falls back to scanning every
        # document in the corpus: measured at 25s against 5ms with it.
        query = (
            f"SELECT j.state, COUNT(*) AS n FROM {fqn} AS j "
            f"WHERE j.type = '{QUEUE_TYPE}' AND j.state IS NOT MISSING "
            f"GROUP BY j.state"
        )
        try:
            rows = list(
                self.cb.cluster.query(
                    query, QueryOptions(scan_consistency=QueryScanConsistency.REQUEST_PLUS)
                )
            )
        except Exception as e:
            logger.error(f"Could not count queue: {e}")
            return {}
        return {r.get("state", "unknown"): r.get("n", 0) for r in rows}

    # --- internals ---------------------------------------------------------

    @staticmethod
    def _fqn() -> str:
        return f"`{config.couchbase_bucket}`"

    def _get(self, doc_id: str) -> Optional[dict]:
        try:
            return self.cb.collection.get(doc_id).content_as[dict]
        except Exception:
            return None

    def _query(self, where: str, order: str = "", limit: int = 500) -> List[dict]:
        fqn = self._fqn()
        query = (
            f"SELECT j.* FROM {fqn} AS j WHERE j.type = '{QUEUE_TYPE}' AND {where}"
        )
        if order:
            query += f" ORDER BY {order}"
        query += f" LIMIT {int(limit)}"
        try:
            # REQUEST_PLUS: the scan writes and the processor reads seconds later
            # on the same tick. At the default consistency the drain would miss
            # what the scan had just queued and idle while work sat waiting.
            return list(
                self.cb.cluster.query(
                    query, QueryOptions(scan_consistency=QueryScanConsistency.REQUEST_PLUS)
                )
            )
        except Exception as e:
            logger.error(f"Queue query failed: {e}")
            return []
