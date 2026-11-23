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
from typing import Any, Callable, Dict, List, Optional
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

    def __init__(
        self,
        message: str,
        partial_chunks: Optional[List["SemanticChunk"]] = None,
        budget_exhausted: bool = False,
    ):
        super().__init__(message)
        self.partial_chunks: List["SemanticChunk"] = partial_chunks or []
        # True when passes were cut short by the file's wall-clock budget rather
        # than by a bad response, so the caller can file it as a timeout.
        self.budget_exhausted = budget_exhausted


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

# Bounds on one pass's answer, enforced by the response schema because
# list-shaped files otherwise have no natural limit. Probed
# 2026-11-09 at an 11,000-token cap: labcore clients/urls.py (234 lines) answered
# api_contracts with 57 items, one per route, in 9,394 tokens, and embedded_code
# was still emitting its 73rd "regex" item — one per URL pattern — at 11,000.
# Neither was looping; every item was distinct. Those calls took 85-183s each,
# which is what blew the 300s file budget behind most open DLQ timeouts. Twenty
# grouped regions of one file are more than retrieval can use, and fifty
# one-line chunks of a route table are worth less than one chunk describing it.
#
# Schema only, deliberately. A prompt asking the model to group repeated
# entries did shrink route tables (57 items to 9), but on the 20-file chunker
# harness it also cost 10-15% of chunks everywhere else and quadrupled empty
# passes (4 to 13-18 of 60), against 4% run-to-run noise. The schema bounds the
# answer without changing what the model looks for.
#
# Every free-text field is bounded, not just `content`. With only items and
# content bounded, the same-sample harness (ollama 0.40.15) still lost one pass
# to the 8000 ceiling: labcore common/magic_signing.py, a file that normally
# answers embedded_code with one item in ~180 tokens. One unbounded field is
# enough for a runaway. The other limits sit well above what answers use
# (169 saved items: name p99 60 chars, purpose 198, tags 6 x 17, related_symbols
# 57 at most, on a route table), so they cut only runaways. Ollama's grammar
# enforces maxLength and maxItems on nested fields; checked the same day.
MAX_ITEMS_PER_PASS = 20
MAX_CONTENT_CHARS = 400
MAX_NAME_CHARS = 120
MAX_PURPOSE_CHARS = 400
MAX_TAGS, MAX_TAG_CHARS = 10, 40
MAX_RELATED_SYMBOLS, MAX_SYMBOL_CHARS = 30, 80

# ollama's MLX runner gained constrained decoding in 0.33.1. Until then only the
# GGUF engine enforced a json_schema and MLX silently ignored it.
MLX_SCHEMA_MIN_VERSION = (0, 33, 1)
MLX_SCHEMA_MIN_VERSION_STR = ".".join(map(str, MLX_SCHEMA_MIN_VERSION))


def schema_enforced(fmt: str, version: Optional[str]) -> Optional[bool]:
    """Whether an ollama runner honours a `json_schema`, from what the API reports.

    True for GGUF on any version, and for MLX (safetensors) from
    MLX_SCHEMA_MIN_VERSION. None when the format is one we know nothing about, or
    the server version is unknown: callers must not treat that as a failure, since
    the server may not be ollama, and must not treat it as enforcement either.

    This is the API's half of the answer. It cannot see an alias whose *name*
    breaks the prompt template (gemma4 on MLX), so a True here still wants one
    real call through the alias.
    """
    if fmt == "gguf":
        return True
    if fmt != "safetensors":
        return None
    m = re.match(r"(\d+)\.(\d+)\.(\d+)", version or "")
    if not m:
        return None
    return tuple(int(g) for g in m.groups()) >= MLX_SCHEMA_MIN_VERSION


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
    # Optional content gate, (content, language) -> bool. A pass whose subject
    # is visibly absent from the file is not worth a call: see has_embedded_code.
    applies: Optional[Callable[[str, str], bool]] = None


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
        "maxItems": MAX_ITEMS_PER_PASS,
        "items": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": types},
                "name": {"type": "string", "maxLength": MAX_NAME_CHARS},
                "content": {"type": "string", "maxLength": MAX_CONTENT_CHARS},
                "start_line": {"type": "integer"},
                "end_line": {"type": "integer"},
                "purpose": {"type": "string", "maxLength": MAX_PURPOSE_CHARS},
                "related_symbols": {
                    "type": "array", "maxItems": MAX_RELATED_SYMBOLS,
                    "items": {"type": "string", "maxLength": MAX_SYMBOL_CHARS},
                },
                "tags": {
                    "type": "array", "maxItems": MAX_TAGS,
                    "items": {"type": "string", "maxLength": MAX_TAG_CHARS},
                },
                "confidence": {"type": "number"},
            },
            "required": [
                "type", "name", "content", "start_line", "end_line",
                "purpose", "related_symbols", "tags", "confidence",
            ],
            "additionalProperties": False,
        },
    }


