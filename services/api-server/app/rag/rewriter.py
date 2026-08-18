"""
Query Rewriter for RAG Pipeline

Translates the user's query into the technical vocabulary of matched modules
so subsequent chunk-level retrieval can use domain-specific terminology that
better matches embedded code.

Called by the orchestrator between the MODULE pass (which surfaces likely-
relevant module summaries) and the FILE/SYMBOL passes.
"""

import os
from typing import Optional

import httpx
from loguru import logger

from app.rag.llm_extras import llm_request_extras


REWRITE_PROMPT = """You translate questions into the technical vocabulary used by a codebase.

ORIGINAL QUESTION:
{query}

TECHNICAL CONTEXT (excerpts from module summaries of likely-relevant code):
{vocabulary}

Rewrite the original question so it uses terminology that appears in the technical context. The rewritten question will be embedded and used to search for source code, so prefer concrete names (function names, class names, technical concepts) over business phrasing.

Rules:
- Do NOT add requirements, constraints, or assumptions that aren't in the original question
- Do NOT mention specific module paths or file names
- Keep the rewrite under 30 words
- Return ONLY the rewritten question, no preamble, no quotes, no explanation
"""


class QueryRewriter:
    """Rewrites a query using technical vocabulary from matched modules."""

    def __init__(
        self,
        ollama_host: Optional[str] = None,
        model: str = "general",
        timeout: float = 15.0,
        max_vocabulary_chars: int = 2000,
    ):
        self.ollama_host = ollama_host or os.getenv(
            "OLLAMA_HOST", "http://localhost:11434"
        )
        self.model = model
        self.timeout = timeout
        self.max_vocabulary_chars = max_vocabulary_chars

    async def rewrite(self, query: str, vocabulary: list[str]) -> str:
        """
        Return a rewritten query, or the original on any failure.

        Args:
            query: Original user question
            vocabulary: Technical excerpts (typically module summary contents)
        """
        if not vocabulary:
            return query

        # Cap total vocabulary size to keep the prompt cheap and predictable.
        joined = ""
        for i, v in enumerate(vocabulary, 1):
            chunk = f"[{i}] {v.strip()}\n"
            if len(joined) + len(chunk) > self.max_vocabulary_chars:
                break
            joined += chunk

        if not joined.strip():
            return query

        prompt = REWRITE_PROMPT.format(query=query, vocabulary=joined)

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.post(
                    f"{self.ollama_host}/v1/chat/completions",
                    json={
                        "model": self.model,
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    "Return only the rewritten question. "
                                    "No preamble, no reasoning, no explanation."
                                ),
                            },
                            {"role": "user", "content": prompt},
                        ],
                        "temperature": 0.1,
                        "max_tokens": 100,
                        **llm_request_extras(),
                    },
                )
                response.raise_for_status()

                rewritten = response.json()["choices"][0]["message"]["content"].strip()

                # Strip common LLM-isms that sneak past the "no preamble" instruction.
                rewritten = rewritten.strip('"\'')
                lower = rewritten.lower()
                for prefix in ("rewritten:", "rewritten question:", "question:"):
                    if lower.startswith(prefix):
                        rewritten = rewritten[len(prefix):].strip()
                        break

                if not rewritten:
                    logger.warning("Rewriter returned empty string, using original")
                    return query

                logger.info(
                    f"Rewrote query: '{query[:60]}' -> '{rewritten[:60]}'"
                )
                return rewritten

        except Exception as e:
            logger.warning(f"Query rewrite failed ({e}), using original")
            return query
