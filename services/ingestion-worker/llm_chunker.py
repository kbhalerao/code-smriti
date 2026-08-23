#!/usr/bin/env python3
"""
LLM-Assisted Chunking

Uses LLM as an intelligent chunker that:
1. Reviews existing tree-sitter chunks
2. Identifies missing semantic units (embedded SQL, business logic, patterns)
3. Creates additional chunks with rich metadata
4. Can run in multiple enrichment passes

This is the "chunker of last resort" - handles what structural parsing misses.
"""

import asyncio
import json
import re
import hashlib
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from pathlib import Path

import httpx
from loguru import logger

from config import WorkerConfig
from llm_gate import gate_for

config = WorkerConfig()


class ChunkerCallFailed(Exception):
    """
    A chunker call did not produce a usable answer.

    This type exists because `_call_llm` used to return `"[]"` from every failure
    path — timeout, HTTP error, bare exception, empty message, truncated reply —
    which is the *identical* value it returns when the model reads a file and
    genuinely finds no additional regions. A failure was therefore
    indistinguishable from a successful empty result: nothing raised, so
    `file_processor.get_llm_chunks`'s `record_llm_call(success=False)` never
    fired either, and the only trace was a log line that nothing counted and
    nothing carried into the run record.

    That silence was expensive. The chunker fires on ~65% of files, and for
    extensions with no tree-sitter grammar (`.sh`, `.sql`, `.vue`, `.kt`) it is
    the *only* source of content — so a swallowed failure put those files in the
    corpus with essentially nothing in them, while the commits index advanced and
    declared the repo current.

    `partial_chunks` carries whatever earlier passes produced before the failing
    one, so raising costs nothing that was already earned.
    """

    def __init__(self, message: str, partial_chunks: Optional[List["SemanticChunk"]] = None):
        super().__init__(message)
        self.partial_chunks: List["SemanticChunk"] = partial_chunks or []


@dataclass
class SemanticChunk:
    """A chunk identified by LLM analysis"""
    chunk_type: str  # embedded_sql, business_logic, api_endpoint, data_transform, etc.
    name: str
    content: str
    start_line: int
    end_line: int
    purpose: str
    related_symbols: List[str] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    confidence: float = 0.0


# Output budget for one chunker call. Generous on purpose: this is a ceiling, not
# an allocation, and typical calls finish around 900 tokens. Hitting it is a
# silent corruption, not a soft limit — see _call_llm's truncation check.
MAX_OUTPUT_TOKENS = 8000


@dataclass
class EnrichmentPass:
    """Configuration for an enrichment pass"""
    name: str
    focus: str  # What to look for
    prompt_template: str
    # The closed vocabulary for this pass's `type` field. Enforced by the JSON
    # schema (see response_schema), so the model cannot invent a category.
    types: List[str] = field(default_factory=list)
    min_file_size: int = 500  # Only analyze files larger than this
    languages: List[str] = field(default_factory=list)  # Empty = all languages


def response_schema(types: List[str]) -> dict:
    """JSON schema for one enrichment pass's response.

    Every pass returns the same shape — an array of found items — differing only
    in the `type` vocabulary. Handing this to the server as a `json_schema`
    response format constrains decoding, so the reply is parseable JSON by
    construction rather than by instruction. Prompt-only "respond with JSON"
    produced 532 unparseable replies in production (~5% of calls), most often by
    wrapping the array in a ```json fence.
    """
    return {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": types},
                "name": {"type": "string"},
                "content": {"type": "string"},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "purpose": {"type": "string"},
                "related_symbols": {"type": "array", "items": {"type": "string"}},
                "tags": {"type": "array", "items": {"type": "string"}},
                "confidence": {"type": "number"},
            },
            "required": [
                "type", "name", "content", "start_line", "end_line",
                "purpose", "related_symbols", "tags", "confidence",
            ],
            "additionalProperties": False,
        },
    }


