#!/usr/bin/env python3
"""
LLM-Assisted Chunk Enrichment

Generates semantic summaries and improves chunk quality using a local LLM.

Provider-agnostic: talks to any OpenAI-compatible server exposing the
/v1/responses endpoint (ollama, LM Studio, vLLM, ...). The endpoint, model,
and reasoning effort are all configured via env (LLM_BASE_URL / LLM_MODEL /
LLM_PROVIDER / LLM_REASONING_EFFORT); see LLM_CONFIG below.
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
from llm_gate import gate_for

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
    provider: str  # informational label for the serving backend (e.g. "ollama")
    model: str
    base_url: str
    temperature: float = 0.3
    max_tokens: int = config.llm_max_output_tokens
    # Per-request timeout. Must cover server-side queue time as well as
    # generation — see config.llm_timeout_seconds for why 60s was too tight.
    timeout_seconds: float = config.llm_timeout_seconds
    max_retries: int = 2  # Retries on failure
    # /v1/responses "reasoning.effort" — set to "none" to disable thinking.
    #
    # "none" is the only value that reliably means *off*. The rest of the scale
    # is renderer-dependent: on gemma4 (the current `general`) low/medium/high
    # all collapse to plain "thinking on", while qwen3.8 does distinguish them.
    # Never treat this as a dial without checking the model's renderer first.
    reasoning_effort: Optional[str] = None


# Single env-driven config — no specific server implied. All fields come from
# env (see config.WorkerConfig): LLM_BASE_URL / LLM_MODEL / LLM_PROVIDER /
# LLM_REASONING_EFFORT. Uses the OpenAI-compatible /v1/responses endpoint, which
# ollama, LM Studio, vLLM and others all expose. reasoning_effort="none" (the
# default) suppresses thinking-token output on thinking-capable models; it was
# the May 2026 BDR eval winner and confirmed at parity for module summaries
# (scripts/eval_module_summary.py).
LLM_CONFIG = LLMConfig(
    provider=config.llm_provider,
    model=config.llm_model,
    base_url=config.llm_base_url,
    temperature=0.3,
    reasoning_effort=config.llm_reasoning_effort or None,
)

DEFAULT_CONFIG = LLM_CONFIG


class LLMEnricher:
    """Enriches code chunks using local LLMs"""

    def __init__(self, config: LLMConfig = LLM_CONFIG):
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

    def _empty_output_error(self, data: dict, prompt: str) -> ValueError:
        """
        Build the error for a /v1/responses reply that carried no usable text,
        logging the raw response so the next occurrence is diagnosable.

        This used to assert "likely truncated while thinking" for every empty
        message. That was wrong often enough to be worth naming: reasoning is
        disabled on this path (reasoning_effort defaults to "none", and ollama
        honours it — the reply carries no `reasoning` item and
        reasoning_tokens=0), so thinking is not where the budget goes. On
        2026-08-22 seven of these fired in one run and six stopped between 83
        and 335 tokens, nowhere near the 2000-token ceiling: not truncation at
        all. Only the seventh hit the cap exactly.

        So report what the response actually says and let the reader judge.
        `status` and `incomplete_details` are the API's own account of why it
        stopped, and hitting the ceiling is inferred from the token counts
        rather than assumed.
        """
        usage = data.get("usage") or {}
        out_tokens = usage.get("output_tokens")
        reasoning_tokens = (usage.get("output_tokens_details") or {}).get("reasoning_tokens")
        truncated = out_tokens is not None and out_tokens >= self.config.max_tokens

        # Bounded: launchd.out.log is already tens of MB and this fires per
        # retry attempt. The interesting part of an empty reply is its shape,
        # not its tail.
        raw = json.dumps(data, default=str)
        if len(raw) > 4000:
            raw = raw[:4000] + f"... [truncated, {len(raw)} chars total]"
        logger.error(
            f"Empty /v1/responses reply from {self.config.model} "
            f"(prompt {len(prompt)} chars): {raw}"
        )

        detail = "hit max_output_tokens" if truncated else "stopped short of max_output_tokens"
        return ValueError(
            f"LLM returned no usable output text "
            f"(output_tokens={out_tokens}, max_output_tokens={self.config.max_tokens}, "
            f"reasoning_tokens={reasoning_tokens}, status={data.get('status')!r}, "
            f"incomplete_details={data.get('incomplete_details')!r}); {detail}"
        )

    async def _call_responses(self, prompt: str) -> str:
        """Call the OpenAI-compatible /v1/responses endpoint."""
        payload = {
            "model": self.config.model,
            "input": prompt,
            "temperature": self.config.temperature,
            "max_output_tokens": self.config.max_tokens,
        }
        if self.config.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.config.reasoning_effort}
        # Admission control. See llm_gate: ollama hands out slots FIFO with no
        # priority, so holding fewer of them is the only way to keep cos-web's
        # chat (which shares this `general` runner) off the back of ingestion's
        # queue. Inside the retry loop deliberately — each attempt is a request
        # and must queue on its own merits.
        async with gate_for(self.config.model, lambda: config.llm_enricher_max_inflight):
            response = await self.client.post(
                f"{self.config.base_url}/v1/responses",
                json=payload,
            )
        response.raise_for_status()
        data = response.json()
        # Extract text from the responses API format
        # Response structure: {"output": [{"type": "message", "content": [{"type": "output_text", "text": "..."}]}]}
        #
        # An empty output_text is a failure, not a result: a message block
        # holding "" still arrives with status="completed" and no
        # incomplete_details. Returning that silently is how the daily digest
        # posted empty content nine times between June and August 2026. Raising
        # sends it through generate()'s retry loop like any other bad response.
        output = data.get("output", [])
        for item in output:
            if item.get("type") == "message":
                content = item.get("content", [])
                for block in content:
                    if block.get("type") == "output_text":
                        text = block.get("text") or ""
                        if not text.strip():
                            raise self._empty_output_error(data, prompt)
                        return text
        # Fallback: try to get text directly if format differs
        if "text" in data:
            return data["text"]
        raise ValueError(f"Could not extract text from responses API: {data}")

    async def _call_responses_with_reasoning(self, prompt: str) -> dict:
        """
        Call LM Studio and return both reasoning trace and output.

        Returns:
            dict with 'reasoning' (str or None) and 'output' (str) keys
        """
        payload = {
            "model": self.config.model,
            "input": prompt,
            "temperature": self.config.temperature,
            "max_output_tokens": self.config.max_tokens,
        }
        if self.config.reasoning_effort is not None:
            payload["reasoning"] = {"effort": self.config.reasoning_effort}
        # Admission control. See llm_gate: ollama hands out slots FIFO with no
        # priority, so holding fewer of them is the only way to keep cos-web's
        # chat (which shares this `general` runner) off the back of ingestion's
        # queue. Inside the retry loop deliberately — each attempt is a request
        # and must queue on its own merits.
        async with gate_for(self.config.model, lambda: config.llm_enricher_max_inflight):
            response = await self.client.post(
                f"{self.config.base_url}/v1/responses",
                json=payload,
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

        # Same contract as _call_responses: a missing *or* empty message is a
        # failed call, so it retries rather than yielding an empty BDR.
        if output_text is None or not output_text.strip():
            raise self._empty_output_error(data, prompt)

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
                result = await self._call_responses(prompt)

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
        Generate text with reasoning trace (for thinking models).

        Uses the OpenAI-compatible /v1/responses API to capture the reasoning
        channel separately from the output.

        Returns:
            dict with 'reasoning' (str or None), 'output' (str), 'usage' (dict)
        """
        # Circuit breaker check
        if self._consecutive_failures >= self._max_consecutive_failures:
            raise LLMUnavailableError("LLM unavailable - circuit breaker open")

        last_error = None
        for attempt in range(self.config.max_retries + 1):
            try:
                result = await self._call_responses_with_reasoning(prompt)
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
    enricher = LLMEnricher(LLM_CONFIG)

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
