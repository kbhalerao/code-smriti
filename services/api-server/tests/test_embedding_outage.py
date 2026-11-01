"""
An embedding outage must look like an outage.

The embedder moved from in-process to a network call, so it can now fail while
everything else is healthy. search_code's blanket handler used to turn that into
`[]`, which reads as "nothing is indexed", and the routes' handlers put the
exception text, with the internal host and model tag, into the response.
"""

import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import embedding_unavailable_handler
from app.rag import tools
from app.rag.embedding import EMBEDDING_UNAVAILABLE_DETAIL, EmbeddingServiceError


def _down(*args, **kwargs):
    raise EmbeddingServiceError("request to http://internal:11434 (some-tag) failed")


def test_search_code_propagates_an_outage_rather_than_returning_nothing(monkeypatch):
    async def embed(*args, **kwargs):
        _down()

    monkeypatch.setattr(tools, "embed_query", embed)
    monkeypatch.setenv("COUCHBASE_PASSWORD", "unused")

    with pytest.raises(EmbeddingServiceError):
        asyncio.run(tools.search_code(db=None, embedding_model=None, query="q"))


def test_outage_is_a_503_that_does_not_name_the_internal_host():
    app = FastAPI()
    app.add_exception_handler(EmbeddingServiceError, embedding_unavailable_handler)

    @app.get("/search")
    async def search():
        _down()

    response = TestClient(app).get("/search")

    assert response.status_code == 503
    assert response.json() == {"detail": EMBEDDING_UNAVAILABLE_DETAIL}
    assert "internal" not in response.text