# Enrichment pass configurations
ENRICHMENT_PASSES = [
    EnrichmentPass(
        name="embedded_code",
        focus="Find code embedded in strings (SQL, HTML, regex, shell commands)",
        prompt_template="""Analyze this {language} code and identify any significant code embedded in strings.

Look for:
- SQL queries in f-strings, format strings, or string concatenation
- HTML templates in strings
- Complex regex patterns
- Shell commands
- GraphQL queries
- JSON schemas

For each embedded code block found, extract:
1. The type (sql, html, regex, shell, graphql, json_schema)
2. A descriptive name
3. The exact content (the embedded code itself)
4. Start and end line numbers
5. What it does (purpose)
6. Related symbols (functions/classes that use it)

Code to analyze:
```{language}
{content}
```

Respond with JSON array of found items:
```json
[
  {{
    "type": "sql",
    "name": "get_user_transactions_query",
    "content": "SELECT t.*, u.name FROM transactions t JOIN users u...",
    "start_line": 45,
    "end_line": 52,
    "purpose": "Fetches user transactions with user details for reporting",
    "related_symbols": ["generate_report", "TransactionService"],
    "tags": ["sql", "join", "transactions", "users"],
    "confidence": 0.95
  }}
]
```

Return empty array [] if no embedded code found. Only include items with confidence > 0.7.""",
        types=["sql", "html", "regex", "shell", "graphql", "json_schema"],
        languages=["python", "javascript", "typescript"]
    ),

    EnrichmentPass(
        name="business_logic",
        focus="Identify business logic patterns and domain concepts",
        prompt_template="""Analyze this {language} code and identify significant business logic patterns.

Look for:
- Validation logic (input validation, business rules)
- State machines or workflow logic
- Calculations (pricing, scoring, aggregations)
- Authorization/permission checks
- Data transformations
- Integration points (API calls, external services)

For each pattern found, extract:
1. The type (validation, workflow, calculation, authorization, transform, integration)
2. A descriptive name reflecting the business domain
3. The relevant code section
4. Line numbers
5. Business purpose (what business problem it solves)
6. Related symbols

Code to analyze:
```{language}
{content}
```

Respond with JSON array:
```json
[
  {{
    "type": "calculation",
    "name": "loan_eligibility_score",
    "content": "score = base_score + income_factor * 0.3 + ...",
    "start_line": 120,
    "end_line": 145,
    "purpose": "Calculates loan eligibility score based on income, credit history, and collateral",
    "related_symbols": ["LoanApplication", "calculate_score", "SCORE_WEIGHTS"],
    "tags": ["loan", "eligibility", "scoring", "financial"],
    "confidence": 0.88
  }}
]
```

Return empty array [] if no significant business logic found. Only include items with confidence > 0.7.""",
        types=["validation", "workflow", "calculation", "authorization", "transform", "integration"],
        languages=[]  # All languages
    ),

    EnrichmentPass(
        name="api_contracts",
        focus="Identify API endpoints, request/response schemas",
        prompt_template="""Analyze this {language} code and identify API-related patterns.

Look for:
- REST endpoint definitions (routes, views)
- Request/response schemas or models
- API authentication/middleware
- GraphQL resolvers or schemas
- WebSocket handlers
- RPC definitions

For each API element found, extract:
1. The type (endpoint, schema, middleware, resolver, websocket, rpc)
2. Name (e.g., "POST /api/users" or "UserCreateSchema")
3. The relevant code
4. Line numbers
5. Purpose
6. Related symbols (serializers, validators, models)

Code to analyze:
```{language}
{content}
```

Respond with JSON array:
```json
[
  {{
    "type": "endpoint",
    "name": "POST /api/transactions",
    "content": "@router.post('/transactions')\\nasync def create_transaction...",
    "start_line": 45,
    "end_line": 78,
    "purpose": "Creates a new financial transaction with validation and audit logging",
    "related_symbols": ["TransactionCreate", "TransactionService", "audit_log"],
    "tags": ["api", "post", "transactions", "create"],
    "confidence": 0.92
  }}
]
```

Return empty array [] if no API patterns found.""",
        types=["endpoint", "schema", "middleware", "resolver", "websocket", "rpc"],
        languages=["python", "javascript", "typescript"]
    ),
]