# --- Line numbers and windows -------------------------------------------------
#
# The prompt used to carry the raw file, and the schema asks for start_line and
# end_line, so the model had to count lines. It counted badly: on the 20-file
# graded harness (2026-11-23, ollama 0.40.17) judges marked 47-73% of chunks
# incorrect, mostly because the cited range was 4-25 lines from the code the
# chunk quoted. Every line now carries its number, and the model copies it.
#
# It also used to send content[:15000] and drop the rest, so a 1,420-line file
# was analysed to about line 400 and its core logic never seen. The file is now
# split into overlapping windows, each small enough that prompt + the
# MAX_OUTPUT_TOKENS ceiling stays inside OLLAMA_CONTEXT_LENGTH (16384).
WINDOW_CHARS = 16_000          # numbered text per call (~4.5-5.5K tokens)
WINDOW_OVERLAP_LINES = 20      # so a region straddling a boundary is seen whole
NUMBERED_PREAMBLE = (
    "Each source line below is prefixed with its 1-based line number and '| '. "
    "Use those numbers for start_line and end_line, and do not include the "
    "prefixes in `content`.{window_note}\n\n"
)


def numbered_windows(content: str) -> List[tuple]:
    """Split content into (first_line, last_line, total_lines, numbered_text) windows."""
    lines = content.splitlines()
    total = len(lines)
    width = len(str(max(total, 1)))
    numbered = [f"{i:>{width}}| {line}" for i, line in enumerate(lines, 1)]
    windows, start = [], 0
    while start < total:
        size, end = 0, start
        while end < total and (size + len(numbered[end]) + 1 <= WINDOW_CHARS or end == start):
            size += len(numbered[end]) + 1
            end += 1
        windows.append((start + 1, end, total, "\n".join(numbered[start:end])))
        if end >= total:
            break
        start = max(end - WINDOW_OVERLAP_LINES, start + 1)
    return windows or [(1, 0, 0, "")]


_SNAP_MIN = 12  # shorter lines (`return x`, `}`) are too common to locate by


def snap_line_range(item: Dict, lines: List[str]) -> bool:
    """Move an item's line range to where its quoted content actually is.

    The safety net under numbered prompts: if no line of `content` appears in
    the cited range (+/-2), find the occurrence nearest the claimed start and
    move the range there, keeping its length. Content the model paraphrased
    rather than quoted matches nothing and is left alone. Returns True if moved.
    """
    try:
        s, e = int(item.get("start_line") or 0), int(item.get("end_line") or 0)
    except (TypeError, ValueError):
        return False
    total = len(lines)
    if total == 0:
        return False
    if e < s:
        s, e = e, s
    anchors = []
    for part in re.split(r"\n|;\s+", item.get("content") or ""):
        part = part.strip()
        if len(part) >= _SNAP_MIN:
            anchors.append(part)
        if len(anchors) >= 4:
            break
    if not anchors:
        return False
    hits = [i for i, line in enumerate(lines, 1)
            if any(a in line or (len(line.strip()) >= _SNAP_MIN and line.strip() in a) for a in anchors)]
    if not hits or any(s - 2 <= h <= e + 2 for h in hits):
        return False
    target = min(hits, key=lambda h: abs(h - s))
    span = max(0, e - s)
    item["start_line"] = target
    item["end_line"] = min(total, target + span)
    return True


def _span_overlap(a: "SemanticChunk", b: "SemanticChunk") -> float:
    inter = max(0, min(a.end_line, b.end_line) - max(a.start_line, b.start_line) + 1)
    shorter = min(a.end_line - a.start_line + 1, b.end_line - b.start_line + 1)
    return inter / shorter if shorter > 0 else 0.0


