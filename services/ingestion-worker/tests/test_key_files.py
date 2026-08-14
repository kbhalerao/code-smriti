"""
Tests for module key_files selection.

The observed failure this pins down: `tier1apps/gislayers` stored
["urls.py", "urls.py", "urls.py", "views.py", "urls.py"] — bare basenames, no
deduplication, and an arbitrary first-10 cut.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from v4.aggregator import BottomUpAggregator
from v4.quality import QualityTracker
from v4.schemas import FileIndex


def _agg():
    return BottomUpAggregator(llm_enricher=None, quality_tracker=QualityTracker())


def _file(path, line_count=10):
    return FileIndex(
        document_id=f"doc-{path}",
        repo_id="r",
        file_path=path,
        commit_hash="abc",
        content="",
        line_count=line_count,
    )


class TestKeyFiles:
    def test_returns_paths_not_bare_basenames(self):
        got = _agg()._identify_key_files([_file("tier1apps/gislayers/urls.py")])
        assert got == ["tier1apps/gislayers/urls.py"]

    def test_duplicate_paths_collapse(self):
        """Same path arriving several times must appear once."""
        dupes = [_file("pkg/urls.py") for _ in range(4)]
        assert _agg()._identify_key_files(dupes) == ["pkg/urls.py"]

    def test_same_basename_in_different_folders_both_survive(self):
        got = _agg()._identify_key_files([_file("a/urls.py"), _file("b/urls.py")])
        assert got == ["a/urls.py", "b/urls.py"]

    def test_entry_points_rank_by_pattern_priority(self):
        got = _agg()._identify_key_files([
            _file("pkg/settings.py"),
            _file("pkg/models.py"),
            _file("pkg/urls.py"),
        ])
        assert got == ["pkg/models.py", "pkg/urls.py", "pkg/settings.py"]

    def test_entry_points_outrank_merely_large_files(self):
        got = _agg()._identify_key_files([
            _file("pkg/huge.py", line_count=5000),
            _file("pkg/urls.py", line_count=8),
        ])
        assert got == ["pkg/urls.py", "pkg/huge.py"]

    def test_large_files_ordered_biggest_first(self):
        got = _agg()._identify_key_files([
            _file("pkg/medium.py", line_count=300),
            _file("pkg/biggest.py", line_count=900),
            _file("pkg/large.py", line_count=500),
        ])
        assert got == ["pkg/biggest.py", "pkg/large.py", "pkg/medium.py"]

    def test_small_unrecognised_files_are_excluded(self):
        assert _agg()._identify_key_files([_file("pkg/helper.py", line_count=12)]) == []

    def test_threshold_is_exclusive(self):
        assert _agg()._identify_key_files([_file("pkg/a.py", line_count=200)]) == []
        assert _agg()._identify_key_files([_file("pkg/a.py", line_count=201)]) != []

    def test_limit_keeps_the_highest_ranked(self):
        """The old code kept whatever came first in input order."""
        files = [_file(f"pkg/big{i}.py", line_count=300 + i) for i in range(20)]
        files.append(_file("pkg/models.py"))
        got = _agg()._identify_key_files(files, limit=3)
        assert got[0] == "pkg/models.py"
        assert got[1:] == ["pkg/big19.py", "pkg/big18.py"]

    def test_empty_input_yields_empty(self):
        assert _agg()._identify_key_files([]) == []

    def test_files_without_a_path_are_skipped(self):
        assert _agg()._identify_key_files([_file("", line_count=900)]) == []