def is_underchunked(file_path: str, content: str, chunks: List[Dict], language: str) -> tuple[bool, str]:
    """
    Detect if a file is inadequately chunked and needs LLM analysis.

    Returns:
        (needs_enrichment: bool, reason: str)
    """
    reasons = []

    file_size = len(content)
    chunk_count = len(chunks)
    lines = content.count('\n') + 1

    # 1. Large file with few chunks (suspicious)
    if file_size > 5000 and chunk_count <= 1:
        reasons.append(f"large_file_single_chunk ({file_size} chars, {chunk_count} chunks)")

    # 2. High lines-per-chunk ratio (code density)
    if chunk_count > 0:
        lines_per_chunk = lines / chunk_count
        if lines_per_chunk > 100:
            reasons.append(f"high_density ({lines_per_chunk:.0f} lines/chunk)")

    # 3. Contains patterns that suggest embedded code
    embedded_patterns = [
        (r'"""[\s\S]{200,}?"""', "long_docstring_or_sql"),  # Long triple-quoted strings
        (r"f'''[\s\S]{100,}?'''", "f_string_multiline"),
        (r'f"[^"]{100,}"', "long_f_string"),
        (r'SELECT\s+.+FROM', "embedded_sql"),
        (r'INSERT\s+INTO', "embedded_sql"),
        (r'UPDATE\s+.+SET', "embedded_sql"),
        (r'DELETE\s+FROM', "embedded_sql"),
        (r'CREATE\s+TABLE', "embedded_sql"),
        (r'<[a-z]+[^>]*>[\s\S]{50,}?</[a-z]+>', "embedded_html"),
        (r'mutation\s*\{', "embedded_graphql"),
        (r'query\s*\{', "embedded_graphql"),
    ]

    for pattern, name in embedded_patterns:
        if re.search(pattern, content, re.IGNORECASE):
            reasons.append(name)

    # 4. Language-specific checks
    if language == "python":
        # Check for SQL-building patterns
        if re.search(r'\.execute\s*\(|\.query\s*\(|cursor\.|rawsql|text\s*\(', content, re.IGNORECASE):
            if "embedded_sql" not in reasons:
                reasons.append("sql_execution_pattern")

        # Check for complex string formatting
        format_count = len(re.findall(r'\.format\s*\(|%\s*\(|f["\']', content))
        if format_count > 5:
            reasons.append(f"heavy_string_formatting ({format_count} instances)")

    if language == "javascript" or language == "typescript":
        # Template literals with expressions
        template_literals = len(re.findall(r'`[^`]*\$\{[^}]+\}[^`]*`', content))
        if template_literals > 3:
            reasons.append(f"template_literals ({template_literals} instances)")

    # 5. File has no language-specific parsing (fallback chunker was used)
    if language in ("sql", "svelte", "vue", "unknown"):
        if chunk_count <= 2:
            reasons.append(f"unsupported_language_minimal_chunks ({language})")

    # 6. File path suggests importance but has few chunks
    important_patterns = ["service", "handler", "controller", "manager", "helper", "util", "api", "view"]
    if any(p in file_path.lower() for p in important_patterns) and chunk_count <= 2:
        reasons.append("important_file_minimal_chunks")

    needs_enrichment = len(reasons) > 0
    return needs_enrichment, "; ".join(reasons) if reasons else "adequately_chunked"


