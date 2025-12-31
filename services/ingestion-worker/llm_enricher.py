#!/usr/bin/env python3
"""
LLM-Assisted Chunk Enrichment

Uses local LLMs (Qwen3-3B, minimax-m2) to generate semantic summaries
and improve chunk quality.

Supports:
- Ollama API (http://localhost:11434)
- LM Studio API (http://localhost:1234)
"""

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Optional, List, Dict, Any
from pathlib import Path

import httpx
from loguru import logger

from config import WorkerConfig

config = WorkerConfig()


class LLMUnavailableError(Exception):
    """Raised when LLM is unavailable (circuit breaker open or persistent failures)"""
    pass


@dataclass
class EnrichmentResult:
    """Result from LLM enrichment"""
    summary: str
    purpose: str
    key_symbols: List[Dict[str, str]]
    usage_pattern: Optional[str]
    integrations: List[str]
    quality_notes: Optional[str]
    raw_response: str


@dataclass
class LLMConfig:
    """Configuration for LLM provider"""
    provider: str  # "ollama" or "lmstudio"
    model: str
    base_url: str
    temperature: float = 0.3
    max_tokens: int = 2000
    timeout_seconds: float = 60.0  # Per-request timeout
    max_retries: int = 2  # Retries on failure


# Default configurations
# Local LLM configurations
OLLAMA_CONFIG = LLMConfig(
    provider="ollama",
    model="qwen3:3b",
    base_url="http://localhost:11434",
    temperature=0.3
)

# MacStudio LM Studio endpoint
LMSTUDIO_CONFIG = LLMConfig(
    provider="lmstudio",
    model="qwen/qwen3-30b-a3b-2507",  # Model loaded in LM Studio
    base_url="http://macstudio.local:1234",
    temperature=0.3
)

# Default to MacStudio
DEFAULT_CONFIG = LMSTUDIO_CONFIG


