"""
Tests for how spec results are presented to the synthesis model.

A spec states behaviour agreed while a feature was being built — L4 enumerates
user paths, L3 the state contracts. Once the feature ships the spec stops being
maintained while the code and its docs move on, so the context has to mark the
difference: authoritative for what was *required*, unreliable for what exists
now. Without that marking the model reports design intent as current fact.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.rag.models import SearchResult
from app.rag.synthesis import Synthesizer


def _result(doc_type="spec", **kwargs):
    base = dict(
        document_id="d1",
        doc_type=doc_type,
        repo_id="PeoplesCompany/farmworth_frontend",
        content="## State Contracts (L3)\n\n| State | Trigger |",
        score=7.2,
    )
    base.update(kwargs)
    return SearchResult(**base)


def _format(results):
    """Formatting is pure; the constructor opens no connections."""
    return Synthesizer()._format_context(results)


class TestSpecHeader:
    def test_spec_is_named_as_a_feature_spec(self):
        out = _format([_result(
            spec_name="Comparable Sales Workspace",
            file_path="docs/specs/comparable-sales-workspace.md",
        )])
        assert "Feature Spec: Comparable Sales Workspace" in out
        assert "docs/specs/comparable-sales-workspace.md" in out

    def test_header_path_locates_the_chunk_within_the_spec(self):
        out = _format([_result(spec_name="Dataroom", header_path="Dataroom > State Contracts (L3)")])
        assert "Dataroom > State Contracts (L3)" in out

    def test_falls_back_to_path_when_spec_name_missing(self):
        out = _format([_result(spec_name=None, file_path="docs/specs/x.md")])
        assert "Feature Spec: docs/specs/x.md" in out


class TestSpecProvenance:
    def test_spec_is_marked_as_design_time_intent(self):
        out = _format([_result(spec_name="Dataroom")])
        assert "Design-time spec" in out
        assert "may lag the shipped code" in out

    def test_l_levels_are_named_not_passed_through_raw(self):
        """'L3' alone tells the model nothing about what it contains."""
        out = _format([_result(spec_name="Dataroom", l_levels=["L4", "L3"])])
        assert "L3 state contracts" in out
        assert "L4 user paths" in out

    def test_l_levels_are_ordered_deterministically(self):
        a = _format([_result(l_levels=["L4", "L3", "L5"])])
        b = _format([_result(l_levels=["L5", "L3", "L4"])])
        assert a == b

    def test_intent_patterns_are_surfaced(self):
        out = _format([_result(intent_patterns=["ownership-gated-edit", "two-mode-workspace"])])
        assert "ownership-gated-edit" in out

    def test_unknown_l_level_passes_through_unchanged(self):
        out = _format([_result(l_levels=["L9"])])
        assert "L9" in out

    def test_non_spec_results_get_no_provenance_note(self):
        out = _format([_result(doc_type="document", file_path="docs/guide.md")])
        assert "Design-time spec" not in out


class TestOtherDocTypesStillRender:
    def test_document_shows_path_and_section(self):
        out = _format([_result(
            doc_type="document",
            file_path="docs/dataroom-api-reference.md",
            header_path="API Reference > Approvals",
        )])
        assert "Doc: docs/dataroom-api-reference.md" in out
        assert "API Reference > Approvals" in out

    def test_module_summary_is_labelled_as_a_module(self):
        """Regression: the old file_path branch ran first and shadowed this."""
        out = _format([_result(doc_type="module_summary", file_path="tier1apps/gislayers")])
        assert "Module: tier1apps/gislayers/" in out

    def test_symbol_keeps_its_line_range(self):
        out = _format([_result(
            doc_type="symbol_index",
            symbol_name="_best_utm_epsg",
            symbol_type="function",
            file_path="tier1apps/gislayers/serializers.py",
            start_line=29,
            end_line=40,
        )])
        assert "_best_utm_epsg (function)" in out
        assert "[lines 29-40]" in out

    def test_commit_keeps_its_author_and_date(self):
        out = _format([_result(
            doc_type="commit_index",
            commit_hash="a47d97efdeadbeef",
            author="Kaustubh Bhalerao",
            commit_date="2026-08-13T10:00:00",
        )])
        assert "Commit `a47d97e`" in out
        assert "Kaustubh Bhalerao" in out
        assert "2026-08-13" in out

    def test_empty_results_are_reported_not_crashed(self):
        assert "No relevant documents found" in _format([])
