"""
Document identity must not depend on the commit.

These lock in the property that made generational rot impossible to recur: two
ingests of the same thing produce the same key, so the second overwrites the
first instead of orphaning it. The old scheme hashed the commit into the key, so
every full re-ingest inserted a parallel generation and left the previous one
stranded — 1,835 files were carrying more than one when this was measured.
"""

import importlib.util
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from v4.schemas import (
    SemanticUnit,
    SymbolIndex,
    make_content_hash,
    make_file_id,
    make_module_id,
    make_repo_id,
    make_semantic_unit_id,
    make_symbol_id,
)


def _load_migration():
    path = Path(__file__).resolve().parent.parent / "scripts" / "migrate_stable_document_ids.py"
    spec = importlib.util.spec_from_file_location("migrate_stable_document_ids", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestIdentityIsCommitIndependent:
    def test_symbol_id_is_stable_across_commits(self):
        # The whole point: the same symbol at two different commits is one document.
        first = make_symbol_id("owner/repo", "app/models.py", "User.save")
        second = make_symbol_id("owner/repo", "app/models.py", "User.save")
        assert first == second

    def test_ids_differ_per_location(self):
        ids = {
            make_symbol_id("owner/repo", "app/models.py", "save"),
            make_symbol_id("owner/repo", "app/views.py", "save"),
            make_symbol_id("other/repo", "app/models.py", "save"),
            make_symbol_id("owner/repo", "app/models.py", "load"),
        }
        assert len(ids) == 4

    def test_renaming_a_symbol_changes_its_identity(self):
        # Correct: the old name stops existing, so its document is reconciled
        # away rather than silently carrying a summary of code under a dead name.
        assert make_symbol_id("r", "f.py", "old_name") != make_symbol_id("r", "f.py", "new_name")

    def test_file_module_repo_ids_are_stable(self):
        assert make_file_id("r", "a/b.py") == make_file_id("r", "a/b.py")
        assert make_module_id("r", "a") == make_module_id("r", "a")
        assert make_repo_id("r") == make_repo_id("r")

    def test_root_module_is_distinct_from_repo(self):
        # module_path "" is the repo root and is a legitimate key, not a missing one.
        assert make_module_id("r", "") != make_repo_id("r")

    def test_content_hash_tracks_source_not_identity(self):
        assert make_content_hash("a") != make_content_hash("b")
        assert make_content_hash("a") == make_content_hash("a")


class TestSemanticUnitsAreNotSymbols:
    def test_keyed_on_span_not_label(self):
        # The chunker's label is unstable between runs — the same region came back
        # as fema_api_response_validation and fema_response_validation — so the
        # span it pointed at is the only part of its output fit to be identity.
        assert make_semantic_unit_id("r", "f.py", 40, 58) == make_semantic_unit_id("r", "f.py", 40, 58)
        assert make_semantic_unit_id("r", "f.py", 40, 58) != make_semantic_unit_id("r", "f.py", 40, 65)

    def test_semantic_unit_never_serialises_a_symbol_name(self):
        unit = SemanticUnit(
            document_id="d", repo_id="r", file_path="f.py", commit_hash="c",
            label="RealstackAPI.get_users", unit_type="rpc", content="summary",
            start_line=1, end_line=9,
        )
        doc = unit.to_dict()
        assert doc["type"] == "semantic_unit"
        assert doc["label"] == "RealstackAPI.get_users"
        # A consumer reading symbol_name must never pick this up as a real symbol.
        assert "symbol_name" not in doc
        assert "symbol_type" not in doc

    def test_symbol_index_still_serialises_symbol_name(self):
        sym = SymbolIndex(
            document_id="d", repo_id="r", file_path="f.py", commit_hash="c",
            symbol_name="User.save", symbol_type="method", content="summary",
        )
        doc = sym.to_dict()
        assert doc["type"] == "symbol_index"
        assert doc["symbol_name"] == "User.save"


class TestMigrationPlanner:
    """The one-time move of the existing corpus onto stable identities."""

    @staticmethod
    def _rows():
        return [
            # a.py was last processed at NEW
            {"document_id": "f_new", "type": "file_index", "repo_id": "r", "file_path": "a.py",
             "commit_hash": "NEW", "version": {"created_at": "2026-05-01"}},
            {"document_id": "f_old", "type": "file_index", "repo_id": "r", "file_path": "a.py",
             "commit_hash": "OLD", "version": {"created_at": "2025-01-01"}},
            {"document_id": "s_new", "type": "symbol_index", "repo_id": "r", "file_path": "a.py",
             "symbol_name": "foo", "symbol_type": "function", "commit_hash": "NEW",
             "version": {"created_at": "2026-05-01"}, "start_line": 1, "end_line": 9},
            {"document_id": "s_old", "type": "symbol_index", "repo_id": "r", "file_path": "a.py",
             "symbol_name": "foo", "symbol_type": "function", "commit_hash": "OLD",
             "version": {"created_at": "2025-01-01"}, "start_line": 1, "end_line": 9},
            # chunker doc from a generation that is no longer current
            {"document_id": "c_old", "type": "symbol_index", "repo_id": "r", "file_path": "a.py",
             "symbol_name": "fema_thing", "symbol_type": "validation", "commit_hash": "OLD",
             "version": {"created_at": "2025-01-01"}, "start_line": 40, "end_line": 58},
            # b.py's current generation does include a chunker region
            {"document_id": "f2", "type": "file_index", "repo_id": "r", "file_path": "b.py",
             "commit_hash": "NEW", "version": {"created_at": "2026-05-01"}},
            {"document_id": "c_new", "type": "symbol_index", "repo_id": "r", "file_path": "b.py",
             "symbol_name": "workflow_x", "symbol_type": "workflow", "commit_hash": "NEW",
             "version": {"created_at": "2026-05-01"}, "start_line": 5, "end_line": 30},
        ]

    def test_keeps_current_generation_drops_older(self):
        m = _load_migration()
        survivors, to_delete, _, _ = m.plan_repo(self._rows())
        assert "s_new" in survivors and "s_old" in to_delete
        assert "f_new" in survivors and "f_old" in to_delete

    def test_stale_chunker_doc_is_deleted_not_converted(self):
        # a.py's current parse produced no chunker regions, so it has none —
        # historical ones describe a version of the file that no longer exists.
        m = _load_migration()
        survivors, to_delete, _, _ = m.plan_repo(self._rows())
        assert "c_old" in to_delete
        assert "c_old" not in survivors

    def test_current_chunker_doc_becomes_a_semantic_unit(self):
        m = _load_migration()
        survivors, _, _, _ = m.plan_repo(self._rows())
        assert survivors["c_new"][0] == "semantic_unit"
        assert survivors["s_new"][0] == "symbol_index"

    def test_no_document_is_both_moved_and_deleted(self):
        m = _load_migration()
        survivors, to_delete, _, _ = m.plan_repo(self._rows())
        assert not (set(survivors) & set(to_delete))

    def test_reference_map_covers_superseded_generations(self):
        # A child's parent_id often points at an older generation of its parent.
        # If the map only held survivors those references would dangle.
        m = _load_migration()
        _, _, id_map, _ = m.plan_repo(self._rows())
        assert id_map["f_old"] == id_map["f_new"] == make_file_id("r", "a.py")

    def test_never_empties_a_file_when_file_index_disagrees(self):
        # Defensive: a missing or mismatched file_index must not delete every
        # child of a path.
        m = _load_migration()
        rows = [
            {"document_id": "s1", "type": "symbol_index", "repo_id": "r", "file_path": "orphan.py",
             "symbol_name": "foo", "symbol_type": "function", "commit_hash": "X",
             "version": {"created_at": "2026-01-01"}, "start_line": 1, "end_line": 9},
        ]
        survivors, to_delete, _, _ = m.plan_repo(rows)
        assert "s1" in survivors
        assert "s1" not in to_delete

    def test_to_semantic_unit_demotes_name_to_label(self):
        m = _load_migration()
        converted = m.to_semantic_unit({
            "type": "symbol_index", "symbol_name": "get_cities", "symbol_type": "rpc",
            "metadata": {"docstring": "a chunker purpose, not a real docstring"},
        })
        assert converted["type"] == "semantic_unit"
        assert converted["label"] == "get_cities"
        assert converted["unit_type"] == "rpc"
        assert "symbol_name" not in converted
        assert "docstring" not in converted["metadata"]

    def test_parser_types_are_the_discriminator(self):
        m = _load_migration()
        assert m.is_parser_symbol({"symbol_type": "function"})
        assert m.is_parser_symbol({"symbol_type": "method"})
        assert not m.is_parser_symbol({"symbol_type": "workflow"})
        assert not m.is_parser_symbol({"symbol_type": "integration"})
        assert not m.is_parser_symbol({})
