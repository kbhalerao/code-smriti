# CodeSmriti V4 Ingestion Pipeline Design

## Executive Summary

V4 is a complete rewrite of the ingestion pipeline that fixes V3's fundamental failures through a **bottom-up summary aggregation** approach. Instead of generating summaries top-down, V4 builds from symbols up to files, modules, and finally repository summaries.

## V3 Failures (Why V4)

| Problem | Root Cause | Impact |
|---------|-----------|--------|
| All symbol names are "unknown" | Wrong metadata key mapping (`symbol_name` vs `function_name`) | 0% useful symbol search |
| Only 3/95 repos have repo_summary | Summaries generated before files processed | 97% repos missing overview |
| Only 18 module_summary docs | Only Django apps detected as modules | No folder navigation |
| symbol_index has no LLM summaries | LLM only ran on file_index | Poor symbol-level search |
| 58% files marked underchunked | Detection happened but LLM chunker never invoked | Large files poorly indexed |

## V4 Key Principles

1. **Bottom-up aggregation**: Build summaries from parts to whole
2. **Folder = module**: Use filesystem hierarchy, not framework detection
3. **Graceful degradation**: LLM failure → basic summary + quality flag
4. **All symbols listed**: Every symbol in file metadata, but only >5 lines get docs
5. **Correct metadata mapping**: Fix the `function_name`/`class_name` bug

## Document Hierarchy

```
repo_summary
    ├── module_summary (folder: src/)
    │   ├── module_summary (folder: src/auth/)
    │   │   ├── file_index (src/auth/login.py)
    │   │   │   ├── symbol_index (LoginView class, >5 lines)
    │   │   │   └── symbol_index (authenticate func, >5 lines)
    │   │   └── file_index (src/auth/utils.py)
    │   └── module_summary (folder: src/api/)
    │       └── ...
    └── module_summary (folder: tests/)
        └── ...
```

## Data Flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           V4 INGESTION PIPELINE                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PHASE 1: FILE PROCESSING (per file, parallelizable)                        │
│  ─────────────────────────────────────────────────────                       │
│                                                                              │
│  1.1 Read file content                                                       │
│      └── git show {commit}:{path} for exact version                         │
│                                                                              │
│  1.2 Parse with tree-sitter                                                  │
│      └── Extract symbols with CORRECT name mapping:                          │
│          - chunk.metadata.get("function_name")                               │
│          - chunk.metadata.get("class_name")                                  │
│          - chunk.metadata.get("method_name")                                 │
│                                                                              │
│  1.3 Quality check: is_underchunked()?                                       │
│      ├── adequate → proceed with tree-sitter symbols                         │
│      └── underchunked → invoke LLM chunker for semantic chunks               │
│                                                                              │
│  1.4 Generate symbol summaries (>5 lines only)                               │
│      └── LLM: docstring + code → summary                                     │
│      └── Fallback: docstring only if LLM unavailable                         │
│                                                                              │
│  1.5 Generate file summary                                                   │
│      └── LLM: symbol summaries + file preview → file summary                 │
│      └── Fallback: list of symbols + imports                                 │
│                                                                              │
│  1.6 Create embeddings                                                       │
│      └── nomic-embed(summary + code_preview)                                 │
│                                                                              │
│  1.7 Store file_index + symbol_index docs                                    │
│                                                                              │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  PHASE 2: BOTTOM-UP AGGREGATION (after all files processed)                  │
│  ──────────────────────────────────────────────────────────                  │
│                                                                              │
│  2.1 Build folder tree from file paths                                       │
│      └── Group files by directory                                            │
│                                                                              │
│  2.2 For each folder (leaf to root):                                         │
│      └── Aggregate file summaries → module_summary                           │
│      └── LLM: "Given these file summaries, describe this module"             │
│      └── Fallback: list of key files + purposes                              │
│                                                                              │
│  2.3 Generate repo_summary                                                   │
│      └── Aggregate module summaries → repo_summary                           │
│      └── LLM: "Given these modules, describe this repository"                │
│      └── Fallback: tech stack + module list                                  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Document Schemas

