"""
A file that runs out of time keeps the LLM work it already finished.

The per-file bound used to be one `asyncio.wait_for` around the whole file. On
expiry it cancelled everything, including chunker passes and summaries that had
completed, and rebuilt the file with no LLM at all. Under contention the time
spent waiting for an ollama slot counts against the file, so a file could lose
two good passes because a third queued behind someone else's work.

The budget is now a deadline that each LLM step runs against. Steps that
finished are kept, steps that run into the deadline or start after it fall
back, and one TIMEOUT failure is recorded. The `wait_for` survives only as a
backstop, which tests/test_file_failure_handling.py pins.
"""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_chunker import ChunkerCallFailed, EnrichmentPass, LLMChunker
from v4.file_processor import FileProcessor


def _pass(name: str) -> EnrichmentPass:
    return EnrichmentPass(
        name=name,
        focus="test",
        prompt_template="{language} {content} {existing_chunks}",
        types=["business_logic"],
        min_file_size=0,
    )


def _chunk_json(name: str) -> str:
    return (
        '[{"type": "business_logic", "name": "%s", "content": "x", '
        '"start_line": 1, "end_line": 2, "purpose": "p", '
        '"related_symbols": [], "tags": [], "confidence": 0.9}]' % name
    )


def _chunker(script):
    """An LLMChunker whose calls follow `script`: (delay_seconds, pass_name) per call."""
    chunker = LLMChunker(base_url="http://test", model="test")
    calls = list(script)
    seen = []

    async def fake_call(prompt, schema=None):
        delay, name = calls.pop(0)
        seen.append(name)
        await asyncio.sleep(delay)
        return _chunk_json(name)

    chunker._call_llm = fake_call
    return chunker, seen


def _analyze(chunker, budget):
    async def go():
        deadline = asyncio.get_running_loop().time() + budget
        return await chunker.analyze_file(
            "f.py", "x" * 100, "python", [],
            passes=[_pass("a"), _pass("b"), _pass("c")],
            deadline=deadline,
        )
    return asyncio.run(go())


class TestChunkerDeadline:
    def test_finished_passes_survive_a_pass_that_runs_into_the_deadline(self):
        chunker, _ = _chunker([(0, "a"), (5, "b"), (0, "c")])
        with pytest.raises(ChunkerCallFailed) as exc:
            _analyze(chunker, budget=0.2)
        assert [c.name for c in exc.value.partial_chunks] == ["a"]
        assert exc.value.budget_exhausted

    def test_passes_after_the_deadline_are_not_started(self):
        chunker, seen = _chunker([(0, "a"), (5, "b"), (0, "c")])
        with pytest.raises(ChunkerCallFailed):
            _analyze(chunker, budget=0.2)
        assert seen == ["a", "b"], "pass c must be skipped, not sent"

    def test_within_budget_is_unchanged(self):
        chunker, seen = _chunker([(0, "a"), (0, "b"), (0, "c")])
        chunks = _analyze(chunker, budget=5)
        assert [c.name for c in chunks] == ["a", "b", "c"]

    def test_a_bad_response_is_not_mistaken_for_the_budget(self):
        chunker = LLMChunker(base_url="http://test", model="test")

        async def bad(prompt, schema=None):
            raise ChunkerCallFailed("not valid JSON")

        chunker._call_llm = bad
        with pytest.raises(ChunkerCallFailed) as exc:
            _analyze(chunker, budget=5)
        assert not exc.value.budget_exhausted


class TestWithinBudget:
    @staticmethod
    def _run(deadline_offset, llm_delay):
        async def go():
            loop = asyncio.get_running_loop()
            deadline = None if deadline_offset is None else loop.time() + deadline_offset

            async def llm():
                await asyncio.sleep(llm_delay)
                return "llm"

            async def fallback():
                return "fallback"

            return await FileProcessor._within_budget(deadline, llm, fallback)
        return asyncio.run(go())

    def test_no_deadline_always_uses_the_llm(self):
        assert self._run(None, 0) == ("llm", False)

    def test_inside_the_budget_uses_the_llm(self):
        assert self._run(5, 0) == ("llm", False)

    def test_past_the_deadline_falls_back_without_calling_the_llm(self):
        assert self._run(-1, 5) == ("fallback", True)

    def test_a_step_that_runs_into_the_deadline_falls_back(self):
        assert self._run(0.1, 5) == ("fallback", True)


class _SlowEnricher:
    """Answers symbol summaries after the delay given per symbol name."""

    def __init__(self, delays):
        self.delays = delays

    async def enrich_symbol(self, symbol_name, **_):
        await asyncio.sleep(self.delays.get(symbol_name, 0))
        return {"summary": f"llm summary of {symbol_name}"}

    async def enrich_file(self, **_):
        return {"summary": "llm file summary"}


def _processor(delays):
    from v4.quality import QualityTracker
    from v4.schemas import SymbolRef

    class _Parser:
        def detect_language(self, path):
            return "python"

    fp = FileProcessor(_Parser(), _SlowEnricher(delays), QualityTracker(),
                       enable_llm=True, llm_chunker=None)
    fp.llm_chunker = None  # no chunker passes: this test is about summaries
    fp.get_file_at_commit = lambda *a: "def fast():\n    pass\n" * 40

    async def symbols(*a):
        return [SymbolRef("fast", "function", 1, 20), SymbolRef("slow", "function", 21, 40)]

    fp.extract_symbols = symbols
    return fp


class TestProcessKeepsFinishedWork:
    def _process(self, delays, budget):
        fp = _processor(delays)

        async def go():
            deadline = asyncio.get_running_loop().time() + budget
            return await fp.process(Path("/repo/f.py"), Path("/repo"), "o/r", "abc",
                                    "", deadline=deadline)
        return asyncio.run(go())

    def test_summary_finished_before_the_deadline_is_kept(self):
        result = self._process({"slow": 5}, budget=0.3)
        by_name = {d.symbol_name: d for d in result.symbols}
        assert by_name["fast"].content == "llm summary of fast"
        assert by_name["slow"].content != "llm summary of slow"
        assert result.file_index is not None

    def test_one_timeout_failure_is_recorded(self):
        result = self._process({"slow": 5}, budget=0.3)
        kinds = [f.kind.value for f in result.failures]
        assert kinds == ["timeout"]

    def test_inside_the_budget_nothing_fails(self):
        result = self._process({}, budget=5)
        assert result.failures == []
        assert result.file_index.content == "llm file summary"
