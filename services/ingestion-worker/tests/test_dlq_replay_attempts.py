"""
Dead-letter entries are replayed automatically, and only alerted on once exhausted.

On 2026-11-09 a plain re-run of the queue recovered 13 of the first 19 entries
replayed. They had failed under contention or on a one-off bad response, and
the daily notice had been asking a person to look at each of them. Each entry
now carries `replay_attempts`, which the scheduled replay advances. The notice
fires only for entries that have used all of them.

The rule most easily broken: an ordinary ingestion that fails a file again
must not reset its attempts, or a file that fails every day would never be
reported.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from v4.dlq import MAX_REPLAY_ATTEMPTS, DeadLetterQueue, is_exhausted
from v4.schemas import FailureKind, FileFailure


class _Doc:
    def __init__(self, d):
        self.content_as = {dict: d}


class _Collection:
    def __init__(self):
        self.docs = {}

    def get(self, key):
        if key not in self.docs:
            raise KeyError(key)
        return _Doc(dict(self.docs[key]))

    def upsert(self, key, doc):
        self.docs[key] = dict(doc)

    def remove(self, key):
        del self.docs[key]


class _CB:
    def __init__(self):
        self.collection = _Collection()


def _fail(kind=FailureKind.CHUNKER):
    return [FileFailure("f.py", kind, "boom")]


def _only(dlq):
    (doc,) = dlq.cb.collection.docs.values()
    return doc


class TestReplayAttempts:
    def test_a_new_entry_starts_with_no_attempts(self):
        dlq = DeadLetterQueue(_CB())
        dlq.record("o/r", _fail())
        assert _only(dlq)["replay_attempts"] == 0
        assert not is_exhausted(_only(dlq))

    def test_the_replay_sets_the_count(self):
        dlq = DeadLetterQueue(_CB())
        dlq.record("o/r", _fail())
        dlq.record("o/r", _fail(), replay_attempts=1)
        assert _only(dlq)["replay_attempts"] == 1

    def test_an_ordinary_failure_keeps_the_count(self):
        dlq = DeadLetterQueue(_CB())
        dlq.record("o/r", _fail(), replay_attempts=2)
        dlq.record("o/r", _fail())  # the file failed again in normal ingestion
        assert _only(dlq)["replay_attempts"] == 2
        assert _only(dlq)["count"] == 2

    def test_exhausted_at_the_cap(self):
        assert not is_exhausted({"replay_attempts": MAX_REPLAY_ATTEMPTS - 1})
        assert is_exhausted({"replay_attempts": MAX_REPLAY_ATTEMPTS})
        assert not is_exhausted({}), "an entry from before this field existed is not exhausted"


class TestClearByKind:
    def test_clearing_one_kind_leaves_the_others(self):
        dlq = DeadLetterQueue(_CB())
        dlq.record("o/r", _fail(FailureKind.CHUNKER))
        dlq.record("o/r", _fail(FailureKind.TIMEOUT))
        dlq.clear("o/r", ["f.py"], kinds=["timeout"])
        assert [d["kind"] for d in dlq.cb.collection.docs.values()] == ["chunker"]

    def test_default_clear_removes_every_kind(self):
        dlq = DeadLetterQueue(_CB())
        dlq.record("o/r", _fail(FailureKind.CHUNKER))
        dlq.record("o/r", _fail(FailureKind.TIMEOUT))
        dlq.clear("o/r", ["f.py"])
        assert dlq.cb.collection.docs == {}