### Unified ID Format

All documents use `document_id` as primary key, generated as a **content-based hash** for deduplication:

```python
# Hash inputs ensure same content = same ID (skip duplicate processing)
document_id = sha256(f"{type}:{repo_id}:{path}:{commit}").hexdigest()

# Examples:
repo_doc_id     = sha256("repo:kbhalerao/labcore:abc123def456").hexdigest()
module_doc_id   = sha256("module:kbhalerao/labcore:associates:abc123def456").hexdigest()
file_doc_id     = sha256("file:kbhalerao/labcore:associates/models.py:abc123def456").hexdigest()
symbol_doc_id   = sha256("symbol:kbhalerao/labcore:associates/models.py:UserModel:abc123def456").hexdigest()
```

The hash is deterministic - same repo + path + commit = same document_id. This enables:
- **Deduplication**: Skip processing if document_id already exists
- **Incremental updates**: Only process changed files (different commit = different hash)
- **Idempotent ingestion**: Re-running produces same IDs

### repo_summary

```json
{
    "document_id": "repo:kbhalerao/labcore:abc123def456",
    "type": "repo_summary",
    "repo_id": "kbhalerao/labcore",
    "commit_hash": "abc123def456789...",

    "content": "LabCore is a Django-based Laboratory Information Management System (LIMS) for veterinary diagnostics labs. It provides multi-tenant organization management, sample tracking, and integration with external services like HubSpot and USDA APIs.\n\n## Key Modules\n- associates/: Multi-tenant user and organization management\n- samples/: Sample submission and tracking\n- workflows/: Task automation and scheduling",

    "embedding": [/* 768 floats */],

    "metadata": {
        "total_files": 450,
        "total_lines": 125000,
        "languages": {"python": 380, "html": 45, "javascript": 25},
        "tech_stack": ["django", "postgresql", "celery", "redis"],
        "modules": ["associates", "samples", "workflows", "clients"]
    },

    "children_ids": [
        "module:kbhalerao/labcore:associates:abc123def456",
        "module:kbhalerao/labcore:samples:abc123def456"
    ],

    "quality": {
        "enrichment_level": "llm_summary",
        "llm_available": true,
        "summary_source": "aggregated_from_modules",
        "enrichment_cost": 2500
    },

    "version": {
        "schema_version": "v4.0",
        "pipeline_version": "2025.11.28",
        "created_at": "2025-11-28T10:00:00Z"
    }
}
```

### module_summary

```json
{
    "document_id": "module:kbhalerao/labcore:associates:abc123def456",
    "type": "module_summary",
    "repo_id": "kbhalerao/labcore",
    "module_path": "associates",
    "commit_hash": "abc123def456789...",

    "content": "The associates module handles multi-tenant user and organization management. It provides permission mixins for object-level access control using django-guardian.\n\n## Key Components\n- models.py: User, Organization, Role models\n- role_privileges.py: FilteredQuerySetMixin for tenant isolation\n- backends.py: Custom authentication backend",

    "embedding": [/* 768 floats */],

    "metadata": {
        "file_count": 12,
        "key_files": ["models.py", "role_privileges.py", "backends.py"]
    },

    "parent_id": "repo:kbhalerao/labcore:abc123def456",
    "children_ids": [
        "file:kbhalerao/labcore:associates/models.py:abc123def456",
        "file:kbhalerao/labcore:associates/role_privileges.py:abc123def456"
    ],

    "quality": {
        "enrichment_level": "llm_summary",
        "summary_source": "aggregated_from_files",
        "enrichment_cost": 1200
    }
}
```

### file_index

