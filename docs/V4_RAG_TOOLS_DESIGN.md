# V4 RAG Tools Design

**Version**: 1.1
**Date**: 2025-11-30
**Status**: Production

## Overview

This document defines the modular RAG tool architecture for CodeSmriti V4. The design supports two usage modes with a shared tool layer.

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     TOOL LAYER (shared)                      │
│  list_repos | explore_structure | search_code | get_file    │
└─────────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
┌─────────────────────┐          ┌─────────────────────────────┐
│  MCP Server         │          │  PydanticAI Agent           │
│  (rag_mcp_server)   │          │  (pydantic_rag_agent)       │
│                     │          │                             │
│  - Direct tool call │          │  - LLM orchestrates tools   │
│  - You reason       │          │  - ask_codebase() wraps     │
│  - No LLM needed    │          │  - Local LLM synthesizes    │
└─────────────────────┘          └─────────────────────────────┘
         │                                    │
         ▼                                    ▼
   Claude Code                         LMStudio / Ollama
```

## Usage Modes

### Mode 1: Claude Code MCP (Direct Tool Access)

Claude Code calls tools directly via MCP protocol. No intermediate LLM - Claude is the reasoning engine and synthesizes answers from tool outputs.

```
User Question
     │
     ▼
Claude Code (reasoning)
     │
     ├── list_repos()           → "What repos exist?"
     ├── explore_structure()    → "What's in this folder?"
     ├── search_code()          → "Find relevant code"
     ├── get_file()             → "Read actual implementation"
     │
     ▼
Claude Code (synthesis)
     │
     ▼
Answer to User
```

### Mode 2: LLM-Driven (LMStudio / Ollama)

Local LLM orchestrates tools via PydanticAI agent. The `ask_codebase` tool wraps the search+synthesis pattern for simpler queries.

```
User Question
     │
     ▼
PydanticAI Agent
     │
     ├── ask_codebase()         → LLM searches + synthesizes
     │   └── (internally uses search_code, get_file)
     │
     ▼
Local LLM (synthesis)
     │
     ▼
Answer to User
```

## Tool Definitions

### 1. `list_repos`

**Purpose**: Discover available repositories.

**Signature**:
```python
list_repos() -> List[RepoInfo]
```

**Returns**:
```python
class RepoInfo:
    repo_id: str           # "owner/repo"
    doc_count: int         # Number of indexed documents
    languages: List[str]   # Primary languages
```

**When to use**: Orientation, when user mentions unfamiliar project name.

---

### 2. `explore_structure`

**Purpose**: Navigate repository structure - list directories and files.

**Signature**:
```python
explore_structure(
    repo_id: str,
    path: str = "",              # "" = repo root
    pattern: str = None,         # Optional glob: "*.py", "tests/**"
    include_summaries: bool = False  # Include module_summary content
) -> StructureInfo
```

**Returns**:
```python
class StructureInfo:
    repo_id: str
    path: str
    directories: List[str]       # ["src/", "tests/", "docs/"]
    files: List[FileInfo]        # Files in this directory
    key_files: Dict[str, str]    # Auto-detected: {"config": "pyproject.toml", ...}
    summary: Optional[str]       # module_summary content if include_summaries=True

class FileInfo:
    name: str
    path: str
    language: str
    line_count: int
    has_summary: bool            # True if file_index exists
```

**When to use**:
- Understanding project layout
- Finding entry points (main.py, index.ts)
- Locating config files
- Before diving into search

---

### 3. `search_code`

**Purpose**: Semantic search across indexed documents at specified granularity.

**Signature**:
```python
search_code(
    query: str,
    level: Literal["symbol", "file", "module", "repo", "doc"] = "file",
    repo_filter: str = None,
    limit: int = 5,
    preview: bool = False  # Return truncated content for quick scanning
) -> List[SearchResult]
```

**Level mapping to V4 doc types**:
| Level | Doc Type | Use Case |
|-------|----------|----------|
| `symbol` | `symbol_index` | Find specific functions/classes |
| `file` | `file_index` | Find relevant files |
| `module` | `module_summary` | Find relevant folders/areas |
| `repo` | `repo_summary` | High-level project understanding |
| `doc` | `document` | Documentation files (RST, MD, design docs) |

**Query Routing Strategy**:
| Query Intent | Recommended Level |
|--------------|-------------------|
| "How does X work" / implementation | `file` (default) |
| "Find function/class X" | `symbol` |
| "What principles/guidelines" / conceptual | `doc` |
| "What is in X folder" | `module` |
| "What repos have X" / overview | `repo` |

**Preview Mode**: Use `preview=True` for quick context scanning before fetching full content. Useful when you need to evaluate multiple results before deep-diving.

**Returns**:
```python
class SearchResult:
    document_id: str
    doc_type: str                # "symbol_index", "file_index", etc.
    repo_id: str
    file_path: Optional[str]     # For file/symbol level
    symbol_name: Optional[str]   # For symbol level
    content: str                 # LLM-generated summary
    score: float

    # Hierarchy navigation
    parent_id: Optional[str]     # Navigate up
    children_ids: List[str]      # Navigate down

    # For code retrieval
    start_line: Optional[int]
    end_line: Optional[int]
```

**When to use**:
- Finding code related to a concept
- Answering "how does X work" questions
- Locating implementations

---

### 4. `get_file`

**Purpose**: Retrieve actual source code from repository.

**Signature**:
```python
get_file(
    repo_id: str,
    file_path: str,
    start_line: int = None,      # 1-indexed, omit for whole file
    end_line: int = None         # 1-indexed, inclusive
) -> FileContent
```

**Returns**:
```python
class FileContent:
    repo_id: str
    file_path: str
    code: str                    # Actual source code
    start_line: int
    end_line: int
    total_lines: int
    language: str
    truncated: bool              # True if content was cut off
