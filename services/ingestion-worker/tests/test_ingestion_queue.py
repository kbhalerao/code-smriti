"""
The durable queue's state machine.

The queue exists because `sorted(repos_to_process)` was a list in one process's
memory: a killed run lost its backlog, and alphabetical order meant the watchdog
always ate the same end of the alphabet. Two properties carry the replacement,
and both are easy to break without noticing:

- **Ordering is by how long a repo has been waiting**, and a re-scan of an
  already-queued repo must not reset that clock. If it did, a repo someone pushes
  to every tick would be sent to the back of the queue every tick and would never
  run — the same starvation, wearing different clothes.
- **A failure is retried a bounded number of times and then stops.** An item that
  kept being claimed would burn the GPU on a known-broken repo every five
  minutes, forever.

The N1QL is not faked here — `_query` is overridden with a Python filter over an
in-memory store, so these tests pin the state machine and say nothing about the
index. The index is exercised live (and its absence cost a 25s full-corpus scan
before `idx_ingestion_queue` existed).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from v4.incremental.plan import (
    ACTION_INCREMENTAL,
    ACTION_NONE,
    ACTION_REBUILD,
    RepoPlan,
)
from v4.incremental.queue import (
    STATE_FAILED,
    STATE_LEASED,
    STATE_QUEUED,
    IngestionQueue,
    make_queue_id,
)


class FakeCollection:
    def __init__(self, store):
        self.store = store

    def upsert(self, key, doc):
        self.store[key] = doc

    def get(self, key):
        if key not in self.store:
            raise KeyError(key)

        class R:
            content_as = {dict: self.store[key]}

        return R()

    def remove(self, key):
        if key not in self.store:
            raise KeyError(key)
        del self.store[key]


class FakeCB:
    def __init__(self):
        self.store = {}
        self.collection = FakeCollection(self.store)
        self.cluster = None


class MemoryQueue(IngestionQueue):
    """IngestionQueue over a dict, so the state machine can be tested alone."""

    def _query(self, where, order="", limit=500):
        rows = list(self.cb.store.values())
        if f"j.state = '{STATE_QUEUED}'" in where:
            rows = [r for r in rows if r["state"] == STATE_QUEUED]
            if "j.attempts <" in where:
                rows = [r for r in rows if r.get("attempts", 0) < self.max_attempts]
        elif f"j.state = '{STATE_LEASED}'" in where:
            rows = [r for r in rows if r["state"] == STATE_LEASED]
        elif "IN [" in where:
            rows = [r for r in rows if r["state"] in (STATE_QUEUED, STATE_LEASED)]
        if "enqueued_at ASC" in order:
            rows.sort(key=lambda r: r["enqueued_at"])
        return rows[:limit]

    def counts(self):
        out = {}
        for r in self.cb.store.values():
            out[r["state"]] = out.get(r["state"], 0) + 1
        return out


def _queue(max_attempts=3):
    return MemoryQueue(FakeCB(), max_attempts=max_attempts)


def _plan(repo_id, action=ACTION_INCREMENTAL, files=("a.py",)):
    return RepoPlan(
        repo_id=repo_id,
        action=action,
        base_commit="aaa",
        target_commit="bbb",
        code_to_process=list(files),
        indexable_changed=len(files),
    )


class TestPlanRoundTrip:
    def test_a_plan_survives_the_document(self):
        """The plan IS the queue item; a lossy round trip loses the work."""
        p = _plan("acme/repo", files=("a.py", "b/c.py"))
        p.rebuild_reason = "threshold_exceeded (61%)"
        back = RepoPlan.from_dict(p.to_dict())
        assert back == p

    def test_unknown_fields_are_ignored(self):
        """An older document must not crash a newer reader."""
        data = _plan("acme/repo").to_dict()
        data["some_future_field"] = 1
        assert RepoPlan.from_dict(data).repo_id == "acme/repo"

    def test_only_work_actions_are_work(self):
        assert _plan("a/b", ACTION_INCREMENTAL).is_work
        assert _plan("a/b", ACTION_REBUILD).is_work
        assert not RepoPlan(repo_id="a/b", action=ACTION_NONE).is_work

    def test_id_is_stable_and_per_repo(self):
        assert make_queue_id("a/b") == make_queue_id("a/b")
        assert make_queue_id("a/b") != make_queue_id("a/c")


class TestOrdering:
    def test_longest_waiting_is_claimed_first(self):
        q = _queue()
        for name in ("c/three", "a/one", "b/two"):
            q.enqueue(_plan(name))
        # Enqueue order, not alphabetical order.
        for key, when in zip(sorted(q.cb.store), ["2026-01-03", "2026-01-01", "2026-01-02"]):
            q.cb.store[key]["enqueued_at"] = when
        claimed = [q.claim_next().repo_id for _ in range(3)]
        assert claimed[0] == q.cb.store[make_queue_id(claimed[0])]["repo_id"]
        waits = [q.cb.store[make_queue_id(r)]["enqueued_at"] for r in claimed]
        assert waits == sorted(waits), "should drain oldest-waiting first"

    def test_requeueing_does_not_reset_the_wait(self):
        """
        The starvation guard. A repo pushed to on every tick would otherwise be
        re-enqueued with a fresh timestamp each time and never reach the head.
        """
        q = _queue()
        q.enqueue(_plan("acme/busy", files=("a.py",)))
        first = q.cb.store[make_queue_id("acme/busy")]["enqueued_at"]

        q.enqueue(_plan("acme/busy", files=("a.py", "b.py")))
        item = q.cb.store[make_queue_id("acme/busy")]
        assert item["enqueued_at"] == first, "the wait clock must not restart"
        assert len(item["plan"]["code_to_process"]) == 2, "but the plan must be current"

    def test_one_document_per_repo(self):
        q = _queue()
        q.enqueue(_plan("acme/repo"))
        q.enqueue(_plan("acme/repo"))
        assert len(q.cb.store) == 1


class TestLeasing:
    def test_claiming_leases(self):
        q = _queue()
        q.enqueue(_plan("acme/repo"))
        assert q.claim_next().repo_id == "acme/repo"
        assert q.counts() == {STATE_LEASED: 1}

    def test_a_leased_item_is_not_claimed_twice(self):
        q = _queue()
        q.enqueue(_plan("acme/repo"))
        q.claim_next()
        assert q.claim_next() is None

    def test_reclaim_returns_orphans_to_the_queue(self):
        """
        A processor that died leaves items leased. Only one drain can exist —
        the ingestion flock guarantees it — so anything still leased at start
        belongs to a corpse, and reclaiming needs no lease timeout.
        """
        q = _queue()
        q.enqueue(_plan("acme/repo"))
        q.claim_next()
        assert q.reclaim_leased() == 1
        assert q.counts() == {STATE_QUEUED: 1}
        assert q.claim_next().repo_id == "acme/repo"

    def test_empty_queue_claims_nothing(self):
        assert _queue().claim_next() is None


class TestFailureHandling:
    def test_failures_return_to_the_queue_until_spent(self):
        q = _queue(max_attempts=3)
        q.enqueue(_plan("acme/repo"))
        q.claim_next()
        assert q.record_failure("acme/repo", "boom") == 1
        assert q.counts() == {STATE_QUEUED: 1}, "a transient failure retries next tick"
        assert q.claim_next() is not None

    def test_a_spent_item_stops_being_claimed(self):
        """
        Otherwise a broken repo takes the GPU every five minutes forever.
        """
        q = _queue(max_attempts=2)
        q.enqueue(_plan("acme/repo"))
        q.record_failure("acme/repo", "boom")
        q.record_failure("acme/repo", "boom")
        assert q.counts() == {STATE_FAILED: 1}
        assert q.claim_next() is None

    def test_attempts_survive_a_requeue(self):
        """
        A re-scan must not launder a repo's failure history — that would make the
        attempt cap unreachable for anything that changes often.
        """
        q = _queue(max_attempts=3)
        q.enqueue(_plan("acme/repo"))
        q.record_failure("acme/repo", "boom")
        q.enqueue(_plan("acme/repo", files=("a.py", "b.py")))
        assert q.cb.store[make_queue_id("acme/repo")]["attempts"] == 1

    def test_failure_on_an_absent_item_is_harmless(self):
        assert _queue().record_failure("acme/gone", "boom") == 0


class TestCompletion:
    def test_drop_removes_the_item(self):
        q = _queue()
        q.enqueue(_plan("acme/repo"))
        q.drop("acme/repo")
        assert q.counts() == {}

    def test_dropping_an_absent_item_is_harmless(self):
        _queue().drop("acme/never-queued")