```json
{
    "document_id": "file:kbhalerao/labcore:associates/role_privileges.py:abc123def456",
    "type": "file_index",
    "repo_id": "kbhalerao/labcore",
    "file_path": "associates/role_privileges.py",
    "commit_hash": "abc123def456789...",

    "content": "This file provides Django view mixins for multi-tenant data isolation. FilteredQuerySetMixin automatically filters querysets by the user's organization, ensuring data security in a multi-tenant environment.\n\n## Key Classes\n- UserPrivilegeResolution: Base class for resolving user permissions\n- FilteredQuerySetMixin: Filters querysets by organization",

    "embedding": [/* 768 floats */],

    "metadata": {
        "line_count": 245,
        "language": "python",
        "imports": ["django.db.models", "guardian.shortcuts", "django.contrib.auth"],
        "symbols": [
            {
                "name": "UserPrivilegeResolution",
                "type": "class",
                "lines": [15, 45],
                "significant": true,
                "docstring": "Base class for resolving user privileges...",
                "methods": [
                    {"name": "get_organization", "lines": [20, 30]},
                    {"name": "get_privilege_level", "lines": [32, 45]}
                ]
            },
            {
                "name": "FilteredQuerySetMixin",
                "type": "class",
                "lines": [47, 98],
                "significant": true,
                "docstring": "Mixin that filters querysets by organization...",
                "methods": []
            },
            {
                "name": "_get_org_from_request",
                "type": "function",
                "lines": [100, 103],
                "significant": false,
                "docstring": null,
                "methods": []
            }
        ]
    },

    "parent_id": "module:kbhalerao/labcore:associates:abc123def456",
    "children_ids": [
        "symbol:kbhalerao/labcore:associates/role_privileges.py:UserPrivilegeResolution:abc123def456",
        "symbol:kbhalerao/labcore:associates/role_privileges.py:FilteredQuerySetMixin:abc123def456"
    ],

    "quality": {
        "enrichment_level": "llm_summary",
        "is_underchunked": false,
        "underchunk_reason": "",
        "summary_source": "llm_from_symbols"
    }
}
```

### symbol_index (>5 lines only)

```json
{
    "document_id": "symbol:kbhalerao/labcore:associates/role_privileges.py:FilteredQuerySetMixin:abc123def456",
    "type": "symbol_index",
    "repo_id": "kbhalerao/labcore",
    "file_path": "associates/role_privileges.py",
    "commit_hash": "abc123def456789...",
    "symbol_name": "FilteredQuerySetMixin",
    "symbol_type": "class",

    "content": "FilteredQuerySetMixin is a Django view mixin that automatically filters querysets by the current user's organization. This is essential for multi-tenant data isolation, ensuring users can only access data belonging to their organization.\n\n## Usage\nInherit from this mixin in any view that needs tenant-filtered data:\n```python\nclass MyView(FilteredQuerySetMixin, ListView):\n    model = MyModel\n```\n\n## Methods\n- get_queryset(): Returns queryset filtered by organization\n- get_organization(): Resolves organization from request",

    "embedding": [/* 768 floats */],

    "metadata": {
        "start_line": 47,
        "end_line": 98,
        "line_count": 51,
        "docstring": "Mixin that filters querysets by the user's organization.",
        "methods": [
            {"name": "get_queryset", "lines": [55, 62]},
            {"name": "get_organization", "lines": [64, 72]}
        ],
        "inherits": ["UserPrivilegeResolution"]
    },

    "parent_id": "file:kbhalerao/labcore:associates/role_privileges.py:abc123def456",

    "quality": {
        "enrichment_level": "llm_summary",
        "summary_source": "llm_from_docstring_and_code",
        "enrichment_cost": 400
    }
}
```

## Configuration

