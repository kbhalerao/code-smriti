"""
V4 LLM Enricher

Wrapper around the base LLMEnricher with V4-specific methods:
- enrich_symbol: Returns dict with summary and tokens
- enrich_file: Returns dict with summary and tokens
- enrich_module: Aggregates file summaries into module summary
- enrich_repo: Aggregates module summaries into repo summary
"""

import json
import re
from typing import Dict, List, Optional

from loguru import logger

# Import base enricher
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from llm_enricher import LLMEnricher as BaseLLMEnricher, LLMConfig, LMSTUDIO_CONFIG


class V4LLMEnricher:
    """
    V4-specific LLM enricher.

    Wraps the base LLMEnricher with methods that return simple dicts
    compatible with V4 pipeline expectations.
    """

    def __init__(self, config: LLMConfig = LMSTUDIO_CONFIG):
        self.base_enricher = BaseLLMEnricher(config)
        self._token_estimate_ratio = 0.25  # ~4 chars per token

    def _estimate_tokens(self, text: str) -> int:
        """Estimate token count from text length."""
        return int(len(text) * self._token_estimate_ratio)

    async def enrich_symbol(
        self,
        symbol_name: str,
        symbol_type: str,
        code: str,
        file_path: str,
        language: str = "python"
    ) -> Dict:
        """
        Generate summary for a symbol (function, class, method).

        Args:
            symbol_name: Name of the symbol
            symbol_type: Type (function, class, method)
            code: Symbol's source code
            file_path: Path to containing file
            language: Programming language

        Returns:
            Dict with 'summary' and 'tokens' keys
        """
        prompt = f"""Analyze this {language} {symbol_type} and provide a concise summary.

File: {file_path}
{symbol_type.capitalize()}: {symbol_name}

```{language}
{code[:4000]}
```

Write a 1-3 sentence summary explaining:
1. What this {symbol_type} does
2. Key parameters/arguments (if function/method)
3. What it returns or its side effects

Be concise and focus on practical usage. Do not repeat the code."""

        try:
            response = await self.base_enricher.generate(prompt)
            tokens = self._estimate_tokens(prompt + response)
            return {
                "summary": response.strip(),
                "tokens": tokens
            }
        except Exception as e:
            logger.debug(f"LLM symbol enrichment failed: {e}")
            raise

    async def enrich_file(
        self,
        file_path: str,
        content: str,
        language: str,
        symbols_context: str = ""
    ) -> Dict:
        """
        Generate summary for a file.

        Args:
            file_path: Path to the file
            content: File content (preview)
            language: Programming language
            symbols_context: Pre-generated symbol summaries

        Returns:
            Dict with 'summary' and 'tokens' keys
        """
        # Build prompt based on available context
        if symbols_context:
            prompt = f"""Summarize this {language} file based on its symbols and content.

File: {file_path}

Symbol summaries:
{symbols_context[:3000]}

File preview:
```{language}
{content[:3000]}
```

Write a 2-4 sentence summary explaining:
1. The file's primary purpose
2. Key classes/functions it provides
3. How it fits in the codebase

Be concise and practical."""
        else:
            prompt = f"""Summarize this {language} file.

File: {file_path}

```{language}
{content[:5000]}
```

Write a 2-4 sentence summary explaining:
1. The file's primary purpose
2. Key classes/functions it provides
3. How it would be used

Be concise and practical."""

        try:
            response = await self.base_enricher.generate(prompt)
            tokens = self._estimate_tokens(prompt + response)
            return {
                "summary": response.strip(),
                "tokens": tokens
            }
        except Exception as e:
            logger.debug(f"LLM file enrichment failed: {e}")
            raise

    async def enrich_module(
        self,
        module_path: str,
        files_context: str,
        repo_id: str
    ) -> Dict:
        """
        Generate summary for a module (folder).

        Args:
            module_path: Path to the module folder
            files_context: Concatenated file summaries
            repo_id: Repository identifier

        Returns:
            Dict with 'summary' and 'tokens' keys
        """
        prompt = f"""Summarize this code module based on its files.

Repository: {repo_id}
Module: {module_path}/

File summaries:
{files_context[:6000]}

Write a 2-4 sentence summary explaining:
1. What this module/package does
2. Its key components
3. How other code would use it

Be concise. Focus on the module's role in the codebase."""

        try:
            response = await self.base_enricher.generate(prompt)
            tokens = self._estimate_tokens(prompt + response)
            return {
                "summary": response.strip(),
                "tokens": tokens
            }
        except Exception as e:
            logger.debug(f"LLM module enrichment failed: {e}")
            raise

    async def enrich_repo(
        self,
        repo_id: str,
        modules_context: str
    ) -> Dict:
        """
        Generate summary for a repository.

        Args:
            repo_id: Repository identifier
            modules_context: Concatenated module summaries

        Returns:
            Dict with 'summary' and 'tokens' keys
        """
        prompt = f"""Summarize this code repository based on its modules.

Repository: {repo_id}

Module summaries:
{modules_context[:8000]}

Write a comprehensive but concise summary (3-5 sentences) explaining:
1. What this project is and what it does
2. Key technologies/frameworks used
3. Main components/modules
4. How the modules work together

This summary will help developers quickly understand the repository."""

        try:
            response = await self.base_enricher.generate(prompt)
            tokens = self._estimate_tokens(prompt + response)
            return {
                "summary": response.strip(),
                "tokens": tokens
            }
        except Exception as e:
            logger.debug(f"LLM repo enrichment failed: {e}")
            raise

    async def close(self):
        """Close the underlying HTTP client."""
        await self.base_enricher.close()


# Convenience function for creating enricher
def create_v4_enricher(config: LLMConfig = LMSTUDIO_CONFIG) -> V4LLMEnricher:
    """Create a V4 LLM enricher with the specified config."""
    return V4LLMEnricher(config)
