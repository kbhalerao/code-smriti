"""
Tests for the symbol-aware module context builder.

The fixtures mirror agkit.io-backend `tier1apps/gislayers/serializers.py`, which
is the file the line-number discrepancy was verified against: tree-sitter puts
`to_internal_value` at L165-178, while the LLM chunker emitted an overlapping
semantic chunk claiming L157-164 — a range that contains none of that function.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from v4.schemas import FileIndex, ModuleSummary, SymbolRef
from v4.module_context import (
    build_file_block,
    build_module_context,
    clean_docstring,
    merge_symbols,
)


def _sym(name, symbol_type, start, end, docstring=None):
    return SymbolRef(
        name=name,
        symbol_type=symbol_type,
        start_line=start,
        end_line=end,
        docstring=docstring,
    )


def _file(path="tier1apps/gislayers/serializers.py", symbols=None, content="Serializers.",
          imports=None, line_count=180):
    return FileIndex(
        document_id="doc1",
        repo_id="kbhalerao/agkit.io-backend",
        file_path=path,
        commit_hash="abc123",
        content=content,
        line_count=line_count,
        language="python",
        imports=imports if imports is not None else ["rest_framework"],
        symbols=symbols or [],
    )


class TestCleanDocstring:
    def test_strips_triple_quotes_and_collapses_whitespace(self):
        raw = '"""Best UTM-zone EPSG for a geometry\'s centroid.\n\n    Used to measure area.\n    """'
        assert clean_docstring(raw) == "Best UTM-zone EPSG for a geometry's centroid. Used to measure area."

    def test_none_and_empty_are_empty(self):
        assert clean_docstring(None) == ""
        assert clean_docstring("   ") == ""

    def test_truncates_at_word_boundary(self):
        out = clean_docstring("word " * 200)
        assert len(out) <= 241
        assert out.endswith("…")
        assert "wor…" not in out


class TestMergeSymbols:
    def test_structural_and_llm_entries_for_same_name_collapse(self):
        merged = merge_symbols([
            _sym("GeoJSONFeatureSerializer", "class", 9, 11),
            _sym("GeoJSONFeatureSerializer", "schema", 10, 20,
                 "Defines a machine-readable response shape."),
        ])
        assert len(merged) == 1
        assert merged[0].symbol_type == "class"

    def test_structural_line_range_wins_over_llm_range(self):
        merged = merge_symbols([
            _sym("VectorLayerListSerializer", "class", 43, 45),
            _sym("VectorLayerListSerializer", "schema", 38, 44, "Serializes layers."),
        ])
        assert (merged[0].start_line, merged[0].end_line) == (43, 45)

    def test_structural_range_wins_regardless_of_pass_order(self):
        merged = merge_symbols([
            _sym("VectorLayerListSerializer", "schema", 38, 44, "Serializes layers."),
            _sym("VectorLayerListSerializer", "class", 43, 45),
        ])
        assert (merged[0].start_line, merged[0].end_line) == (43, 45)

    def test_llm_docstring_fills_gap_when_structural_has_none(self):
        merged = merge_symbols([
            _sym("RasterLayerListSerializer", "class", 52, 54),
            _sym("RasterLayerListSerializer", "schema", 46, 62, "Provides an absolute URL."),
        ])
        assert merged[0].docstring == "Provides an absolute URL."

    def test_real_docstring_preferred_over_llm_description(self):
        merged = merge_symbols([
            _sym("x", "schema", 1, 9, "LLM description."),
            _sym("x", "function", 3, 12, '"""Real docstring."""'),
        ])
        assert merged[0].docstring == "Real docstring."

    def test_llm_only_chunk_carries_no_line_range(self):
        """The whole point: never emit an LLM-guessed line number."""
        merged = merge_symbols([
            _sym("multipolygon_to_geometry_collection_storage", "transform", 157, 164,
                 "Wraps incoming MultiPolygon GeoJSON."),
        ])
        assert merged[0].has_lines is False
        assert merged[0].start_line is None

    def test_llm_only_chunk_renders_without_a_location(self):
        block = build_file_block(_file(symbols=[
            _sym("field_area_acreage_calculation", "calculation", 107, 114, "Calculates acres."),
        ]))
        rendered = block.render()
        assert "field_area_acreage_calculation (calculation): Calculates acres." in rendered
        assert "L107" not in rendered

    def test_significance_survives_the_merge(self):
        """A 3-line class is insignificant alone but significant once merged."""
        merged = merge_symbols([
            _sym("FieldBoundaryDraftSerializer", "class", 77, 79),
            _sym("FieldBoundaryDraftSerializer", "schema", 64, 157, "Handles serialization."),
        ])
        assert merged[0].significant is True


