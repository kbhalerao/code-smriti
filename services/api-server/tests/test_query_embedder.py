"""
Query embedding over Ollama.

These pin the parts that fail silently if they drift: the query instruction
(measured at 0.77 minimum cosine to the corpus space without it), the width
check at the HTTP boundary, and the startup probe. No network: httpx's mock
transport stands in for Ollama.
"""

import asyncio
import json

import httpx
import numpy as np
import pytest

from app.rag import embedding
from app.rag.embedding import (
    EMBEDDING_DIMS,
    EMBEDDING_MODEL,
    EmbeddingServiceError,
    QueryEmbedder,
    assert_corpus_matches,
    embed_query,
    probe_embedder,
)

QUERY_INSTRUCTION = (
    "Instruct: Given a web search query, retrieve relevant passages "
    "that answer the query\nQuery:"
)


def _vector(dims: int = 1024) -> list:
    rng = np.random.default_rng(0)
    v = rng.normal(size=dims).astype(np.float32)
    return (v / np.linalg.norm(v)).tolist()


def _embedder(monkeypatch, handler) -> QueryEmbedder:
    """A QueryEmbedder whose HTTP client is routed to `handler`."""
    real = httpx.AsyncClient
    monkeypatch.setattr(
        embedding.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    return QueryEmbedder("http://ollama:11434/")


def _ok(dims: int = 1024):
    return lambda request: httpx.Response(200, json={"embeddings": [_vector(dims)]})


def test_query_is_sent_with_the_instruction_and_pinned(monkeypatch):
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [_vector()]})

    asyncio.run(embed_query(_embedder(monkeypatch, handler), "where is auth"))

    assert seen["url"] == "http://ollama:11434/api/embed"
    assert seen["body"]["model"] == "qwen3-embedding:0.6b-q8_0"
    assert seen["body"]["input"] == QUERY_INSTRUCTION + "where is auth"
    assert seen["body"]["keep_alive"] == -1


def test_query_vector_is_truncated_and_unit_length(monkeypatch):
    vec = asyncio.run(embed_query(_embedder(monkeypatch, _ok()), "q"))

    assert len(vec) == EMBEDDING_DIMS
    assert np.linalg.norm(vec) == pytest.approx(1.0, abs=1e-5)
    # Matryoshka: the leading dimensions of the native vector, renormalised.
    native = np.asarray(_vector(), dtype=np.float32)[:EMBEDDING_DIMS]
    assert vec == pytest.approx((native / np.linalg.norm(native)).tolist(), abs=1e-5)


def test_wrong_width_is_rejected_not_truncated(monkeypatch):
    # A 4B model answers with 2560 dims. Truncating that to 768 would "work" and
    # land in a different space, so the width is checked before truncation.
    with pytest.raises(EmbeddingServiceError, match="expected 1 of 1024"):
        asyncio.run(embed_query(_embedder(monkeypatch, _ok(2560)), "q"))


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="model failed to load"),
        httpx.Response(200, json={"error": "model not found"}),
        httpx.Response(200, text="not json"),
    ],
)
def test_host_failures_become_embedding_service_errors(monkeypatch, response):
    with pytest.raises(EmbeddingServiceError):
        asyncio.run(embed_query(_embedder(monkeypatch, lambda r: response), "q"))


def test_unreachable_host_becomes_embedding_service_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    with pytest.raises(EmbeddingServiceError, match="ConnectError"):
        asyncio.run(embed_query(_embedder(monkeypatch, handler), "q"))


def test_model_without_an_ollama_tag_is_refused():
    # nomic is in the convention table but this service cannot serve it. The old
    # defaults named nomic while embed_query applied the Qwen convention, so a
    # silent fallback here would be wrong vectors with no error.
    with pytest.raises(ValueError, match="no Ollama tag"):
        QueryEmbedder("http://ollama:11434", "nomic-ai/nomic-embed-text-v1.5")


class _Db:
    def __init__(self, doc):
        self.doc = doc

    async def get_doc(self, bucket, key):
        return self.doc


_MANIFEST = {"model": EMBEDDING_MODEL, "dims": EMBEDDING_DIMS}


def _serve(monkeypatch, handler) -> None:
    """Route get_embedding_model to an embedder talking to `handler`."""
    embedder = _embedder(monkeypatch, handler)
    monkeypatch.setattr(embedding, "get_embedding_model", lambda name=EMBEDDING_MODEL: embedder)


def test_probe_passes_when_the_host_serves_the_model(monkeypatch):
    _serve(monkeypatch, _ok())
    assert asyncio.run(probe_embedder()) is None


def test_probe_reports_an_unreachable_host(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused", request=request)

    _serve(monkeypatch, handler)
    problem = asyncio.run(probe_embedder())
    assert problem and "Could not embed a probe query" in problem


def test_corpus_check_passes_when_the_manifest_agrees():
    assert asyncio.run(assert_corpus_matches(_Db(_MANIFEST))) is None


def test_corpus_check_reports_a_manifest_mismatch():
    stale = {"model": "nomic-ai/nomic-embed-text-v1.5", "dims": 768}
    problem = asyncio.run(assert_corpus_matches(_Db(stale)))
    assert problem and "Embedding mismatch" in problem


class _BrokenDb:
    async def get_doc(self, bucket, key):
        raise TimeoutError("couchbase is still starting")


def test_unreadable_manifest_is_a_problem_not_a_pass():
    # This used to return None, which main.py logged as a checkmark.
    problem = asyncio.run(assert_corpus_matches(_BrokenDb()))
    assert problem and "unverified" in problem


def test_wrong_model_name_reaches_the_klaxon_without_a_database():
    # .env setting nomic is the precedent this check exists for. It used to die
    # earlier, building the embedder, and surface as a mild warning.
    problem = asyncio.run(
        assert_corpus_matches(_BrokenDb(), loaded_model_name="nomic-ai/nomic-embed-text-v1.5")
    )
    assert problem and "EMBEDDING_MODEL_NAME" in problem


def test_wrong_model_name_is_a_service_error_at_request_time(monkeypatch):
    # Otherwise a bare ValueError escapes every route as an opaque 500.
    from types import SimpleNamespace
    import sys

    fake = SimpleNamespace(settings=SimpleNamespace(ollama_host="http://ollama:11434"))
    monkeypatch.setitem(sys.modules, "app.config", fake)
    embedding.get_embedding_model.cache_clear()
    with pytest.raises(EmbeddingServiceError, match="no Ollama tag"):
        embedding.get_embedding_model("nomic-ai/nomic-embed-text-v1.5")
