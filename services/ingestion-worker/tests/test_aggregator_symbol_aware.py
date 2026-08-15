"""
Tests that module aggregation actually reaches the LLM with symbol-aware context.

The wiring is the part worth pinning down: `build_module_context` can be perfect
and still be bypassed if the aggregator keeps calling the prose-only path, which
is exactly the state this repo was in while the builder existed but was unused.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from v4.aggregator import BottomUpAggregator
from v4.quality import QualityTracker
from v4.schemas import EnrichmentLevel, FileIndex, ModuleSummary, SymbolRef


class StubEnricher:
    """Captures whichever enrichment method the aggregator chose to call."""

    def __init__(self, summary="A generated summary."):
        self.summary = summary
        self.symbol_aware_calls = []
        self.prose_calls = []

    async def enrich_module_symbol_aware(self, module_path, module_context, repo_id):
        self.symbol_aware_calls.append(module_context)
        return {"summary": self.summary, "tokens": 10}

    async def enrich_module(self, module_path, files_context, repo_id):
        self.prose_calls.append(files_context)
        return {"summary": self.summary, "tokens": 10}


def _file(path="pkg/serializers.py"):
    return FileIndex(
        document_id=f"doc-{path}",
        repo_id="r",
        file_path=path,
        commit_hash="abc",
        content="Serializers for GIS data.",
        line_count=180,
        language="python",
        imports=["rest_framework"],
        symbols=[
            SymbolRef(
                name="_best_utm_epsg",
                symbol_type="function",
                start_line=29,
                end_line=40,
                docstring='"""Best UTM-zone EPSG for a geometry\'s centroid."""',
            ),
        ],
    )


def _aggregate(enricher, file_indices, children=None, **kwargs):
    tracker = QualityTracker()
    tracker.start_run("r")
    agg = BottomUpAggregator(enricher, tracker, enable_llm=True)
    return asyncio.run(agg.aggregate_module_summary(
        module_path="pkg",
        file_indices=file_indices,
        child_module_summaries=children or [],
        repo_id="r",
        commit_hash="abc",
        parent_module_id="parent",
        **kwargs,
    ))


class TestSymbolAwareWiring:
    def test_symbol_aware_path_is_the_one_called(self):
        enricher = StubEnricher()
        _aggregate(enricher, [_file()])
        assert len(enricher.symbol_aware_calls) == 1
        assert enricher.prose_calls == []

    def test_context_carries_symbol_names_and_docstrings(self):
        enricher = StubEnricher()
        _aggregate(enricher, [_file()])
        context = enricher.symbol_aware_calls[0]
        assert "_best_utm_epsg (function, L29-40)" in context
        assert "Best UTM-zone EPSG" in context
        assert "pkg/serializers.py" in context

    def test_summary_source_records_the_new_provenance(self):
        doc = _aggregate(StubEnricher(), [_file()])
        assert doc.quality.summary_source == "aggregated_from_symbols"
        assert doc.quality.enrichment_level == EnrichmentLevel.LLM_SUMMARY

    def test_generated_summary_lands_on_the_document(self):
        doc = _aggregate(StubEnricher(summary="Names GISVectorLayer."), [_file()])
        assert doc.content == "Names GISVectorLayer."

    def test_submodule_prose_reaches_the_context(self):
        child = ModuleSummary(
            document_id="m1", repo_id="r", module_path="pkg/models",
            commit_hash="abc", content="Model definitions for GIS layers.",
        )
        enricher = StubEnricher()
        _aggregate(enricher, [_file()], children=[child])
        assert "Model definitions for GIS layers." in enricher.symbol_aware_calls[0]


class TestFallbackBehaviourIsPreserved:
    def test_no_children_means_no_llm_call(self):
        """An empty context must not be sent to the model."""
        enricher = StubEnricher()
        doc = _aggregate(enricher, [])
        assert enricher.symbol_aware_calls == []
        assert doc.quality.enrichment_level == EnrichmentLevel.BASIC

    def test_use_llm_false_skips_the_model(self):
        enricher = StubEnricher()
        _aggregate(enricher, [_file()], use_llm=False)
        assert enricher.symbol_aware_calls == []

    def test_prior_llm_summary_is_carried_forward_when_not_regenerating(self):
        """An unaffected module keeps its good summary instead of downgrading."""
        doc = _aggregate(
            StubEnricher(), [_file()],
            use_llm=False,
            existing_summary=(
                "A previously generated summary.",
                EnrichmentLevel.LLM_SUMMARY,
                "aggregated_from_symbols",
            ),
        )
        assert doc.content == "A previously generated summary."
        assert doc.quality.enrichment_level == EnrichmentLevel.LLM_SUMMARY

    def test_carry_forward_keeps_the_prior_provenance(self):
        """Carrying prose text must not relabel it as symbol-aware.

        The backfill selects work by summary_source; a carried summary stamped
        with the new provenance would be excluded from upgrade forever.
        """
        doc = _aggregate(
            StubEnricher(), [_file()],
            use_llm=False,
            existing_summary=(
                "Older prose-derived summary.",
                EnrichmentLevel.LLM_SUMMARY,
                "aggregated_from_files",
            ),
        )
        assert doc.quality.summary_source == "aggregated_from_files"

    def test_carry_forward_without_a_recorded_source_is_not_claimed_as_upgraded(self):
        doc = _aggregate(
            StubEnricher(), [_file()],
            use_llm=False,
            existing_summary=("Summary of unknown origin.", EnrichmentLevel.LLM_SUMMARY, ""),
        )
        assert doc.quality.summary_source != "aggregated_from_symbols"

    def test_llm_failure_falls_back_to_structural_summary(self):
        class Failing(StubEnricher):
            async def enrich_module_symbol_aware(self, module_path, module_context, repo_id):
                raise RuntimeError("model unavailable")

        doc = _aggregate(Failing(), [_file()])
        assert doc.quality.enrichment_level == EnrichmentLevel.BASIC
        assert "pkg" in doc.content

    def test_structural_fallback_is_not_labelled_symbol_aware(self):
        """A mechanical file listing carries the fallback provenance, not the
        symbol-aware one — otherwise an audit by source reports it as good."""
        class Failing(StubEnricher):
            async def enrich_module_symbol_aware(self, module_path, module_context, repo_id):
                raise RuntimeError("model unavailable")

        doc = _aggregate(Failing(), [_file()])
        assert doc.quality.summary_source == "fallback"
