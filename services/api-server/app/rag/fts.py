"""
One way to ask code_vector_index for filtered nearest neighbours.

The filter goes *inside* the knn clause (``knn[0].filter``), which pre-filters:
candidates are restricted first, then ranked by vector similarity. Measured on
Couchbase 8.0.3 against this index (2026-11-14):

  - type, repo and type+repo filters return only matching documents;
  - scores are the vector similarity (~0.7 for a good hit), not a flat filter
    score, so FTS order is meaningful;
  - a ``disjuncts`` filter over two types returns whichever type ranks higher,
    which is correct ranking, not the old one-clause-only bug;
  - k above 100 still filters correctly.

The previous form, a top-level ``query`` plus ``knn_operator: "and"``, still
post-filters on 8.0.3: it takes a global top-k and intersects it with the
filter, so a selective filter starves and the scores come back flat. On 7.6.2
the knn-clause filter was ignored, which is why every call site used that
form. Do not reintroduce it.
"""

from typing import Any, Optional


def knn_request(
    vector: list[float],
    k: int,
    filter_query: Optional[dict[str, Any]] = None,
    fields: Optional[list[str]] = None,
) -> dict[str, Any]:
    """A Search request for the k nearest documents passing ``filter_query``."""
    knn: dict[str, Any] = {"field": "embedding", "vector": vector, "k": k}
    if filter_query:
        knn["filter"] = filter_query
    body: dict[str, Any] = {"knn": [knn], "size": k}
    if fields:
        body["fields"] = fields
    return body