class LLMEnricher:
    """Enriches code chunks using local LLMs"""

    def __init__(self, config: LLMConfig = OLLAMA_CONFIG):
        self.config = config
        self._client = None  # Lazy init to avoid event loop issues
        self._client_loop = None  # Track which loop the client was created on
        self._consecutive_failures = 0
        self._max_consecutive_failures = 5  # Circuit breaker threshold
        logger.info(f"LLM Enricher initialized: {config.provider}/{config.model} (timeout={config.timeout_seconds}s)")

    @property
    def client(self):
        """Get httpx client, creating fresh one if needed for current event loop."""
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            current_loop = None

        # Check if we need a new client:
        # 1. No client exists
        # 2. Loop changed
        # 3. Previous loop was closed
        needs_new_client = (
            self._client is None or
            self._client_loop is not current_loop or
            (self._client_loop is not None and self._client_loop.is_closed())
        )

        if needs_new_client:
            # Discard old client (don't await close - it's bound to closed loop)
            self._client = None
            self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds)
            self._client_loop = current_loop

        return self._client

    async def _call_ollama(self, prompt: str) -> str:
        """Call Ollama API"""
        response = await self.client.post(
            f"{self.config.base_url}/api/generate",
            json={
                "model": self.config.model,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": self.config.temperature,
                    "num_predict": self.config.max_tokens
                }
            }
        )
        response.raise_for_status()
        return response.json()["response"]

    async def _call_lmstudio(self, prompt: str) -> str:
        """Call LM Studio using /v1/responses API (better performance with thinking models)"""
        response = await self.client.post(
            f"{self.config.base_url}/v1/responses",
            json={
                "model": self.config.model,
                "input": prompt,
                "temperature": self.config.temperature,
                "max_output_tokens": self.config.max_tokens
            }
        )
        response.raise_for_status()
        data = response.json()
        # Extract text from the responses API format
        # Response structure: {"output": [{"type": "message", "content": [{"type": "output_text", "text": "..."}]}]}
        output = data.get("output", [])
        for item in output:
            if item.get("type") == "message":
                content = item.get("content", [])
                for block in content:
                    if block.get("type") == "output_text":
                        return block.get("text", "")
        # Fallback: try to get text directly if format differs
        if "text" in data:
            return data["text"]
        raise ValueError(f"Could not extract text from responses API: {data}")

    async def _call_lmstudio_with_reasoning(self, prompt: str) -> dict:
        """
        Call LM Studio and return both reasoning trace and output.

        Returns:
            dict with 'reasoning' (str or None) and 'output' (str) keys
        """
        response = await self.client.post(
            f"{self.config.base_url}/v1/responses",
            json={
                "model": self.config.model,
                "input": prompt,
                "temperature": self.config.temperature,
                "max_output_tokens": self.config.max_tokens
            }
        )
        response.raise_for_status()
        data = response.json()

        reasoning_text = None
        output_text = None

        for item in data.get("output", []):
            if item.get("type") == "reasoning":
                # Extract reasoning trace
                for block in item.get("content", []):
                    if block.get("type") == "reasoning_text":
                        reasoning_text = block.get("text", "")
                        break
            elif item.get("type") == "message":
                # Extract final output
                for block in item.get("content", []):
                    if block.get("type") == "output_text":
                        output_text = block.get("text", "")
                        break

        if output_text is None:
            raise ValueError(f"Could not extract output from responses API: {data}")

        return {
            "reasoning": reasoning_text,
            "output": output_text,
            "usage": data.get("usage", {})
        }

    async def generate(self, prompt: str) -> str:
        """
        Generate text using configured LLM with retry logic and circuit breaker.

        Raises:
            LLMUnavailableError: If LLM is unavailable (circuit breaker open)
            Exception: On persistent failure after retries
        """
        # Circuit breaker check
        if self._consecutive_failures >= self._max_consecutive_failures:
            logger.warning(f"LLM circuit breaker OPEN ({self._consecutive_failures} consecutive failures)")
            raise LLMUnavailableError("LLM unavailable - circuit breaker open")

        last_error = None

        for attempt in range(self.config.max_retries + 1):
            try:
                if self.config.provider == "ollama":
                    result = await self._call_ollama(prompt)
                elif self.config.provider == "lmstudio":
                    result = await self._call_lmstudio(prompt)
                else:
                    raise ValueError(f"Unknown provider: {self.config.provider}")

                # Success - reset failure counter
                self._consecutive_failures = 0
                return result

            except httpx.TimeoutException as e:
                last_error = e
                self._consecutive_failures += 1
                logger.warning(f"LLM timeout (attempt {attempt + 1}/{self.config.max_retries + 1}): {e}")
                if attempt < self.config.max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))  # Backoff

            except httpx.HTTPStatusError as e:
                last_error = e
                self._consecutive_failures += 1
                logger.warning(f"LLM HTTP error {e.response.status_code} (attempt {attempt + 1}): {e}")
                if e.response.status_code >= 500 and attempt < self.config.max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    break  # Client error, don't retry

            except Exception as e:
                last_error = e
                self._consecutive_failures += 1
                logger.error(f"LLM call failed (attempt {attempt + 1}): {e}")
                if attempt < self.config.max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))

        raise last_error or Exception("LLM call failed")

    async def generate_with_reasoning(self, prompt: str) -> dict:
        """
        Generate text with reasoning trace (for thinking models like Nemotron).

        Only works with lmstudio provider using /v1/responses API.

        Returns:
            dict with 'reasoning' (str or None), 'output' (str), 'usage' (dict)
        """
        if self.config.provider != "lmstudio":
            # Fallback for non-lmstudio providers
            output = await self.generate(prompt)
            return {"reasoning": None, "output": output, "usage": {}}

        # Circuit breaker check
        if self._consecutive_failures >= self._max_consecutive_failures:
            raise LLMUnavailableError("LLM unavailable - circuit breaker open")

        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                result = await self._call_lmstudio_with_reasoning(prompt)
                self._consecutive_failures = 0
                return result

            except httpx.HTTPStatusError as e:
                last_error = e
                self._consecutive_failures += 1
                logger.warning(f"LLM HTTP error {e.response.status_code} (attempt {attempt + 1}): {e}")
                if e.response.status_code >= 500:
                    if attempt < self.config.max_retries:
                        await asyncio.sleep(1.0 * (attempt + 1))
                else:
                    break

            except Exception as e:
                last_error = e
                self._consecutive_failures += 1
                logger.error(f"LLM call failed (attempt {attempt + 1}): {e}")
                if attempt < self.config.max_retries:
                    await asyncio.sleep(1.0 * (attempt + 1))

        raise last_error or Exception("LLM call failed")

    async def enrich_file(
        self,
        file_path: str,
        content: str,
        language: str = "python"
    ) -> EnrichmentResult:
        """
        Generate enrichment for a file chunk

        Args:
            file_path: Path to the file
            content: File content (may be truncated)
            language: Programming language

        Returns:
            EnrichmentResult with summary, purpose, etc.
        """
        # Truncate content if too long for context window
        max_content = 6000
        truncated = content[:max_content] if len(content) > max_content else content
        was_truncated = len(content) > max_content

        prompt = f"""Analyze this {language} file and provide a structured summary.

File: {file_path}
{"(Content truncated to first 6000 chars)" if was_truncated else ""}

```{language}
{truncated}
```

Provide your analysis in this exact JSON format (no markdown, just JSON):
{{
    "summary": "2-3 sentence description of what this file does",
    "purpose": "Why does this file exist? What problem does it solve?",
    "key_symbols": [
        {{"name": "SymbolName", "type": "class|function|constant", "purpose": "What it does"}}
    ],
    "usage_pattern": "How would a developer typically use this file/module?",
    "integrations": ["list", "of", "modules", "this", "connects", "to"],
    "quality_notes": "Any issues noticed (missing docstrings, complexity, etc.) or null"
}}

Respond with only valid JSON, no explanation."""

        try:
            response = await self.generate(prompt)

            # Try to extract JSON from response
            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                return EnrichmentResult(
                    summary=data.get("summary", ""),
                    purpose=data.get("purpose", ""),
                    key_symbols=data.get("key_symbols", []),
                    usage_pattern=data.get("usage_pattern"),
                    integrations=data.get("integrations", []),
                    quality_notes=data.get("quality_notes"),
                    raw_response=response
                )
            else:
                logger.warning(f"Could not parse JSON from LLM response for {file_path}")
                return EnrichmentResult(
                    summary=response[:500],  # Use raw response as summary
                    purpose="",
                    key_symbols=[],
                    usage_pattern=None,
                    integrations=[],
                    quality_notes="LLM response parsing failed",
                    raw_response=response
                )

        except Exception as e:
            logger.error(f"Enrichment failed for {file_path}: {e}")
            raise

    async def enrich_symbol(
        self,
        symbol_name: str,
        symbol_type: str,
        code: str,
        file_path: str,
        language: str = "python"
    ) -> EnrichmentResult:
        """
        Generate enrichment for a class/function

        Args:
            symbol_name: Name of the class/function
            symbol_type: "class", "function", "method"
            code: Symbol's code
            file_path: Path to containing file
            language: Programming language

        Returns:
            EnrichmentResult with summary and usage info
        """
        max_code = 4000
        truncated = code[:max_code] if len(code) > max_code else code

        prompt = f"""Analyze this {language} {symbol_type} and explain how to use it.

File: {file_path}
{symbol_type.capitalize()}: {symbol_name}

```{language}
{truncated}
```

Provide your analysis in this exact JSON format:
{{
    "summary": "1-2 sentence description of what this {symbol_type} does",
    "purpose": "When would a developer use this?",
    "key_symbols": [
        {{"name": "method_name", "type": "method", "purpose": "What it does"}}
    ],
    "usage_pattern": "Example of how to use this (code snippet or description)",
    "integrations": ["what", "this", "connects", "to"],
    "quality_notes": "Any issues or null"
}}

Respond with only valid JSON."""

        try:
            response = await self.generate(prompt)

            json_match = re.search(r'\{[\s\S]*\}', response)
            if json_match:
                data = json.loads(json_match.group())
                return EnrichmentResult(
                    summary=data.get("summary", ""),
                    purpose=data.get("purpose", ""),
                    key_symbols=data.get("key_symbols", []),
                    usage_pattern=data.get("usage_pattern"),
                    integrations=data.get("integrations", []),
                    quality_notes=data.get("quality_notes"),
                    raw_response=response
                )
            else:
                return EnrichmentResult(
                    summary=response[:300],
                    purpose="",
                    key_symbols=[],
                    usage_pattern=None,
                    integrations=[],
                    quality_notes="Parsing failed",
                    raw_response=response
                )

        except Exception as e:
            logger.error(f"Symbol enrichment failed for {symbol_name}: {e}")
            raise

    async def generate_repo_summary(
        self,
        repo_id: str,
        file_list: List[str],
        sample_files: Dict[str, str]
    ) -> str:
        """
        Generate a repository-level summary

        Args:
            repo_id: Repository identifier
            file_list: List of all file paths in repo
            sample_files: Dict of file_path -> content for key files

        Returns:
            Markdown summary of the repository
        """
        # Identify key directories
        dirs = set()
        for f in file_list:
            parts = Path(f).parts
            if len(parts) > 1:
                dirs.add(parts[0])

        # Build prompt with sample files
        samples_text = ""
        for path, content in list(sample_files.items())[:5]:
            samples_text += f"\n### {path}\n```\n{content[:1000]}\n```\n"

        prompt = f"""Analyze this code repository and create a comprehensive summary.

Repository: {repo_id}
Total files: {len(file_list)}
Top-level directories: {', '.join(sorted(dirs)[:10])}

Sample files:
{samples_text}

Create a markdown summary with these sections:
1. **Overview**: What is this project? What does it do?
2. **Tech Stack**: Languages, frameworks, databases
3. **Architecture**: How is it organized?
4. **Key Modules**: Most important directories/packages
5. **Getting Started**: How would a new developer begin?

Write clear, concise documentation (300-500 words)."""

        return await self.generate(prompt)

    async def generate_module_summary(
        self,
        module_path: str,
        files: List[str],
        key_file_contents: Dict[str, str]
    ) -> str:
        """
        Generate a module-level summary (Django app, Python package, etc.)

        Args:
            module_path: Path to the module directory
            files: List of files in this module
            key_file_contents: Content of key files (models.py, views.py, etc.)

        Returns:
            Markdown summary of the module
        """
        # Build content sample
        content_sample = ""
        for path, content in key_file_contents.items():
            content_sample += f"\n### {path}\n```\n{content[:1500]}\n```\n"

        prompt = f"""Analyze this code module/package and create a summary.

Module: {module_path}
Files: {', '.join(files[:20])}

Key file contents:
{content_sample}

Create a markdown summary with:
1. **Purpose**: What does this module do?
2. **Key Components**: Main classes, functions, models
3. **Dependencies**: What it imports/requires
4. **Usage**: How other code uses this module

Write clear documentation (150-300 words)."""

        return await self.generate(prompt)

    async def close(self):
        """Close HTTP client"""
        if self._client is not None:
            await self._client.aclose()
            self._client = None


async def test_enricher():
    """Test the LLM enricher"""
    enricher = LLMEnricher(LMSTUDIO_CONFIG)

    test_code = '''
class FilteredQuerySetMixin(UserPrivilegeResolution):
    """
    Mixin for filtering querysets by organization.
    Add to any ListView or ListAPIView.
    """

    def get_queryset(self):
        qs = super().get_queryset()
        org = self.get_organization()
        if org and hasattr(qs.model, 'organization'):
            return qs.filter(organization=org)
        return qs

    def get_organization(self):
        if hasattr(self.request, 'organization'):
            return self.request.organization
        return None
'''

    try:
        result = await enricher.enrich_symbol(
            symbol_name="FilteredQuerySetMixin",
            symbol_type="class",
            code=test_code,
            file_path="associates/role_privileges.py"
        )

        print("=== LLM Enrichment Result ===")
        print(f"Summary: {result.summary}")
        print(f"Purpose: {result.purpose}")
        print(f"Usage: {result.usage_pattern}")
        print(f"Key symbols: {result.key_symbols}")
        print(f"Integrations: {result.integrations}")

    finally:
        await enricher.close()


if __name__ == "__main__":
    asyncio.run(test_enricher())
