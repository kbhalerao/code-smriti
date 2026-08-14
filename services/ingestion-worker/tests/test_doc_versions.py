"""
Tests for per-path document version resolution.

The invariant that makes the purge safe is that exactly one doc survives per key,
so `partition_superseded` can never return the last remaining version of a path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from v4.doc_versions import (
    file_key,
    newest_per_key,
    partition_superseded,
    symbol_key,
)


def _doc(doc_id, path="a.py", repo="r", created=None, symbol=None):
    d = {"document_id": doc_id, "repo_id": repo, "file_path": path}
    if created is not None:
        d["version"] = {"created_at": created}
    if symbol is not None:
        d["symbol_name"] = symbol
    return d


class TestNewestPerKey:
    def test_picks_the_most_recent_version(self):
        docs = [
            _doc("old", created="2025-11-29T05:14:27"),
            _doc("new", created="2026-05-13T15:07:07"),
            _doc("mid", created="2026-02-14T15:21:43"),
        ]
        assert [d["document_id"] for d in newest_per_key(docs, file_key)] == ["new"]

    def test_result_is_order_independent(self):
        a = _doc("old", created="2025-11-29T05:14:27")
        b = _doc("new", created="2026-05-13T15:07:07")
        assert newest_per_key([a, b], file_key)[0]["document_id"] == "new"
        assert newest_per_key([b, a], file_key)[0]["document_id"] == "new"

    def test_missing_version_sorts_oldest(self):
        docs = [_doc("nover"), _doc("dated", created="2025-01-01T00:00:00")]
        assert newest_per_key(docs, file_key)[0]["document_id"] == "dated"

    def test_ties_break_deterministically_on_document_id(self):
        docs = [_doc("aaa", created="2026-01-01T00:00:00"),
                _doc("zzz", created="2026-01-01T00:00:00")]
        assert newest_per_key(docs, file_key)[0]["document_id"] == "zzz"
        assert newest_per_key(list(reversed(docs)), file_key)[0]["document_id"] == "zzz"

    def test_distinct_paths_are_independent(self):
        docs = [
            _doc("a1", path="a.py", created="2026-01-01T00:00:00"),
            _doc("b1", path="b.py", created="2025-01-01T00:00:00"),
        ]
        assert len(newest_per_key(docs, file_key)) == 2

    def test_same_path_in_different_repos_does_not_collide(self):
        docs = [
            _doc("r1", repo="one", created="2026-01-01T00:00:00"),
            _doc("r2", repo="two", created="2025-01-01T00:00:00"),
        ]
        assert len(newest_per_key(docs, file_key)) == 2

    def test_symbol_key_separates_symbols_within_one_file(self):
        docs = [
            _doc("s1", symbol="foo", created="2026-01-01T00:00:00"),
            _doc("s2", symbol="bar", created="2026-01-01T00:00:00"),
            _doc("s3", symbol="foo", created="2025-01-01T00:00:00"),
        ]
        kept = {d["document_id"] for d in newest_per_key(docs, symbol_key)}
        assert kept == {"s1", "s2"}


class TestPartitionSuperseded:
    def test_splits_current_from_superseded(self):
        docs = [
            _doc("old", created="2025-11-29T05:14:27"),
            _doc("new", created="2026-05-13T15:07:07"),
        ]
        current, superseded = partition_superseded(docs, file_key)
        assert [d["document_id"] for d in current] == ["new"]
        assert [d["document_id"] for d in superseded] == ["old"]

    def test_a_single_version_is_never_superseded(self):
        current, superseded = partition_superseded([_doc("only")], file_key)
        assert len(current) == 1
        assert superseded == []

    def test_every_key_retains_exactly_one_doc(self):
        docs = [
            _doc(f"{path}-{i}", path=path, created=f"202{i}-01-01T00:00:00")
            for path in ("a.py", "b.py", "c.py")
            for i in range(1, 5)
        ]
        current, superseded = partition_superseded(docs, file_key)
        assert len(current) == 3
        assert len(superseded) == 9
        assert len(current) + len(superseded) == len(docs)

    def test_docs_missing_document_id_do_not_collide(self):
        """Identity-based bookkeeping: two id-less docs must not merge."""
        a = {"repo_id": "r", "file_path": "a.py", "version": {"created_at": "2026-01-01"}}
        b = {"repo_id": "r", "file_path": "b.py", "version": {"created_at": "2026-01-01"}}
        current, superseded = partition_superseded([a, b], file_key)
        assert len(current) == 2
        assert superseded == []

    def test_superseded_and_current_are_disjoint(self):
        docs = [_doc(f"d{i}", created=f"202{i}-01-01T00:00:00") for i in range(1, 4)]
        current, superseded = partition_superseded(docs, file_key)
        assert not ({id(d) for d in current} & {id(d) for d in superseded})
