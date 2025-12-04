"""
Summary significance checker for incremental updates.

Determines if a change warrants propagating summary updates to parent levels.
Uses embedding-based semantic similarity when available, with text-based
heuristics as fallback.
"""

from typing import List, Optional

from loguru import logger


class SignificanceChecker:
    """
    Checks if summary changes are significant enough to propagate.

    This prevents minor changes (bugfixes, typos) from unnecessarily
    regenerating module and repo summaries.

    Uses embedding cosine similarity for semantic comparison when available,
    falling back to text similarity and keyword heuristics.
    """

    def __init__(self, embedding_generator=None, enabled: bool = True):
        """
        Args:
            embedding_generator: LocalEmbeddingGenerator for semantic similarity
            enabled: If False, all changes are considered significant
        """
        self.embedding_generator = embedding_generator
        self.enabled = enabled

    def is_significant(
        self,
        old_summary: str,
        new_summary: str,
        diff_context: str,
        level: str,
        old_embedding: Optional[List[float]] = None
    ) -> bool:
        """
        Determine if a summary change warrants propagation to parent level.

        Args:
            old_summary: Previous summary text
            new_summary: New summary text
            diff_context: Git diff or change description
            level: "file", "module", or "repo"
            old_embedding: Previous embedding vector (optional, for semantic similarity)

        Returns:
            True if parent summaries should be regenerated
        """
        # If no old summary, it's new - always propagate
        if not old_summary:
            return True

        # If summaries are identical, no propagation needed
        if old_summary.strip() == new_summary.strip():
            logger.debug(f"  [{level}] Summary unchanged - skipping propagation")
            return False

        # If disabled, be conservative and propagate
        if not self.enabled:
            return True

        # Try embedding-based similarity first (most accurate)
        if old_embedding and self.embedding_generator and new_summary:
            embedding_result = self._evaluate_with_embeddings(
                old_embedding, new_summary, level
            )
            if embedding_result is not None:
                return embedding_result

        # Fall back to text-based heuristics
        return self._evaluate_with_heuristics(old_summary, new_summary, diff_context, level)

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        import math
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        norm1 = math.sqrt(sum(a * a for a in vec1))
        norm2 = math.sqrt(sum(b * b for b in vec2))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot_product / (norm1 * norm2)

    def _evaluate_with_embeddings(
        self,
        old_embedding: List[float],
        new_summary: str,
        level: str
    ) -> Optional[bool]:
        """
        Evaluate significance using embedding-based semantic similarity.

        Args:
            old_embedding: Previous file's embedding vector
            new_summary: New summary text to compare

        Returns:
            True if significant, False if minor, None if unable to determine
        """
        try:
            # Generate embedding for new summary
            new_embedding = self.embedding_generator.generate_embedding(new_summary)

            if not new_embedding:
                return None

            # Calculate cosine similarity
            similarity = self._cosine_similarity(old_embedding, new_embedding)

            # High similarity (>0.95) = minor change, skip propagation
            if similarity > 0.95:
                logger.info(f"  [{level}] Embedding similarity {similarity:.3f} > 0.95 - minor change")
                return False

            # Low similarity (<0.80) = significant change, propagate
            if similarity < 0.80:
                logger.info(f"  [{level}] Embedding similarity {similarity:.3f} < 0.80 - significant change")
                return True

            # Medium similarity (0.80-0.95) = uncertain, fall back to heuristics
            logger.debug(f"  [{level}] Embedding similarity {similarity:.3f} - checking heuristics")
            return None

        except Exception as e:
            logger.warning(f"  [{level}] Embedding comparison failed: {e}")
            return None

    def _evaluate_with_heuristics(
        self,
        old_summary: str,
        new_summary: str,
        diff_context: str,
        level: str
    ) -> bool:
        """
        Evaluate if change is significant using text-based heuristics.

        Uses text similarity and keyword detection as fallback
        when embeddings are not available.
        """
        # Heuristic 1: Check similarity ratio
        from difflib import SequenceMatcher
        similarity = SequenceMatcher(None, old_summary.lower(), new_summary.lower()).ratio()

        # If summaries are very similar (>90%), likely minor change
        if similarity > 0.90:
            logger.info(f"  [{level}] Summaries 90%+ similar - minor change")
            return False

        # Heuristic 2: Check diff for significant keywords
        significant_keywords = [
            'new feature', 'added', 'implements', 'creates',
            'api', 'interface', 'breaking', 'refactor',
            'architecture', 'dependency', 'integration'
        ]
        minor_keywords = [
            'fix', 'typo', 'comment', 'format', 'style',
            'cleanup', 'lint', 'whitespace', 'minor'
        ]

        diff_lower = diff_context.lower()
        summary_diff = (new_summary.lower().replace(old_summary.lower(), '')).strip()
        combined = diff_lower + ' ' + summary_diff

        has_significant = any(kw in combined for kw in significant_keywords)
        has_minor = any(kw in combined for kw in minor_keywords)

        # If only minor keywords and no significant ones, it's minor
        if has_minor and not has_significant:
            logger.info(f"  [{level}] Detected minor change keywords - stopping propagation")
            return False

        # If significant keywords found, propagate
        if has_significant:
            logger.debug(f"  [{level}] Detected significant keywords - propagating")
            return True

        # If summaries differ significantly (<70% similar), propagate
        if similarity < 0.70:
            logger.debug(f"  [{level}] Summaries differ significantly ({similarity:.0%}) - propagating")
            return True

        # Default: be conservative for moderate changes
        logger.debug(f"  [{level}] Moderate change ({similarity:.0%} similar) - propagating")
        return True
