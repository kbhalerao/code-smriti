"""
Cross-Encoder Reranker for RAG Pipeline

Re-scores retrieved candidates with a cross-encoder that computes joint
query-passage relevance. Replaces the bi-encoder's independent-embedding
cosine similarity for the FINAL ranking step only — bi-encoder still drives
recall during the drilldown.

Applied after the drilldown loop, scored against the ORIGINAL user query
(not the bridge-rewritten one). Bridge rewrite is for recall; original
query determines true relevance.

Served over HTTP, not loaded in-process. The model is Qwen3-Reranker-0.6B on
llama-server (`--reranking`), matching the Qwen3-Embedding-0.6B bi-encoder that
drives recall. Two reasons it lives behind a socket rather than in this process:

1. Ollama cannot serve it. It has no rerank endpoint (`/api/rerank` 404s), and
   this model has no LM head to fake one with — the GGUF is converted with
   `pooling_type=RANK` and a 2-way `cls.output.weight` classifier, so
   `/api/generate` is refused outright and `/api/embed` returns an
   uninitialized buffer. llama.cpp's rerank endpoint is the only thing that
   reads that head.
2. Scoring is batched. One HTTP call carries every candidate and llama.cpp
   scores them in a single pass, so candidate count costs prefill, not
   round-trips.

Scores arrive already normalised to 0-1. The classifier's two labels are
['yes','no'] and llama.cpp returns softmax P(yes), so do NOT apply a sigmoid on
top — that was correct for the previous in-process ms-marco cross-encoder,
which emitted raw logits, and here would squash everything into a narrow band
above 0.5.

Calibration note for anyone adding a cutoff: the score distribution is bimodal,
not linear. Confident judgements pin to the rails — a clear hit reads ~0.9998
and an obviously unrelated document reads exactly 0.0000 — while genuinely
ambiguous candidates do occupy the middle (0.07-0.15 measured on near-miss code
chunks). So ordering is reliable across the whole range, but the gap between
"good" and "bad" is not a fixed distance: most of the mass sits at the ends, and
a cutoff anywhere in the middle separates very few documents from each other.
Derive one from held-out queries rather than picking a round number.
"""

import os
from typing import Optional

import httpx
from loguru import logger

from app.rag.models import SearchResult


DEFAULT_RERANK_URL = "http://host.docker.internal:11435/rerank"


class CrossEncoderReranker:
    """Reranks SearchResults by cross-encoder relevance against the original query."""

    def __init__(
        self,
        rerank_url: Optional[str] = None,
        max_candidates: int = 20,
        content_max_chars: int = 1500,
        timeout: float = 30.0,
    ):
        self.rerank_url = rerank_url or os.getenv("RERANK_URL", DEFAULT_RERANK_URL)
        self.max_candidates = max_candidates
        self.content_max_chars = content_max_chars
        self.timeout = timeout

    async def rerank(
        self,
        query: str,
        results: list[SearchResult],
    ) -> list[SearchResult]:
        """Reorder results by cross-encoder score against the original query.

        Sets rerank_score (0-1) on each scored result. Returns results sorted by
        rerank_score descending. Candidates beyond max_candidates are left in
        original order and appended at the end. Falls back to original order on
        any error — a reranker outage degrades ranking, it does not fail search.
        """
        if not results:
            return results

        head = results[: self.max_candidates]
        tail = results[self.max_candidates:]

        documents = [(r.content or "")[: self.content_max_chars] for r in head]

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    self.rerank_url,
                    json={
                        "query": query,
                        "documents": documents,
                        "top_n": len(documents),
                    },
                    timeout=self.timeout,
                )
            resp.raise_for_status()
            # Results come back reordered, so index is what maps a score home.
            for scored in resp.json()["results"]:
                head[scored["index"]].rerank_score = float(scored["relevance_score"])
        except Exception as e:
            logger.warning(f"Rerank failed: {e}, falling back to original order")
            return results

        head.sort(key=lambda r: r.rerank_score or 0.0, reverse=True)
        logger.info(
            f"Reranked {len(head)} candidates "
            f"(top score: {head[0].rerank_score:.3f}, "
            f"bottom: {head[-1].rerank_score:.3f})"
        )
        return head + tail


def get_reranker() -> Optional[CrossEncoderReranker]:
    """Reranker for this deployment, or None when RAG_RERANK_ENABLED=0.

    Cheap to construct — it holds no weights, only a URL — so callers build one
    per request rather than sharing a singleton.
    """
    if os.getenv("RAG_RERANK_ENABLED", "1") == "0":
        return None
    return CrossEncoderReranker()