```

**When to use**:
- After search, to get actual implementation
- When user asks to "show me the code"
- Reading specific functions (using line numbers from search)

---

### 5. `ask_codebase`

**Purpose**: High-level RAG query - search + LLM synthesis in one call.

**Signature**:
```python
ask_codebase(
    query: str
) -> str
```

**Returns**: Markdown-formatted answer with code snippets and citations.

**When to use**:
- Simple questions in LLM mode
- When user wants a synthesized answer, not raw search results

**Note**: In MCP mode, this calls the backend LLM for synthesis. In LLM mode, the agent synthesizes directly.

---

## Tool Availability by Mode

| Tool | MCP Mode | LLM Mode | Notes |
|------|:--------:|:--------:|-------|
| `list_repos` | ✅ | ✅ | Identical |
| `explore_structure` | ✅ | ✅ | Identical - navigate directories |
| `search_code` | ✅ | ✅ | Identical - with level + preview parameters |
| `get_file` | ✅ | ✅ | Identical - fetch actual code |
| `ask_codebase` | ✅ | ✅ | MCP calls backend LLM; LLM mode synthesizes directly |

Both modes have full access to all navigation and search tools.

## Progressive Disclosure Patterns

### Pattern 1: Top-Down (Overview → Details)

```
1. ask_codebase("What does labcore do?", level="repo")
   → High-level overview from repo_summary

2. search_code("authentication", level="module")
   → Find auth-related modules

3. explore_structure("kbhalerao/labcore", "associates/")
   → See files in auth module

4. get_file("kbhalerao/labcore", "associates/backends.py")
   → Read actual implementation
```

### Pattern 2: Bottom-Up (Specific → Context)

```
1. search_code("job_counter decorator", level="symbol")
   → Find the specific decorator

2. get_file("labcore", "utils/decorators.py", 45, 78)
   → Read the code

3. search_code("job_counter usage", level="file")
   → Find files that use it

4. explore_structure("labcore", "utils/")
   → Understand surrounding context
```

### Pattern 3: Structural Navigation

```
1. list_repos()
   → See available repos

2. explore_structure("labcore", "")
   → See top-level structure

3. explore_structure("labcore", "src/", include_summaries=True)
   → Dive into src with context

4. search_code("main entry point", level="file", repo_filter="labcore")
   → Find entry point
```

## File Structure

```
services/
├── api-server/
│   └── app/
│       ├── rag/                      # NEW: Shared RAG core
│       │   ├── __init__.py
│       │   ├── tools.py              # Tool implementations
│       │   ├── models.py             # Pydantic models (RepoInfo, SearchResult, etc.)
│       │   ├── search.py             # search_code implementation
│       │   ├── structure.py          # explore_structure implementation
│       │   └── file_fetcher.py       # get_file implementation
│       │
│       └── chat/
│           ├── routes.py             # HTTP endpoints (unchanged)
│           └── pydantic_rag_agent.py # LLM agent using tools
│
└── mcp-server/
    └── rag_mcp_server.py             # MCP wrapper calling api-server endpoints
```

## API Endpoints

The api-server exposes REST endpoints that both MCP server and PydanticAI agent can use:

| Endpoint | Method | Tool |
|----------|--------|------|
| `/api/rag/repos` | GET | list_repos |
| `/api/rag/structure` | POST | explore_structure |
| `/api/rag/search` | POST | search_code |
| `/api/rag/file` | POST | get_file |
| `/api/rag/` | POST | ask_codebase (LLM mode) |

## Implementation Priority

1. **Phase 1**: Update `search_code` with `level` parameter
2. **Phase 2**: Implement `explore_structure`
3. **Phase 3**: Update MCP server to expose new tools
4. **Phase 4**: Update PydanticAI agent system prompt for V4 awareness
5. **Phase 5**: Test both modes end-to-end

## Success Criteria

1. MCP tools work standalone (Claude Code can navigate without LLM)
2. LLM agent uses appropriate granularity based on query
3. Progressive disclosure works (can drill down/roll up)
4. Both modes share same underlying tool implementations
5. `explore_structure` correctly lists files from repo on disk

## Implementation Notes

### Couchbase FTS Hybrid Search

**Critical**: Couchbase 7.6.2 KNN filter alone does NOT pre-filter results. You must use hybrid search:

```json
{
  "query": {"term": "symbol_index", "field": "type"},
  "knn": [{
    "field": "embedding",
    "vector": [...],
    "k": 10
  }],
  "knn_operator": "and",
  "size": 10,
  "fields": ["*"]
}
```

The combination of `query` + `knn` + `knn_operator: "and"` ensures:
1. First, a text query filters by document type
2. Then, KNN is performed only on filtered results
3. This enables proper level-based search (symbol vs file vs module vs repo vs doc)

### FTS Index Requirements

The `code_vector_index` must have `keyword_analyzer` on the `type` field for all document types. Without this, the standard analyzer tokenizes values like `symbol_index` into `["symbol", "index"]` breaking exact matching.

See: `scripts/setup/code_vector_index.json`

### Preview Mode

The `preview` parameter truncates content to ~200 characters. Use this for:
1. Quick scanning of multiple results
2. Context management (reduce token usage)
3. Deciding which results to fetch in full with `get_file`