# --- Gating ---------------------------------------------------------------------
#
# The graded harness also showed where the value is. Chunks the judges marked
# most useful (2 of 2), by pass, general / structured:
#   business_logic  22 / 22      embedded_code  3 / 7      api_contracts  2 / 3
# api_contracts produced 44 / 89 chunks, mostly restating route handlers that
# tree-sitter already indexes by name (15 / 42 judged useless), so it is no longer
# a default pass. Re-graded after numbering and windowing landed, the split held:
#   business_logic  38 / 73      embedded_code  8 / 5      api_contracts  3 / 9
# so dropping api_contracts costs ~10% of structured's top-rated chunks for a
# third of its calls (20 files: 743s -> 380s at production concurrency). embedded_code runs only when there is visibly code inside a
# string; otherwise it is a call that can only return [] or invent something.
_SQL_IN_TEXT = re.compile(
    r"\b(?:SELECT\b[\s\S]{1,300}?\bFROM\b|INSERT\s+INTO\b|UPDATE\s+[\w.\"`]+\s+SET\b|"
    r"DELETE\s+FROM\b|CREATE\s+(?:TABLE|INDEX|VIEW|FUNCTION)\b|WITH\s+\w+\s+AS\s*\()"
)
_SQL_CALL = re.compile(r"\.(?:execute|executemany|raw|query)\s*\(|\btext\s*\(\s*[fr]?[\"']", re.I)
_HTML_BLOB = re.compile(r"<[a-z][a-z0-9]*[^>]*>[\s\S]{50,}?</[a-z][a-z0-9]*>", re.I)
_REGEX_CALL = re.compile(
    r"\bre\.(?:compile|match|search|sub|subn|findall|finditer|fullmatch|split)\(\s*r?[\"'][^\"'\n]{8,}"
    r"|new\s+RegExp\(|(?<![\w)\]])/(?:[^/\n\\]|\\.){25,}/[gimsuy]*"
)
_SHELL_CALL = re.compile(
    r"\bsubprocess\.(?:run|call|check_call|check_output|Popen)\(\s*f?[\"']|\bos\.system\(|"
    r"\bshell\s*=\s*True|\bexecSync\(|\bspawn\("
)
# Not strings, but the judges rated ORM aggregations as highly as raw SQL
# (labcore apiviews_composition.py: 4 of 4 at useful=2), and they are the same
# question a searcher asks ("where do we count fields by type?").
_ORM_AGG = re.compile(r"\.(?:annotate|aggregate)\s*\(")
_GQL = re.compile(r"\bgql\s*[`(]|\b(?:query|mutation|subscription)\s+\w*\s*[({]")
_JSON_SCHEMA = re.compile(r"[\"']\$schema[\"']|[\"']properties[\"']\s*:\s*\{")
MARKUP_EXTENSIONS = (".svelte", ".vue", ".html", ".htm", ".jsx", ".tsx", ".astro")


def has_embedded_code(content: str, language: str) -> bool:
    """Whether a file visibly contains code inside strings, worth an embedded_code pass."""
    if _SQL_IN_TEXT.search(content) or _REGEX_CALL.search(content) or _SHELL_CALL.search(content):
        return True
    if _GQL.search(content) or _JSON_SCHEMA.search(content):
        return True
    if _SQL_CALL.search(content) or _ORM_AGG.search(content):
        return True
    # HTML only counts when it is *inside* code. In a markup-first language it is
    # the code, and tree-sitter / the component parser already sees it.
    if language not in ("html", "svelte", "vue") and _HTML_BLOB.search(content):
        return True
    return False


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
        languages=["python", "javascript", "typescript"],
        applies=has_embedded_code,
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