```python
# v4/config.py

# Chunk sizing
SYMBOL_MIN_LINES = 5          # Symbols >= this get their own document
MAX_EMBEDDING_TOKENS = 8192   # nomic-embed limit
MAX_CHUNK_CHARS = 32000       # ~8000 tokens, conservative

# LLM settings
LLM_TIMEOUT = 60              # seconds
LLM_RETRY_COUNT = 3
CIRCUIT_BREAKER_THRESHOLD = 5  # consecutive failures to open
CIRCUIT_BREAKER_RESET = 60     # seconds

# Underchunked triggers (same as V3)
UNDERCHUNK_MIN_SIZE = 5000    # chars
UNDERCHUNK_MIN_DENSITY = 100  # lines per chunk
```

## LLM Model Benchmarks

Benchmarked on 2025-12-02 using local LM Studio models for JSON summarization tasks.

### Test Setup
- **Task**: File summarization with structured JSON output
- **Test files**: Python code (500 lines), RST documentation
- **Prompt**: Simplified prompt optimized for smaller models (flat JSON, explicit format)

### Results

| Model | Size | Context | Python Latency | RST Latency | JSON Valid | Quality |
|-------|------|---------|----------------|-------------|------------|---------|
| **Qwen3 30B** | 17 GB | 262K | 2.7s | 1.7s | 100% | Best (includes methods, catches edge cases) |
| **Granite 4 H Tiny** | 4 GB | 65K | 2.6s | 1.4s | 100% | Good (concise, classes only) |
| **MiniMax M2** | 129 GB | 131K | 31s | 13s | 100% | Same as Qwen (no advantage) |

### Observations

1. **Qwen3 30B (current default)**: Best quality-to-speed ratio. Slightly more detailed key_elements, catches code issues like truncation limits. JSON failure rate <1%.

2. **Granite 4 H Tiny**: Viable fallback. Nearly identical speed when Qwen is idle, 90% of the quality. Good for parallel processing when Qwen is busy with other tasks.

3. **MiniMax M2**: No quality advantage over Qwen at 10x the latency. Skip for batch enrichment.

### Prompt Engineering Notes

Smaller models perform better with:
- Flat JSON structure (no nested objects)
- Explicit format examples in prompt
- Lower temperature (0.1 vs 0.3)
- No "think step by step" instructions (wastes tokens)
- Self-contained prompt (no system message preamble)

### Recommendation

- **Primary**: Qwen3 30B for all enrichment tasks
- **Fallback**: Granite 4 H Tiny if Qwen unavailable or for parallel batch jobs
- **Skip**: MiniMax M2 (overkill for this use case)

## Embedding Normalization (Critical)

The Couchbase FTS vector index uses `dot_product` similarity. For dot product to work correctly as a similarity measure, **all embeddings must be normalized to unit length (L2 norm = 1)**.

### Why Normalization Matters

Without normalization:
- `dot_product(a, b) = ||a|| * ||b|| * cos(θ)` — includes magnitude
- Vectors with larger norms dominate search results regardless of semantic similarity
- `repo_filter` effectively becomes ignored because wrong repos can outscore correct ones

With normalization:
- `dot_product(a, b) = cos(θ)` — pure semantic similarity
- Scores range from -1 to 1 (typically 0 to 1 for similar content)
- Filtering works correctly because scores reflect actual relevance

### Implementation

All embedding generation must use `normalize_embeddings=True`:

```python
# Ingestion (local_generator.py)
embedding = self.model.encode(
    f"search_document: {text}",  # Document prefix
    normalize_embeddings=True     # CRITICAL
)

# Search queries (tools.py)
query_embedding = embedding_model.encode(
    f"search_query: {query}",     # Query prefix (different!)
    normalize_embeddings=True      # CRITICAL
)
```

### Bi-Encoder Prefixes

The nomic-ai/nomic-embed-text-v1.5 model is a bi-encoder that expects different prefixes:
- **Documents**: `search_document: {content}` — used during ingestion
- **Queries**: `search_query: {query}` — used during search

Using the wrong prefix degrades retrieval quality.