class LLMChunker:
    """
    LLM-assisted chunker that creates semantic chunks from code.

    Works as a "chunker of last resort" - finds what structural parsing misses.
    """

    def __init__(
        self,
        base_url: str = None,
        model: str = None,
        temperature: float = 0.2,
        reasoning_effort: str = None,
    ):
        # Env-driven (LLM_BASE_URL / LLM_CHUNKER_MODEL) — no specific server implied.
        self.base_url = (base_url or config.llm_base_url).rstrip("/")
        # Deliberately NOT config.llm_model: the chunker is the one caller that
        # depends on schema-constrained decoding, which only ollama's GGUF engine
        # implements. See config.llm_chunker_model.
        self.model = model or config.llm_chunker_model
        self.temperature = temperature
        # Thinking is pinned OFF here, NOT inherited from LLM_REASONING_EFFORT.
        # Chunking is extraction against a fixed schema — there is nothing to
        # reason about, and a reasoning trace only spends the output budget (and
        # wall clock) before the answer. Two reasons this is pinned rather than
        # configured:
        #   1. Inheriting the global means a change made for RAG's benefit
        #      silently slows every ingestion run.
        #   2. "none" is the only value that means off. Omitting the key does NOT
        #      mean off — on gemma4 an absent `reasoning` renders as thinking ON,
        #      so an unset/empty env var would have quietly enabled it.
        self.reasoning_effort = reasoning_effort or "none"
        self._client = None  # Lazy init to avoid event loop issues
        self._client_loop = None  # Track which loop the client was created on
        self._schema_support_checked = False
        logger.info(f"LLM Chunker initialized: {self.model} @ {self.base_url}")

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
            self._client = httpx.AsyncClient(timeout=config.llm_timeout_seconds)
            self._client_loop = current_loop

        return self._client

    async def _warn_if_schema_unsupported(self) -> None:
        """Warn once if this model's runtime can't honour a response schema.

        A safetensors/MLX build accepts `text.format` and ignores it, returning
        fenced prose that fails to parse — with nothing in the response marking
        it as unsupported. That silence is what made this cost ~3-5% of chunks
        undetected, so surface it at the first call instead.

        `/api/show` is ollama-specific while the rest of this class is
        provider-agnostic, so a server without it is not an error — just skip.
        """
        if self._schema_support_checked:
            return
        self._schema_support_checked = True
        try:
            resp = await self.client.post(f"{self.base_url}/api/show", json={"model": self.model})
            resp.raise_for_status()
            fmt = (resp.json().get("details") or {}).get("format", "unknown")
        except Exception as e:
            logger.debug(f"Could not determine model format for {self.model}: {e}")
            return
        if fmt != "gguf":
            logger.warning(
                f"Chunker model {self.model!r} is format={fmt}, not gguf — ollama only "
                f"enforces json_schema on its GGUF engine, so responses will be "
                f"unconstrained and some will fail to parse. Set LLM_CHUNKER_MODEL to a "
                f"GGUF build (e.g. 'structured')."
            )

    async def _call_llm(self, prompt: str, schema: dict = None) -> str:
        """Call the OpenAI-compatible /v1/responses endpoint.

        When `schema` is given it is sent as a `json_schema` response format, so
        the server constrains decoding to that shape. The reply is then valid
        JSON by construction — no fences, no prose, no invented `type` values.
        """
        try:
            if schema is not None:
                await self._warn_if_schema_unsupported()
            full_prompt = "You are a code analysis expert.\n\n" + prompt
            payload = {
                "model": self.model,
                "input": full_prompt,
                "temperature": self.temperature,
                "max_output_tokens": MAX_OUTPUT_TOKENS,
            }
            if schema is not None:
                payload["text"] = {
                    "format": {
                        "type": "json_schema",
                        "name": "semantic_chunks",
                        "strict": True,
                        "schema": schema,
                    }
                }
            # Always sent, never conditional: an absent `reasoning` key is not
            # "off", it is the model's default, which for gemma4 is thinking ON.
            payload["reasoning"] = {"effort": self.reasoning_effort}
            # Admission control, not rate limiting: ollama hands out slots FIFO
            # with no priority, so holding fewer of them is the only way to keep
            # an interactive caller off the back of ingestion's queue. See
            # llm_gate.
            async with gate_for(self.model, lambda: config.llm_chunker_max_inflight):
                response = await self.client.post(
                    f"{self.base_url}/v1/responses",
                    json=payload,
                )
            response.raise_for_status()
            data = response.json()

            # Truncation check. A reply cut off at the token ceiling is still a
            # valid *prefix* under schema-constrained decoding — an unterminated
            # string, typically — so it fails to parse for a reason that has
            # nothing to do with the model ignoring instructions. ollama reports
            # status="completed" with incomplete_details=None even when it
            # stopped at the cap, so output_tokens hitting the ceiling is the
            # only available tell. Say so plainly rather than letting it land as
            # a generic "Failed to parse LLM response as JSON".
            usage = data.get("usage", {})
            if usage.get("output_tokens") is not None and usage["output_tokens"] >= MAX_OUTPUT_TOKENS:
                # A failure, not a warning to read past. The reply is a valid
                # *prefix* under schema-constrained decoding, so it will fail to
                # parse for a reason that has nothing to do with the content —
                # and every chunk in this call is lost either way.
                raise ChunkerCallFailed(
                    f"response hit the {MAX_OUTPUT_TOKENS}-token output ceiling and was "
                    f"truncated mid-structure; chunks from this call are lost. "
                    f"Raise MAX_OUTPUT_TOKENS or shrink the analysed content window."
                )

            # Extract text from responses API format
            output = data.get("output", [])
            for item in output:
                if item.get("type") == "message":
                    content = item.get("content", [])
                    for block in content:
                        if block.get("type") == "output_text":
                            text = block.get("text") or ""
                            if not text.strip():
                                # A thinking model that burns its whole output
                                # budget reasoning returns status="completed"
                                # with an empty message. That is a lost call, not
                                # a file with no semantic regions in it — the
                                # distinction this whole class exists to make.
                                raise ChunkerCallFailed(
                                    f"model returned an empty message "
                                    f"(output_tokens={usage.get('output_tokens')}, "
                                    f"max_output_tokens={MAX_OUTPUT_TOKENS})"
                                )
                            # The one legitimate path. If the model answers "[]"
                            # here, it looked at the file and found nothing —
                            # which is a real result and must stay distinct from
                            # every failure above and below.
                            return text
            # Fallback
            if "text" in data:
                return data["text"]
            raise ChunkerCallFailed(f"unexpected responses API format: {str(data)[:500]}")
        except ChunkerCallFailed:
            # Already precise about what went wrong; do not rewrap it as a
            # generic call failure below.
            raise
        except httpx.HTTPStatusError as e:
            try:
                error_body = e.response.text
            except Exception:
                error_body = "<unreadable>"
            raise ChunkerCallFailed(f"HTTP {e.response.status_code}: {error_body[:500]}") from e
        except Exception as e:
            raise ChunkerCallFailed(f"{type(e).__name__}: {e}") from e

    def _parse_llm_response(self, response: str) -> List[Dict]:
        """Parse JSON from LLM response, handling markdown code blocks.

        With schema-constrained decoding the response is already valid JSON;
        the fence-stripping and escape-repair below are the boundary guard for a
        server that ignores or mis-implements the response format.
        """
        # Extract JSON from markdown code blocks if present
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
        if json_match:
            response = json_match.group(1)

        response = response.strip()

        try:
            result = json.loads(response)
        except json.JSONDecodeError as e:
            # Try fixing invalid escape sequences (common LLM issue)
            try:
                # Fix invalid escapes by replacing single backslashes not followed by valid escape chars
                fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', response)
                result = json.loads(fixed)
            except json.JSONDecodeError:
                # Not "no chunks". Under schema-constrained decoding the reply is
                # valid JSON by construction, so landing here means the server did
                # not honour the schema — the documented symptom of pointing the
                # chunker at a non-GGUF build, which _warn_if_schema_unsupported
                # warns about once and which used to cost ~3-5% of chunks
                # undetected.
                raise ChunkerCallFailed(
                    f"response was not valid JSON ({e}); first 300 chars: {response[:300]!r}"
                ) from e

        if not isinstance(result, list):
            raise ChunkerCallFailed(
                f"response parsed to {type(result).__name__}, expected a list; "
                f"first 300 chars: {response[:300]!r}"
            )
        return result

    async def analyze_file(
        self,
        file_path: str,
        content: str,
        language: str,
        existing_chunks: List[Dict],
        passes: List[EnrichmentPass] = None
    ) -> List[SemanticChunk]:
        """
        Analyze a file and extract semantic chunks.

        Args:
            file_path: Path to the file
            content: File content
            language: Programming language
            existing_chunks: Already extracted chunks (from tree-sitter)
            passes: Which enrichment passes to run (default: all applicable)

        Returns:
            List of semantic chunks found

        Raises:
            ChunkerCallFailed: if any pass failed. Passes are independent, so the
                remaining ones still run and whatever they produced is attached to
                the exception as `partial_chunks` — a failure in one pass must not
                discard the work of the others, and must not be silent either.
        """
        if passes is None:
            passes = ENRICHMENT_PASSES

        all_chunks = []
        failures = []

        for pass_config in passes:
            # Skip if pass doesn't apply to this language
            if pass_config.languages and language not in pass_config.languages:
                continue

            # Skip small files
            if len(content) < pass_config.min_file_size:
                continue

            # Build prompt
            prompt = pass_config.prompt_template.format(
                language=language,
                content=content[:15000],  # Limit content size
                existing_chunks=json.dumps([c.get("symbol_name", c.get("name", "")) for c in existing_chunks[:20]])
            )

            # Call LLM, constrained to this pass's response shape and type vocabulary
            logger.debug(f"Running {pass_config.name} pass on {file_path}")
            try:
                response = await self._call_llm(prompt, schema=response_schema(pass_config.types))
                items = self._parse_llm_response(response)
            except ChunkerCallFailed as e:
                logger.error(f"Chunker pass {pass_config.name!r} failed on {file_path}: {e}")
                failures.append(f"{pass_config.name}: {e}")
                continue

            for item in items:
                if item.get("confidence", 0) < 0.7:
                    continue

                chunk = SemanticChunk(
                    chunk_type=item.get("type", "unknown"),
                    name=item.get("name", "unnamed"),
                    content=item.get("content", ""),
                    start_line=item.get("start_line", 0),
                    end_line=item.get("end_line", 0),
                    purpose=item.get("purpose", ""),
                    related_symbols=item.get("related_symbols", []),
                    tags=item.get("tags", []),
                    confidence=item.get("confidence", 0.0)
                )
                all_chunks.append(chunk)
                logger.debug(f"Found {chunk.chunk_type}: {chunk.name} (confidence={chunk.confidence})")

        if failures:
            raise ChunkerCallFailed(
                f"{len(failures)} of {len(passes)} chunker pass(es) failed on {file_path}: "
                + "; ".join(failures),
                partial_chunks=all_chunks,
            )

        return all_chunks

    async def enrich_repository(
        self,
        repo_path: Path,
        repo_id: str,
        file_chunks: Dict[str, List[Dict]],
        passes: List[EnrichmentPass] = None,
        max_files: int = 100
    ) -> Dict[str, List[SemanticChunk]]:
        """
        Run enrichment passes on a repository.

        Args:
            repo_path: Path to repository
            repo_id: Repository identifier
            file_chunks: Existing chunks per file {file_path: [chunks]}
            passes: Which passes to run
            max_files: Maximum files to process (for cost control)

        Returns:
            Dict of {file_path: [SemanticChunk]}
        """
        results = {}
        processed = 0

        for file_path, chunks in file_chunks.items():
            if processed >= max_files:
                break

            full_path = repo_path / file_path
            if not full_path.exists():
                continue

            try:
                content = full_path.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue

            # Detect language from extension
            ext = Path(file_path).suffix
            language_map = {
                ".py": "python",
                ".js": "javascript",
                ".ts": "typescript",
                ".sql": "sql",
                ".svelte": "svelte"
            }
            language = language_map.get(ext, "unknown")

            semantic_chunks = await self.analyze_file(
                file_path, content, language, chunks, passes
            )

            if semantic_chunks:
                results[file_path] = semantic_chunks
                logger.info(f"Found {len(semantic_chunks)} semantic chunks in {file_path}")

            processed += 1

        return results

    def semantic_chunk_to_dict(
        self,
        chunk: SemanticChunk,
        repo_id: str,
        file_path: str,
        git_metadata: Dict = None
    ) -> Dict:
        """Convert SemanticChunk to storage format"""
        content_hash = hashlib.sha256(chunk.content.encode()).hexdigest()[:16]
        chunk_id = hashlib.sha256(
            f"semantic:{repo_id}:{file_path}:{chunk.name}:{content_hash}".encode()
        ).hexdigest()

        return {
            "chunk_id": chunk_id,
            "type": "semantic_chunk",
            "repo_id": repo_id,
            "file_path": file_path,
            "chunk_type": chunk.chunk_type,
            "content": chunk.content,
            "language": "mixed",  # Semantic chunks often span languages
            "metadata": {
                "name": chunk.name,
                "purpose": chunk.purpose,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "related_symbols": chunk.related_symbols,
                "tags": chunk.tags,
                "confidence": chunk.confidence,
                "enrichment_source": "llm_chunker",
                **(git_metadata or {})
            },
            "embedding": None
        }

    async def close(self):
        """Close HTTP client if it exists and loop is valid."""
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass  # Ignore errors from closed loop
            finally:
                self._client = None
                self._client_loop = None


