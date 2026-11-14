"""
Filtered vector search must pre-filter.

On Couchbase 8.0.3 a filter inside the knn clause restricts candidates before
ranking; a top-level query plus knn_operator "and" still post-filters a global
top-k, which starves selective filters and returns flat scores. Every call site
builds its request through app.rag.fts.knn_request, so pinning its shape pins
all of them.
"""

from app.rag.fts import knn_request


def test_filter_sits_inside_the_knn_clause():
    flt = {"term": "document", "field": "type"}
    body = knn_request([0.1, 0.2], 25, flt, ["type"])
    assert body["knn"] == [{"field": "embedding", "vector": [0.1, 0.2], "k": 25, "filter": flt}]
    assert body["size"] == 25 and body["fields"] == ["type"]


def test_the_post_filtering_form_is_never_built():
    body = knn_request([0.1], 5, {"term": "spec", "field": "type"})
    assert "knn_operator" not in body
    assert "query" not in body


def test_no_filter_means_a_plain_knn():
    body = knn_request([0.1], 5)
    assert "filter" not in body["knn"][0] and "fields" not in body