### Backfill Script

If embeddings become denormalized, run:

```bash
python scripts/normalize_embeddings.py          # Full normalization
python scripts/normalize_embeddings.py --dry-run # Check status only
```

This updates all embeddings in-place using subdocument mutations for efficiency.

## Error Handling

### LLM Failures

```python
class LLMEnricher:
    async def generate_summary(self, content, context) -> EnrichmentResult:
        if self.circuit_breaker.is_open:
            return self.fallback.generate(content, context)

        try:
            result = await self._call_llm(content, context)
            self.circuit_breaker.record_success()
            return EnrichmentResult(
                summary=result,
                enrichment_level=EnrichmentLevel.LLM_SUMMARY,
                llm_available=True
            )
        except LLMError as e:
            self.circuit_breaker.record_failure()
            return self.fallback.generate(content, context)

class FallbackEnricher:
    def generate(self, content, context) -> EnrichmentResult:
        # Build summary from docstrings + structure
        summary = self._extract_docstrings(content)
        return EnrichmentResult(
            summary=summary,
            enrichment_level=EnrichmentLevel.BASIC,
            llm_available=False
        )
```

### Partial File Failures

```python
async def process_repository(repo_id: str):
    results = []
    failures = []

    for file_path in files:
        try:
            result = await process_file(file_path)
            results.append(result)
        except Exception as e:
            failures.append({"file": file_path, "error": str(e)})
            logger.warning(f"Failed to process {file_path}: {e}")

    # Continue with successful files
    # Aggregate summaries from what we have
    await aggregate_summaries(results)

    return IngestionResult(
        files_processed=len(results),
        files_failed=len(failures),
        failures=failures
    )
```

## File Structure

```
services/ingestion-worker/
├── ingest_v4.py                    # CLI entry point
├── V4_DESIGN.md                    # This document
│
├── v4/
│   ├── __init__.py
│   ├── schemas.py                  # Dataclasses for all document types
│   ├── quality.py                  # QualityTracker, EnrichmentLevel
│   ├── file_processor.py           # Single file → file_index + symbol_index
│   ├── aggregator.py               # Bottom-up summary aggregation
│   └── pipeline.py                 # V4Pipeline orchestrator
│
├── parsers/
│   └── code_parser.py              # Tree-sitter (fix symbol name mapping)
│
├── llm_enricher.py                 # LLM calls with circuit breaker
├── llm_chunker.py                  # is_underchunked() + LLM chunking
│
├── embeddings/
│   └── local_generator.py          # nomic-embed with normalization
│
├── scripts/
│   └── normalize_embeddings.py     # Backfill script for normalizing existing embeddings
│
└── storage/
    └── couchbase_client.py         # Add V4 methods
```

## CLI Usage

```bash
# Full ingestion with V3 cleanup
python ingest_v4.py --all --delete-v3

# Single repo test (dry run)
python ingest_v4.py --repo kbhalerao/labcore --dry-run

# Single repo without LLM (basic summaries only)
python ingest_v4.py --repo kbhalerao/labcore --no-llm

# Resume after failure (skip already processed)
python ingest_v4.py --all --skip-existing
```

## Success Criteria

After V4 ingestion completes:

- [ ] All 95 repos have `repo_summary` documents
- [ ] Every folder has a `module_summary` document
- [ ] 13,699+ `file_index` documents (same as V3 file count)
- [ ] All symbol names are correct (not "unknown")
- [ ] `symbol_index` documents have LLM summaries (>5 line symbols)
- [ ] Underchunked files have additional semantic chunks from LLM
- [ ] Quality flags accurately reflect LLM availability
- [ ] Search returns relevant results for "job_counter decorator" type queries

## Migration

1. Run `python ingest_v4.py --all --delete-v3` to clean V3 and ingest V4
2. Verify counts match expectations
3. Test search quality with sample queries
4. Monitor for any files that failed processing