async def test_chunker():
    """Test the LLM chunker on a sample file"""
    chunker = LLMChunker()

    # Sample Python code with embedded SQL
    test_code = '''
def get_user_transactions(user_id: int, start_date: date, end_date: date) -> List[Transaction]:
    """Fetch all transactions for a user within a date range."""

    query = f"""
        SELECT
            t.id,
            t.amount,
            t.transaction_date,
            t.description,
            c.name as category_name,
            a.account_number
        FROM transactions t
        JOIN categories c ON t.category_id = c.id
        JOIN accounts a ON t.account_id = a.id
        WHERE t.user_id = {user_id}
          AND t.transaction_date BETWEEN '{start_date}' AND '{end_date}'
          AND t.status = 'completed'
        ORDER BY t.transaction_date DESC
    """

    results = db.execute(query)
    return [Transaction(**row) for row in results]


def calculate_monthly_summary(transactions: List[Transaction]) -> Dict:
    """Calculate spending summary by category."""

    summary = defaultdict(float)
    for t in transactions:
        summary[t.category_name] += t.amount

    # Apply business rules
    total = sum(summary.values())
    if total > 10000:
        summary['high_spender_flag'] = True

    return dict(summary)
'''

    try:
        chunks = await chunker.analyze_file(
            "transactions/service.py",
            test_code,
            "python",
            existing_chunks=[{"symbol_name": "get_user_transactions"}, {"symbol_name": "calculate_monthly_summary"}]
        )

        print("=== LLM Chunker Results ===")
        for chunk in chunks:
            print(f"\nType: {chunk.chunk_type}")
            print(f"Name: {chunk.name}")
            print(f"Purpose: {chunk.purpose}")
            print(f"Tags: {chunk.tags}")
            print(f"Confidence: {chunk.confidence}")
            print(f"Content preview: {chunk.content[:200]}...")

        if not chunks:
            print("No semantic chunks found (this might be expected for simple code)")

    finally:
        await chunker.close()


if __name__ == "__main__":
    asyncio.run(test_chunker())
