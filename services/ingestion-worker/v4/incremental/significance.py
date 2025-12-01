"""
Summary significance checker for incremental updates.

Determines if a change warrants propagating summary updates to parent levels.
"""

from typing import Optional

from loguru import logger


class SignificanceChecker:
    """
    Checks if summary changes are significant enough to propagate.

    This prevents minor changes (bugfixes, typos) from unnecessarily
    regenerating module and repo summaries.
    """

    def __init__(self, llm_enricher=None, enabled: bool = True):
        """
        Args:
            llm_enricher: LLM enricher for significance evaluation
            enabled: If False, all changes are considered significant
        """
        self.llm_enricher = llm_enricher
        self.enabled = enabled

    def is_significant(
        self,
        old_summary: str,
        new_summary: str,
        diff_context: str,
        level: str
    ) -> bool:
        """
        Determine if a summary change warrants propagation to parent level.

        Args:
            old_summary: Previous summary text
            new_summary: New summary text
            diff_context: Git diff or change description
            level: "file", "module", or "repo"

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

        # If disabled or no LLM, be conservative and propagate
        if not self.enabled or not self.llm_enricher:
            return True

        return self._evaluate_with_llm(old_summary, new_summary, diff_context, level)

    def _evaluate_with_llm(
        self,
        old_summary: str,
        new_summary: str,
        diff_context: str,
        level: str
    ) -> bool:
        """Use LLM to evaluate if change is significant."""
        prompt = f"""Analyze if this code change warrants updating parent-level documentation.

Level: {level}

Code Change (diff):
```
{diff_context[:800]}
```

Old {level} Summary:
{old_summary[:400]}

New {level} Summary:
{new_summary[:400]}

Question: Should parent-level summaries be updated?

Answer YES if the change involves:
- New functionality or features
- API/interface changes
- Architectural or structural changes
- Significant behavior changes
- New dependencies or integrations

Answer NO if the change is:
- Bug fix with no API change
- Typo or documentation fix
- Code formatting or style
- Internal refactoring (same behavior)
- Performance optimization (same API)

Respond with just YES or NO."""

        try:
            response = self.llm_enricher.generate(prompt)
            is_significant = "YES" in response.upper()

            if not is_significant:
                logger.info(f"  [{level}] Change not significant - stopping propagation")

            return is_significant

        except Exception as e:
            logger.warning(f"Could not determine significance: {e} - propagating to be safe")
            return True  # Be conservative on error
