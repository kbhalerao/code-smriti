# Ingestion Pipeline Enhancement Session - November 24, 2024

## Executive Summary

This session focused on improving chunking quality for the CodeSmriti ingestion pipeline. We ran the MacStudio analysis steps, identified quality issues, enabled tree-sitter parsing, added SQL support, and created an LLM-assisted chunking system for handling edge cases that structural parsing misses.

---

## 1. Environment Setup

### Launchctl Agent Disabled
```bash
launchctl unload ~/Library/LaunchAgents/com.codesmriti.incremental-update.plist
```
**Important**: Do NOT re-enable until V3 migration is complete and versioning is in place.

### Python Downgrade to 3.12
- **Reason**: `tree-sitter-languages` only has wheels for Python ≤3.12
- **Changes**:
  - `pyproject.toml`: `requires-python = ">=3.12,<3.13"`
  - `.python-version`: `3.12`
  - Added dependencies: `tree-sitter-languages>=1.10.2`, `tree-sitter<0.22`

### LM Studio Configuration
- **Endpoint**: `http://macstudio.local:1234`
- **Model**: `qwen/qwen3-30b-a3b-2507` (30B MoE, loaded)
- **Other models available**: `minimax/minimax-m2`, `ibm/granite-4-h-tiny`

---

## 2. Analysis Results

### Embedding Space Analysis (10,000 chunks)

| Metric | Value | Assessment |
|--------|-------|------------|
| Embedding dimension | 768 | Standard |
| Effective dimensions (90% variance) | 1 | ⚠️ Very low |
| Top 10 components explain | 37.8% | Poor diversity |
| Clusters detected | 15 | - |
| Silhouette score | 0.077 | ⚠️ Poor separation |
| Near-duplicate pairs | 100+ | ⚠️ High redundancy |
| Average pairwise distance | 0.899 | - |

**Key Finding**: Embeddings lack diversity. Most variance is captured by a single principal component, suggesting chunks are too similar semantically.

### Quality Audit (98 repositories)

| Metric | Value |
|--------|-------|
| Total chunks | 7,774 |
| Repos with issues | 98 (100%) |

**Quality Distribution**:
- 🟢 High: 4,949 (63.7%)
- 🟡 Medium: 2,340 (30.1%)
- 🟠 Low: 463 (6.0%)
- 🔴 Critical: 22 (0.3%)

**Top Issues**:
1. `missing_language`: 3,361 chunks
2. `missing_git_metadata`: 650 chunks
3. `minimal_content`: 638 chunks
4. `truncated_content`: 37 chunks
5. `empty_content`: 22 chunks

---

## 3. Enhancements Made

### 3.1 Tree-sitter Integration

**Before** (regex fallback):
```
✓ Parsers initialized: regex fallback mode
claudegram: 21 chunks
```

**After** (tree-sitter):
```
✓ Tree-sitter parsers loaded: ['python', 'javascript', 'typescript', 'html', 'css']
claudegram: 41 chunks
```

**Impact**: ~2x more chunks, proper symbol extraction (functions, classes, methods).

### 3.2 SQL File Support

**Files Modified**:
- `config.py`: Added `.sql` to `supported_code_extensions`
- `code_parser.py`: Added `parse_sql_file()` method
- `ingest_v2.py`: Changed hardcoded extensions to use config

**SQL Parser Features**:
- Extracts CREATE TABLE/VIEW/INDEX/FUNCTION/PROCEDURE/TRIGGER statements
- Identifies named queries via comments (`-- Query: GetUserById`)
- Falls back to keyword-based naming for unnamed statements

**Results on evolvechiro**:
- Before: 809 chunks (SQL files ignored)
- After: 840 chunks (+7 SQL files, +24 SQL statement chunks)

### 3.3 LLM-Assisted Chunking (`llm_chunker.py`)

Created a "chunker of last resort" that uses LLM to find what structural parsing misses.

#### Under-chunked Detection (`is_underchunked()`)

Flags files for LLM enrichment based on:
1. Large file with few chunks (>5000 chars, ≤1 chunk)
2. High lines-per-chunk ratio (>100 lines/chunk)
3. Embedded code patterns:
   - SQL in f-strings/format strings
   - HTML templates in strings
   - GraphQL queries
4. SQL execution patterns (`.execute()`, `cursor.`)
5. Heavy string formatting (>5 instances)
6. Unsupported languages with minimal chunks
7. Important file paths (service, handler, controller, etc.)

**Test Results**:
```
transactions/sql_helpers.py: 🔴 NEEDS ENRICHMENT
   Reason: high_density (393 lines/chunk); embedded_sql; heavy_string_formatting (25 instances)

transactions/views.py: 🔴 NEEDS ENRICHMENT
   Reason: high_density (220 lines/chunk); embedded_html; sql_execution_pattern
```

