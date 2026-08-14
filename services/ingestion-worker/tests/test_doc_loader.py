"""
Tests for rehydrating v4 documents out of Couchbase.

The regression that motivated this module: `updater._regenerate_summaries` built
FileIndex objects by hand and omitted `metadata.symbols`, so the symbol-aware
module context silently received files with no symbols on the incremental path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from v4.doc_loader import (
    is_direct_child_file,
    is_direct_child_module,
    rehydrate_file_index,
    rehydrate_module_summary,
    rehydrate_symbols,
    resolve_module_children,
)


def _file_doc(path, symbols=None, content="A file.", commit="abc"):
    return {
        "document_id": f"doc-{path}",
        "repo_id": "kbhalerao/agkit.io-backend",
        "file_path": path,
        "commit_hash": commit,
        "content": content,
        "metadata": {
            "line_count": 180,
            "language": "python",
            "imports": ["rest_framework"],
            "symbols": symbols or [],
        },
    }


def _module_doc(path, content="A module."):
    return {
        "document_id": f"mod-{path}",
        "repo_id": "kbhalerao/agkit.io-backend",
        "module_path": path,
        "commit_hash": "abc",
        "content": content,
        "metadata": {"file_count": 3, "key_files": ["urls.py"]},
    }


class TestRehydrateSymbols:
    def test_lines_array_maps_to_start_and_end(self):
        syms = rehydrate_symbols({"symbols": [
            {"name": "_best_utm_epsg", "type": "function", "lines": [29, 40],
             "docstring": '"""Best UTM-zone EPSG."""'},
        ]})
        assert (syms[0].name, syms[0].start_line, syms[0].end_line) == ("_best_utm_epsg", 29, 40)
        assert syms[0].symbol_type == "function"

    def test_missing_or_short_lines_do_not_raise(self):
        syms = rehydrate_symbols({"symbols": [
            {"name": "a", "type": "function"},
            {"name": "b", "type": "function", "lines": [5]},
        ]})
        assert (syms[0].start_line, syms[0].end_line) == (0, 0)
        assert (syms[1].start_line, syms[1].end_line) == (5, 0)

    def test_absent_symbols_key_yields_empty(self):
        assert rehydrate_symbols({}) == []


class TestRehydrateFileIndex:
    def test_symbols_survive_the_round_trip(self):
        """The exact field the hand-rolled conversion dropped."""
        doc = _file_doc("a.py", symbols=[
            {"name": "f", "type": "function", "lines": [1, 9], "docstring": None},
        ])
        assert len(rehydrate_file_index(doc).symbols) == 1

    def test_metadata_fields_are_read_from_metadata(self):
        fi = rehydrate_file_index(_file_doc("a.py"))
        assert fi.line_count == 180
        assert fi.language == "python"
        assert fi.imports == ["rest_framework"]

    def test_commit_hash_override_wins(self):
        fi = rehydrate_file_index(_file_doc("a.py", commit="old"), commit_hash="new")
        assert fi.commit_hash == "new"

    def test_stored_commit_used_when_no_override(self):
        assert rehydrate_file_index(_file_doc("a.py", commit="old")).commit_hash == "old"

    def test_missing_fields_become_empty_not_none(self):
        fi = rehydrate_file_index({})
        assert fi.repo_id == "" and fi.content == "" and fi.imports == []
        assert fi.symbols == []


class TestRehydrateModuleSummary:
    def test_reads_metadata_fields(self):
        ms = rehydrate_module_summary(_module_doc("pkg/sub"))
        assert ms.module_path == "pkg/sub"
        assert ms.file_count == 3
        assert ms.key_files == ["urls.py"]


class TestDirectChildPredicates:
    def test_file_directly_in_module(self):
        assert is_direct_child_file("pkg/a.py", "pkg")

    def test_file_in_subfolder_is_not_direct(self):
        assert not is_direct_child_file("pkg/sub/a.py", "pkg")

    def test_prefix_collision_is_rejected(self):
        """`pkgx/` must not count as a child of `pkg`."""
        assert not is_direct_child_file("pkgx/a.py", "pkg")

    def test_root_module_takes_only_top_level_files(self):
        assert is_direct_child_file("a.py", "")
        assert not is_direct_child_file("pkg/a.py", "")

    def test_direct_submodule(self):
        assert is_direct_child_module("pkg/sub", "pkg")
        assert not is_direct_child_module("pkg/sub/deep", "pkg")

    def test_module_is_not_its_own_child(self):
        assert not is_direct_child_module("pkg", "pkg")

    def test_root_submodules_are_top_level_only(self):
        assert is_direct_child_module("pkg", "")
        assert not is_direct_child_module("pkg/sub", "")
        assert not is_direct_child_module("", "")


class TestResolveModuleChildren:
    def test_selects_only_direct_children(self):
        files = [_file_doc("pkg/a.py"), _file_doc("pkg/sub/b.py"), _file_doc("other/c.py")]
        mods = [_module_doc("pkg/sub"), _module_doc("pkg/sub/deep"), _module_doc("other")]
        got_files, got_mods = resolve_module_children(files, mods, "pkg")
        assert [f.file_path for f in got_files] == ["pkg/a.py"]
        assert [m.module_path for m in got_mods] == ["pkg/sub"]

    def test_results_are_sorted_for_stable_prompts(self):
        files = [_file_doc("pkg/z.py"), _file_doc("pkg/a.py"), _file_doc("pkg/m.py")]
        got_files, _ = resolve_module_children(files, [], "pkg")
        assert [f.file_path for f in got_files] == ["pkg/a.py", "pkg/m.py", "pkg/z.py"]

    def test_empty_repo_yields_empty_children(self):
        assert resolve_module_children([], [], "pkg") == ([], [])
