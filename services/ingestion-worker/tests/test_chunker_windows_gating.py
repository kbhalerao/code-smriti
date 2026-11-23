"""
Line numbers, windows, snapping and gating in the LLM chunker.

From the 2026-11-23 graded harness (20 files, blind judges):
- the prompt carried no line numbers, so cited ranges were 4-25 lines off;
- content was cut at 15,000 chars, so a 1,420-line file was read to ~line 400;
- api_contracts produced mostly restatements of routes tree-sitter already has,
  and embedded_code ran on files with no code in any string.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import llm_chunker as m
from llm_chunker import (
    DEFAULT_PASSES,
    ENRICHMENT_PASSES,
    has_embedded_code,
    is_underchunked,
    numbered_windows,
    snap_line_range,
)


def _file(n_lines, width=60):
    return "\n".join(f"line {i} " + "x" * width for i in range(1, n_lines + 1))


class TestWindows:
    def test_small_file_is_one_window_covering_every_line(self):
        w = numbered_windows(_file(50))
        assert len(w) == 1
        first, last, total, text = w[0]
        assert (first, last, total) == (1, 50, 50)
        assert text.splitlines()[0].startswith(" 1| line 1 ")
        assert text.splitlines()[-1].startswith("50| line 50 ")

    def test_large_file_is_covered_to_the_last_line(self):
        w = numbered_windows(_file(1420))
        assert len(w) > 1
        assert w[0][0] == 1 and w[-1][1] == 1420
        for (_, prev_last, _, _), (next_first, _, _, _) in zip(w, w[1:]):
            # contiguous coverage with overlap, never a gap
            assert next_first <= prev_last
            assert prev_last - next_first + 1 <= m.WINDOW_OVERLAP_LINES

    def test_every_window_fits_the_budget(self):
        for _, _, _, text in numbered_windows(_file(3000)):
            assert len(text) <= m.WINDOW_CHARS

    def test_one_huge_line_still_makes_progress(self):
        content = "a = 1\n" + "b = '" + "z" * (m.WINDOW_CHARS * 2) + "'\nc = 3"
        w = numbered_windows(content)
        assert w[-1][1] == 3

    def test_empty_file(self):
        assert numbered_windows("") == [(1, 0, 0, "")]


class TestSnap:
    LINES = [
        "def report(db):",
        "    sql = 'SELECT id, name FROM users WHERE active = 1'",
        "    return db.execute(sql)",
    ]

    def test_moves_range_to_quoted_content(self):
        item = {"start_line": 40, "end_line": 41, "content": "SELECT id, name FROM users WHERE active = 1"}
        assert snap_line_range(item, self.LINES)
        assert (item["start_line"], item["end_line"]) == (2, 3)

    def test_leaves_a_correct_range_alone(self):
        item = {"start_line": 2, "end_line": 3, "content": "SELECT id, name FROM users WHERE active = 1"}
        assert not snap_line_range(item, self.LINES)
        assert (item["start_line"], item["end_line"]) == (2, 3)

    def test_leaves_paraphrase_alone(self):
        item = {"start_line": 40, "end_line": 41, "content": "Fetches the active users for the report"}
        assert not snap_line_range(item, self.LINES)
        assert item["start_line"] == 40

    def test_semicolon_joined_content_still_locates(self):
        item = {"start_line": 90, "end_line": 90,
                "content": "x = 0; sql = 'SELECT id, name FROM users WHERE active = 1'"}
        assert snap_line_range(item, self.LINES)
        assert item["start_line"] == 2


class TestEmbeddedGate:
    def test_plain_python_has_nothing_embedded(self):
        src = 'def add(a, b):\n    """Add two numbers and return the sum of them."""\n    return a + b\n'
        assert not has_embedded_code(src, "python")

    def test_sql_string(self):
        assert has_embedded_code('q = "SELECT a FROM t WHERE b = %s"', "python")

    def test_short_regex_counts(self):
        # wallets.py: every model found this and the judges rated it 2/2
        assert has_embedded_code('re.match(r"^0x[0-9a-fA-F]{40}$", address)', "python")

    def test_orm_aggregation_counts(self):
        assert has_embedded_code("qs.values('t').annotate(n=Count('id'))", "python")

    def test_markup_is_not_embedded_in_markup_languages(self):
        html = "<div class='card'>" + "content " * 20 + "</div>"
        assert not has_embedded_code(html, "svelte")
        assert has_embedded_code(f"tpl = '''{html}'''", "python")

    def test_shell(self):
        assert has_embedded_code('subprocess.run("ls -la", shell=True)', "python")


class TestDefaults:
    def test_api_contracts_not_run_by_default(self):
        assert "api_contracts" not in [p.name for p in DEFAULT_PASSES]
        assert "api_contracts" in [p.name for p in ENRICHMENT_PASSES]

    def test_embedded_pass_is_gated(self):
        emb = next(p for p in ENRICHMENT_PASSES if p.name == "embedded_code")
        assert emb.applies is has_embedded_code


def _chunks(n):
    return [{"content": "x"} for _ in range(n)]


class TestUnderchunkGate:
    def test_long_docstring_alone_no_longer_triggers(self):
        src = 'def f():\n    """' + "Explains the function at length. " * 20 + '"""\n    return 1\n'
        needs, reason = is_underchunked("pkg/mod.py", src, _chunks(3), "python")
        assert "long_docstring_or_sql" not in reason
        assert "sql_or_markup_in_long_string" not in reason

    def test_sql_in_long_string_triggers(self):
        src = 'Q = """\n' + "SELECT a, b FROM t JOIN u ON t.id = u.tid WHERE x = 1\n" * 5 + '"""\n'
        needs, reason = is_underchunked("pkg/mod.py", src, _chunks(3), "python")
        assert needs and "sql_or_markup_in_long_string" in reason

    def test_markup_file_markup_does_not_trigger(self):
        src = "<section class='hero'>" + "text " * 30 + "</section>"
        needs, reason = is_underchunked("src/routes/+page.svelte", src, _chunks(3), "svelte")
        assert "embedded_html" not in reason
