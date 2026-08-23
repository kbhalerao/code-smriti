"""
The chunker must distinguish "the model found nothing" from "the call failed".

`_call_llm` used to return `"[]"` from every failure path — timeout, HTTP error,
bare exception, empty message, truncated reply — which is the identical value it
returns when the model reads a file and genuinely finds no additional regions.
Nothing raised, so `file_processor.get_llm_chunks`'s `record_llm_call(success=False)`
never fired either, and the only trace was a log line that nothing counted.

That silence had teeth: the chunker fires on ~65% of files, and for extensions
with no tree-sitter grammar (`.sh`, `.sql`, `.vue`, `.kt`) it is the *only*
source of content — so a swallowed failure put those files in the corpus with
essentially nothing in them while the commits index advanced and declared the
repo current.

The single most important test here is
`test_genuine_empty_result_is_not_a_failure`: if that regresses in the other
direction, every file with nothing to chunk starts reporting a fault.
"""

import asyncio
import sys
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from llm_chunker import (
    MAX_OUTPUT_TOKENS,
    ChunkerCallFailed,
    EnrichmentPass,
    LLMChunker,
)


def _message(text: str, output_tokens: int = 100) -> dict:
    """A well-formed /v1/responses payload carrying one message block."""
    return {
        "usage": {"output_tokens": output_tokens},
        "output": [
            {"type": "message", "content": [{"type": "output_text", "text": text}]}
        ],
    }


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code}",
                request=httpx.Request("POST", "http://test/v1/responses"),
                response=httpx.Response(self.status_code, text=self.text),
            )

    def json(self):
        return self._payload


class FakeClient:
    """Returns each queued item in turn; an Exception instance is raised."""

    def __init__(self, items):
        self.items = list(items)
        self.calls = 0

    async def post(self, url, json=None):
        self.calls += 1
        item = self.items.pop(0) if self.items else self.items
        if isinstance(item, Exception):
            raise item
        return item


class FakeChunker(LLMChunker):
    def __init__(self, items):
        super().__init__()
        self._fake = FakeClient(items)
        # Skip the one-shot /api/show probe; it is not what these tests exercise.
        self._schema_support_checked = True

    @property
    def client(self):
        return self._fake


def _call(chunker, prompt="p", schema=None):
    return asyncio.run(asyncio.wait_for(chunker._call_llm(prompt, schema=schema), timeout=5))


class TestFailureIsDistinguishableFromEmpty:
    def test_genuine_empty_result_is_not_a_failure(self):
        """
        The model looked at the file and found no additional regions. This is a
        real answer and must survive as one — it is the case every other test
        here is trying not to swallow.
        """
        chunker = FakeChunker([FakeResponse(_message("[]"))])
        assert _call(chunker) == "[]"

    def test_http_error_raises(self):
        chunker = FakeChunker([FakeResponse({"error": "boom"}, status_code=500)])
        with pytest.raises(ChunkerCallFailed) as exc:
            _call(chunker)
        assert "500" in str(exc.value)

    def test_transport_error_raises(self):
        chunker = FakeChunker([httpx.ConnectError("connection refused")])
        with pytest.raises(ChunkerCallFailed) as exc:
            _call(chunker)
        assert "ConnectError" in str(exc.value)

    def test_timeout_raises(self):
        chunker = FakeChunker([httpx.ReadTimeout("timed out")])
        with pytest.raises(ChunkerCallFailed):
            _call(chunker)

    def test_empty_message_raises(self):
        """A thinking model that burns its output budget returns status=completed
        with an empty message. Lost call, not an empty file."""
        chunker = FakeChunker([FakeResponse(_message("   "))])
        with pytest.raises(ChunkerCallFailed) as exc:
            _call(chunker)
        assert "empty message" in str(exc.value)

    def test_truncated_at_output_ceiling_raises(self):
        """Schema-constrained decoding makes a truncated reply a valid prefix, so
        it fails to parse for a reason unrelated to the content."""
        chunker = FakeChunker([FakeResponse(_message('[{"name":', MAX_OUTPUT_TOKENS))])
        with pytest.raises(ChunkerCallFailed) as exc:
            _call(chunker)
        assert "ceiling" in str(exc.value)

    def test_unexpected_payload_shape_raises(self):
        chunker = FakeChunker([FakeResponse({"nothing": "useful"})])
        with pytest.raises(ChunkerCallFailed):
            _call(chunker)


