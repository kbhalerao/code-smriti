"""
Cross-Encoder Reranker for RAG Pipeline

Re-scores retrieved candidates with a cross-encoder that computes joint
query-passage relevance. Replaces the bi-encoder's independent-embedding
cosine similarity for the FINAL ranking step only — bi-encoder still drives
recall during the drilldown.

Applied after the drilldown loop, scored against the ORIGINAL user query
(not the bridge-rewritten one). Bridge rewrite is for recall; original
query determines true relevance.
"""

import asyncio
import math
from threading import Lock

from loguru import logger

from app.rag.models import SearchResult


_reranker_model = None
_reranker_lock = Lock()


def get_reranker_model(model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
    """Lazy singleton load of the CrossEncoder model."""
    global _reranker_model
    if _reranker_model is None:
        with _reranker_lock:
            if _reranker_model is None:
                from sentence_transformers import CrossEncoder
                logger.info(f"Loading reranker: {model_name}")
                _reranker_model = CrossEncoder(model_name, max_length=512)
                logger.info("Reranker loaded")
    return _reranker_model


class CrossEncoderReranker:
    """Reranks SearchResults by cross-encoder relevance against the original query."""

    def __init__(
        self,
        model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2",
        max_candidates: int = 20,
        content_max_chars: int = 1500,
    ):
        self.model_name = model_name
        self.max_candidates = max_candidates
        self.content_max_chars = content_max_chars

    async def rerank(
        self,
        query: str,
        results: list[SearchResult],
    ) -> list[SearchResult]:
        """Reorder results by cross-encoder score against the original query.

        Sets rerank_score (sigmoid-normalized, 0-1) on each scored result.
        Returns results sorted by rerank_score descending. Candidates beyond
        max_candidates are left in original order and appended at the end.
        Falls back to original order on any error.
        """
        if not results:
            return results

        head = results[: self.max_candidates]
        tail = results[self.max_candidates:]

        pairs = [
            (query, (r.content or "")[: self.content_max_chars])
            for r in head
        ]

        try:
            model = get_reranker_model(self.model_name)
            logits = await asyncio.to_thread(
                model.predict, pairs, show_progress_bar=False
            )
            for r, logit in zip(head, logits):
                r.rerank_score = 1.0 / (1.0 + math.exp(-float(logit)))
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