#### Multi-Pass Enrichment (`LLMChunker`)

Configurable passes:
1. **embedded_code**: SQL, HTML, regex, shell commands in strings
2. **business_logic**: Validation, calculations, workflows, authorization
3. **api_contracts**: Endpoints, schemas, middleware

**Test on sql_helpers.py**:
```
Found 3 semantic chunks:
  - sql: get_d1_d2_d3_combined_query (confidence=0.98)
    Tags: ['sql', 'postgresql', 'window functions', 'date filtering']
  - sql: get_visits_summary_query (confidence=0.96)
    Tags: ['sql', 'aggregation', 'completion rate', 'visit metrics']
  - sql: generate_report_query (confidence=0.97)
    Tags: ['sql', 'shift analysis', 'patient visit frequency']
```

### 3.4 Bug Fixes

| File | Issue | Fix |
|------|-------|-----|
| `analyze_embeddings.py` | `language` is SQL reserved word | Backtick-escaped: `` metadata.`language` as lang `` |
| `llm_enricher.py` | Wrong model name and endpoint | Updated to `qwen/qwen3-30b-a3b-2507`, LM Studio config |
| `config.py` | Missing `repos_path` | Added with default `/Users/kaustubh/Documents/codesmriti-repos` |
| `ingest_v2.py` | Hardcoded extensions | Changed to use `config.supported_code_extensions` |

---

## 4. Test Results Summary

| Repository | Files | Chunks Before | Chunks After | Improvement |
|------------|-------|---------------|--------------|-------------|
| kbhalerao/claudegram | 16 | 21 (regex) | 41 (tree-sitter) | +95% |
| PeoplesCompany/public-farmworth | 240 | - | 242 | New (159 Svelte) |
| kbhalerao/evolvechiro | 346 | 809 | 840 | +4% (+SQL) |

---

## 5. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Ingestion Pipeline V2                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐       │
│  │   File       │───▶│  Tree-sitter │───▶│   Chunks     │       │
│  │   Scanner    │    │   Parser     │    │   (Primary)  │       │
│  └──────────────┘    └──────────────┘    └──────┬───────┘       │
│                                                  │               │
│                      ┌──────────────┐            │               │
│                      │ is_under-    │◀───────────┘               │
│                      │ chunked()    │                            │
│                      └──────┬───────┘                            │
│                             │ Yes                                │
│                             ▼                                    │
│                      ┌──────────────┐    ┌──────────────┐       │
│                      │  LLM         │───▶│   Semantic   │       │
│                      │  Chunker     │    │   Chunks     │       │
│                      └──────────────┘    └──────────────┘       │
│                                                                  │
│  Passes: embedded_code → business_logic → api_contracts          │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

---

## 6. Files Changed

| File | Changes |
|------|---------|
| `pyproject.toml` | Python 3.12, tree-sitter deps |
| `.python-version` | `3.12` |
| `config.py` | Added `repos_path`, `.sql` extension |
| `code_parser.py` | SQL parser (+123 lines) |
| `ingest_v2.py` | SQL dispatch, config-based extensions |
| `llm_enricher.py` | Fixed model name, endpoint |
| `analyze_embeddings.py` | Fixed SQL syntax |
| **NEW** `llm_chunker.py` | LLM-assisted chunking system |

---

## 7. Next Steps

### Immediate
1. [ ] Integrate `llm_chunker.py` into main ingestion pipeline
2. [ ] Run LLM enrichment pass on under-chunked files in evolvechiro
3. [ ] Add Svelte tree-sitter parser (currently regex fallback)

### Short-term
4. [ ] Implement V3 normalized schema to reduce embedding redundancy
5. [ ] Add chunk versioning with `protect_from_update` flag
6. [ ] Re-run embedding analysis after improvements

### Long-term
7. [ ] Consider domain-specific embedding model for better clustering
8. [ ] Add cross-file relationship detection in LLM passes
9. [ ] Re-enable launchctl agent with versioning protection

---

## 8. Commands Reference

```bash
# Run embedding analysis
uv run python analyze_embeddings.py --sample 10000 --export embedding_analysis.json

# Run quality audit
uv run python audit_chunks.py --export audit_report.json

# Test LLM enrichment
uv run python llm_enricher.py

# Run V2 ingestion on a repo
uv run python ingest_v2.py --repo kbhalerao/claudegram

# Run with LLM enrichment
uv run python ingest_v2.py --repo kbhalerao/claudegram --enrich --model "qwen/qwen3-30b-a3b-2507"

# Check if launchctl agent is disabled
launchctl list | grep codesmriti  # Should return nothing
```

---

*Session completed November 24, 2024*