class TestParseDiscrimination:
    def test_valid_list_parses(self):
        chunker = FakeChunker([])
        assert chunker._parse_llm_response('[{"name": "x"}]') == [{"name": "x"}]

    def test_empty_list_parses(self):
        chunker = FakeChunker([])
        assert chunker._parse_llm_response("[]") == []

    def test_fenced_json_still_parses(self):
        """Boundary guard for a server that ignores the response schema."""
        chunker = FakeChunker([])
        assert chunker._parse_llm_response('```json\n[{"a": 1}]\n```') == [{"a": 1}]

    def test_unparseable_raises_rather_than_returning_empty(self):
        """
        Under constrained decoding the reply is valid JSON by construction, so
        landing here means the server did not honour the schema — the documented
        symptom of a non-GGUF chunker model, which once cost ~3-5% of chunks
        undetected.
        """
        chunker = FakeChunker([])
        with pytest.raises(ChunkerCallFailed) as exc:
            chunker._parse_llm_response("Sure! Here are the chunks I found:")
        assert "not valid JSON" in str(exc.value)

    def test_non_list_json_raises(self):
        chunker = FakeChunker([])
        with pytest.raises(ChunkerCallFailed) as exc:
            chunker._parse_llm_response('{"name": "x"}')
        assert "expected a list" in str(exc.value)


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


class TestAnalyzeFilePartialResults:
    def test_one_failing_pass_does_not_discard_the_others(self):
        """
        Passes are independent. A failure in the second must be reported, but the
        first pass's chunks were already earned and are attached to the exception
        rather than thrown away.
        """
        chunker = FakeChunker([
            FakeResponse(_message(_chunk_json("found_by_pass_one"))),
            FakeResponse(_message("")),
        ])

        with pytest.raises(ChunkerCallFailed) as exc:
            asyncio.run(asyncio.wait_for(
                chunker.analyze_file(
                    file_path="t.py",
                    content="x" * 1000,
                    language="python",
                    existing_chunks=[],
                    passes=[_pass("one"), _pass("two")],
                ),
                timeout=5,
            ))

        assert [c.name for c in exc.value.partial_chunks] == ["found_by_pass_one"]
        assert "1 of 2" in str(exc.value)

    def test_a_later_pass_still_runs_after_an_earlier_one_fails(self):
        chunker = FakeChunker([
            FakeResponse(_message("")),
            FakeResponse(_message(_chunk_json("found_by_pass_two"))),
        ])

        with pytest.raises(ChunkerCallFailed) as exc:
            asyncio.run(asyncio.wait_for(
                chunker.analyze_file(
                    file_path="t.py",
                    content="x" * 1000,
                    language="python",
                    existing_chunks=[],
                    passes=[_pass("one"), _pass("two")],
                ),
                timeout=5,
            ))

        assert chunker._fake.calls == 2, "the second pass must still be attempted"
        assert [c.name for c in exc.value.partial_chunks] == ["found_by_pass_two"]

    def test_all_passes_succeeding_returns_chunks_normally(self):
        chunker = FakeChunker([
            FakeResponse(_message(_chunk_json("a"))),
            FakeResponse(_message(_chunk_json("b"))),
        ])

        chunks = asyncio.run(asyncio.wait_for(
            chunker.analyze_file(
                file_path="t.py",
                content="x" * 1000,
                language="python",
                existing_chunks=[],
                passes=[_pass("one"), _pass("two")],
            ),
            timeout=5,
        ))
        assert [c.name for c in chunks] == ["a", "b"]

    def test_all_passes_empty_is_not_a_failure(self):
        chunker = FakeChunker([
            FakeResponse(_message("[]")),
            FakeResponse(_message("[]")),
        ])

        chunks = asyncio.run(asyncio.wait_for(
            chunker.analyze_file(
                file_path="t.py",
                content="x" * 1000,
                language="python",
                existing_chunks=[],
                passes=[_pass("one"), _pass("two")],
            ),
            timeout=5,
        ))
        assert chunks == []