# What ingestion runs. ENRICHMENT_PASSES keeps every defined pass so the eval
# harness can still measure the dropped one; see "Gating" above for why
# api_contracts is not here.
DEFAULT_PASSES = [p for p in ENRICHMENT_PASSES if p.name != "api_contracts"]


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
    #
    # A long triple-quoted string used to count on its own ("long_docstring_or_sql"),
    # which is any well-documented Python file: it was a reason on 2,460 of 5,145
    # chunker calls in the run logs, and the only reason on 852. It now counts
    # only when the string holds SQL or markup. Likewise embedded_html fired on
    # the markup of .svelte/.tsx/.html files themselves (1,806 calls, 975 sole
    # reason); in those languages HTML is the code, not something hidden in it.
    for block in re.findall(r'"""[\s\S]{200,}?"""|\'\'\'[\s\S]{200,}?\'\'\'', content):
        if _SQL_IN_TEXT.search(block) or _HTML_BLOB.search(block):
            reasons.append("sql_or_markup_in_long_string")
            break

    markup_file = file_path.lower().endswith(MARKUP_EXTENSIONS)
    embedded_patterns = [
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
        if name == "embedded_html" and markup_file:
            continue
        if name in reasons:
            continue
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
        # Template literals big enough to be hiding an HTML or SQL blob the
        # structural chunker cannot see into. Gated on *length*, like every
        # pattern in §3, rather than on instance count.
        #
        # Counting instances was the bug: `> 3` interpolations flags essentially
        # every non-trivial JS file, because interpolation is how ordinary JS
        # writes a URL, a coordinate pair or a toast message. It fired on 59 of
        # farmworth_frontend's 1,271 files and identified nothing — the LLM
        # chunker was called to look for structure that was never there, and the
        # run report named healthy files as degraded.
        big_literals = [
            lit
            for lit in re.findall(r'`[^`]*\$\{[^}]+\}[^`]*`', content)
            if len(lit) >= 200
        ]
        if big_literals:
            reasons.append(f"template_literals ({len(big_literals)} substantial instances)")

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
        # depends on schema-constrained decoding, so it needs a runner that
        # enforces one (GGUF on any ollama; MLX from 0.33.1). See
        # config.llm_chunker_model.
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

        Before ollama 0.33.1 a safetensors/MLX build accepted `text.format` and
        ignored it, returning fenced prose that fails to parse — with nothing in
        the response marking it as unsupported. That silence is what made this
        cost ~3-5% of chunks undetected, so surface it at the first call instead.
        From 0.33.1 the MLX runner enforces the schema, so a non-GGUF format is
        only a problem on an older server (see schema_enforced).

        `/api/show` and `/api/version` are ollama-specific while the rest of this
        class is provider-agnostic, so a server without them is not an error —
        just skip.
        """
        if self._schema_support_checked:
            return
        self._schema_support_checked = True
        try:
            resp = await self.client.post(f"{self.base_url}/api/show", json={"model": self.model})
            resp.raise_for_status()
            fmt = (resp.json().get("details") or {}).get("format", "unknown")
            ver = await self.client.get(f"{self.base_url}/api/version")
            ver.raise_for_status()
            version = ver.json().get("version")
        except Exception as e:
            logger.debug(f"Could not determine model format/server version for {self.model}: {e}")
            return
        if schema_enforced(fmt, version) is False:
            logger.warning(
                f"Chunker model {self.model!r} is format={fmt} on ollama {version} — "
                f"that runner ignores json_schema before {MLX_SCHEMA_MIN_VERSION_STR}, so "
                f"responses will be unconstrained and some will fail to parse. Upgrade "
                f"ollama, or set LLM_CHUNKER_MODEL to a GGUF build."
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

        Parsed with strict=False, which admits raw control characters inside
        strings. Ollama's MLX grammar allows them, and `structured` emits a
        literal tab when copying indented SQL: agkit.io-services
        app/legacy_ssurgo/field_summary.py failed its embedded_code pass 3 of 3
        times on strict parsing and parsed to 7 items each time without it
        (2026-11-23). It had exhausted its DLQ replays on this alone.
        """
        # Parse as-is first; strip a fence only if that fails. The fence regex
        # used to run first, and it matches fences *inside* the JSON: a chunk
        # whose content quotes markdown (```...```) had the text between its
        # backticks parsed in place of the whole reply. agkit.io-doclet
        # tests/test_doclet.py lost two of three passes per run this way, on
        # replies that were valid JSON (captured 2026-11-09).
        response = response.strip()
        try:
            result = json.loads(response, strict=False)
        except json.JSONDecodeError:
            result = None
        if isinstance(result, list):
            return result

        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', response)
        if result is None and json_match:
            response = json_match.group(1).strip()

        try:
            result = json.loads(response, strict=False)
        except json.JSONDecodeError as e:
            # Try fixing invalid escape sequences (common LLM issue)
            try:
                # Fix invalid escapes by replacing single backslashes not followed by valid escape chars
                fixed = re.sub(r'\\(?!["\\/bfnrtu])', r'\\\\', response)
                result = json.loads(fixed, strict=False)
            except json.JSONDecodeError:
                # Not "no chunks". Under schema-constrained decoding the reply is
                # valid JSON by construction, so landing here means the server did
                # not honour the schema — the documented symptom of a runner that
                # ignores it (a non-GGUF build on ollama < 0.33.1, or an alias whose
                # name changes the prompt rendering; see ~/code/ollama README).
                # _warn_if_schema_unsupported covers the first case; this used to
                # cost ~3-5% of chunks undetected.
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
        passes: List[EnrichmentPass] = None,
        deadline: Optional[float] = None,
    ) -> List[SemanticChunk]:
        """
        Analyze a file and extract semantic chunks.

        Args:
            file_path: Path to the file
            content: File content
            language: Programming language
            existing_chunks: Already extracted chunks (from tree-sitter)
            passes: Which enrichment passes to run (default: all applicable)
            deadline: `loop.time()` by which the file's LLM work must end. A pass
                still running at the deadline is abandoned and any pass not yet
                started is skipped, but every pass that already finished keeps
                its chunks. They used to be lost: the file-level timeout
                cancelled the whole coroutine and rebuilt the file with no LLM.

        Returns:
            List of semantic chunks found

        Raises:
            ChunkerCallFailed: if any pass failed. Passes are independent, so the
                remaining ones still run and whatever they produced is attached to
                the exception as `partial_chunks` — a failure in one pass must not
                discard the work of the others, and must not be silent either.
        """
        if passes is None:
            passes = DEFAULT_PASSES

        all_chunks = []
        failures = []
        budget_exhausted = False
        loop = asyncio.get_running_loop()

        file_lines = content.splitlines()
        windows = numbered_windows(content)

        for pass_config in passes:
            # Skip if pass doesn't apply to this language
            if pass_config.languages and language not in pass_config.languages:
                continue

            # Skip small files
            if len(content) < pass_config.min_file_size:
                continue

            # Skip when the pass's subject is visibly absent (see has_embedded_code)
            if pass_config.applies is not None and not pass_config.applies(content, language):
                logger.debug(f"Skipping {pass_config.name} on {file_path}: nothing it looks for")
                continue

            pass_chunks: List[SemanticChunk] = []
            for first, last, total, numbered in windows:
                window_note = (
                    f" This excerpt is lines {first}-{last} of {total}; report only regions "
                    f"that start inside it." if len(windows) > 1 else ""
                )
                prompt = NUMBERED_PREAMBLE.format(window_note=window_note) + pass_config.prompt_template.format(
                    language=language,
                    content=numbered,
                    existing_chunks=json.dumps([c.get("symbol_name", c.get("name", "")) for c in existing_chunks[:20]])
                )

                remaining = None if deadline is None else deadline - loop.time()
                if remaining is not None and remaining <= 0:
                    budget_exhausted = True
                    failures.append(f"{pass_config.name}: skipped, file budget exhausted")
                    break

                # Call LLM, constrained to this pass's response shape and type vocabulary
                logger.debug(f"Running {pass_config.name} pass on {file_path} (lines {first}-{last})")
                try:
                    response = await asyncio.wait_for(
                        self._call_llm(prompt, schema=response_schema(pass_config.types)),
                        timeout=remaining,
                    )
                    items = self._parse_llm_response(response)
                except asyncio.TimeoutError:
                    budget_exhausted = True
                    logger.error(f"Chunker pass {pass_config.name!r} on {file_path} ran past the file budget")
                    failures.append(f"{pass_config.name}: ran past the file budget")
                    break
                except ChunkerCallFailed as e:
                    logger.error(f"Chunker pass {pass_config.name!r} failed on {file_path} (lines {first}-{last}): {e}")
                    failures.append(f"{pass_config.name}: {e}")
                    continue

                for item in items:
                    if item.get("confidence", 0) < 0.7:
                        continue
                    if snap_line_range(item, file_lines):
                        logger.debug(f"Snapped {item.get('name')!r} in {file_path} to line {item['start_line']}")

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
                    # Windows overlap, so the same region can be reported twice.
                    dup = next((c for c in pass_chunks
                                if c.chunk_type == chunk.chunk_type and _span_overlap(c, chunk) >= 0.8), None)
                    if dup is not None:
                        if chunk.confidence > dup.confidence:
                            pass_chunks[pass_chunks.index(dup)] = chunk
                        continue
                    pass_chunks.append(chunk)
                    logger.debug(f"Found {chunk.chunk_type}: {chunk.name} (confidence={chunk.confidence})")
            all_chunks.extend(pass_chunks)

        if failures:
            raise ChunkerCallFailed(
                f"{len(failures)} of {len(passes)} chunker pass(es) failed on {file_path}: "
                + "; ".join(failures),
                partial_chunks=all_chunks,
                budget_exhausted=budget_exhausted,
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