class TestBuildFileBlock:
    def test_insignificant_symbols_are_dropped(self):
        block = build_file_block(_file(symbols=[
            _sym("tiny", "function", 1, 2),
            _sym("_best_utm_epsg", "function", 29, 40, '"""Best UTM-zone EPSG."""'),
        ]))
        names = [s.name for s in block.symbols]
        assert names == ["_best_utm_epsg"]

    def test_documented_symbols_sort_ahead_of_undocumented(self):
        block = build_file_block(_file(symbols=[
            _sym("undocumented", "function", 10, 20),
            _sym("documented", "function", 30, 40, '"""Does a thing."""'),
        ]))
        assert [s.name for s in block.symbols] == ["documented", "undocumented"]

    def test_render_includes_path_language_and_exact_lines(self):
        block = build_file_block(_file(symbols=[
            _sym("_best_utm_epsg", "function", 29, 40, '"""Best UTM-zone EPSG."""'),
        ]))
        rendered = block.render()
        assert "### tier1apps/gislayers/serializers.py (python, 180 lines)" in rendered
        assert "_best_utm_epsg (function, L29-40): Best UTM-zone EPSG." in rendered
        assert "Imports: rest_framework" in rendered

    def test_imports_are_capped(self):
        block = build_file_block(_file(imports=[f"mod{i}" for i in range(20)]), max_imports=3)
        assert block.imports == ["mod0", "mod1", "mod2"]


class TestBuildModuleContext:
    def test_every_file_survives_a_tight_budget(self):
        """The old pipeline truncated the file list; this must not."""
        files = [
            _file(path=f"pkg/f{i}.py", symbols=[
                _sym(f"sym{i}_{j}", "function", j * 10, j * 10 + 8, '"""Docs here."""')
                for j in range(1, 9)
            ])
            for i in range(20)
        ]
        ctx = build_module_context(files, [], char_budget=3000)
        for i in range(20):
            assert f"pkg/f{i}.py" in ctx

    def test_trimming_takes_from_the_largest_block_first(self):
        big = _file(path="pkg/big.py", symbols=[
            _sym(f"big{j}", "function", j * 10, j * 10 + 8, '"""A reasonably long docstring."""')
            for j in range(1, 30)
        ])
        small = _file(path="pkg/small.py", symbols=[
            _sym("small1", "function", 1, 9, '"""Short."""'),
        ])
        ctx = build_module_context([big, small], [], char_budget=1500)
        assert "small1 (function, L1-9)" in ctx
        assert ctx.count("big") < 29

    def test_submodules_are_appended_with_their_prose(self):
        child = ModuleSummary(
            document_id="m1",
            repo_id="r",
            module_path="tier1apps/gislayers/models",
            commit_hash="abc",
            content="Model definitions for GIS layers.",
        )
        ctx = build_module_context([_file()], [child])
        assert "### tier1apps/gislayers/models/ (submodule)" in ctx
        assert "Model definitions for GIS layers." in ctx

    def test_empty_inputs_produce_empty_context(self):
        assert build_module_context([], []) == ""

    def test_files_without_symbols_still_contribute_their_summary(self):
        ctx = build_module_context([_file(content="Custom exceptions.", symbols=[])], [])
        assert "Custom exceptions." in ctx
