# CodeSmriti V3: Normalized Chunk Schema

**Key Insight**: Since we have access to the actual repo, we don't need to store code redundantly at multiple levels. Store **references + summaries**, fetch code on demand.

---

## The Problem with V2

V2 stores code redundantly:
```
repo_summary     →  overview text (OK)
  └─ module_summary →  module overview (OK)
       └─ file_chunk →  FULL FILE CODE (redundant)
            └─ symbol_chunk →  SYMBOL CODE (subset of file - REDUNDANT!)
```

A 1000-line file with 20 functions stores the same code 21+ times!

---

## V3 Normalized Schema

### Principle: Store Once, Reference Everywhere

```
repo_summary     →  LLM-generated summary + metadata
  └─ module_summary →  LLM-generated summary + file list
       └─ file_chunk →  LLM-generated summary + line count (NO CODE)
            └─ symbol_ref →  summary + (start_line, end_line) (NO CODE)
```

**Code is fetched from the actual repo when needed.**

---

## Chunk Types

### 1. `repo_summary` (unchanged)
```python
{
    "chunk_id": "repo:{repo_id}",
    "type": "repo_summary",
    "repo_id": "kbhalerao/labcore",
    "content": """LLM-generated repo overview...""",
    "embedding": [...],  # Embed the summary
    "metadata": {
        "tech_stack": ["django", "postgresql"],
        "total_files": 450,
        "modules": ["associates", "samples", "workflows"]
    }
}
```

### 2. `module_summary` (unchanged)
```python
{
    "chunk_id": "module:{repo_id}:{path}",
    "type": "module_summary",
    "repo_id": "kbhalerao/labcore",
    "module_path": "associates",
    "content": """LLM-generated module overview...""",
    "embedding": [...],
    "metadata": {
        "files": ["models.py", "views.py", ...],
        "is_django_app": True
    },
    "parent_id": "repo:kbhalerao/labcore"
}
```

### 3. `file_index` (NEW - replaces file_chunk)

**No code stored!** Just summary + metadata for search.

```python
{
    "chunk_id": "file:{repo_id}:{path}:{commit}",
    "type": "file_index",
    "repo_id": "kbhalerao/labcore",
    "file_path": "associates/role_privileges.py",

    # For embedding/search - LLM-generated
    "content": """
    File: associates/role_privileges.py

    Purpose: Permission mixins for Django views that ensure users only
    access data belonging to their organization. Core to labcore's multi-tenancy.

    Key Components:
    - UserPrivilegeResolution: Base class for resolving user's effective org
    - FilteredQuerySetMixin: Mixin for ListView/ListAPIView to filter querysets
    - FilteredObjectMixin: Mixin for DetailView/RetrieveAPIView
    - DealershipRequiredMixin: Ensures user has dealership context

    Usage Pattern:
    class SampleListView(FilteredQuerySetMixin, ListAPIView):
        queryset = Sample.objects.all()
    """,

    "embedding": [...],  # Embed the summary + key info

    # For retrieval - NO CODE, just references
    "metadata": {
        "commit_hash": "abc123",
        "line_count": 245,
        "language": "python",
        "symbols": [
            {"name": "UserPrivilegeResolution", "type": "class", "lines": [15, 45]},
            {"name": "FilteredQuerySetMixin", "type": "class", "lines": [47, 98]},
            {"name": "FilteredObjectMixin", "type": "class", "lines": [100, 145]}
        ],
        "imports": ["django.db.models", "guardian.shortcuts"],
        "quality_score": 0.85
    },

    "parent_id": "module:kbhalerao/labcore:associates"
}
```

### 4. `symbol_index` (NEW - replaces symbol_chunk)

**No code stored!** Just summary + line references.

```python
{
    "chunk_id": "symbol:{repo_id}:{path}:{name}:{commit}",
    "type": "symbol_index",
    "repo_id": "kbhalerao/labcore",
    "file_path": "associates/role_privileges.py",
    "symbol_name": "FilteredQuerySetMixin",
    "symbol_type": "class",

    # For embedding/search
    "content": """
    FilteredQuerySetMixin (class in associates/role_privileges.py)

    A Django mixin that automatically filters querysets to only include
    objects belonging to the current user's organization. Essential for
    multi-tenant data isolation.

    Methods:
    - get_queryset(): Returns filtered queryset
    - get_organization(): Resolves current org from request

    Usage:
    class MyListView(FilteredQuerySetMixin, ListAPIView):
        queryset = MyModel.objects.all()
    """,

    "embedding": [...],

    # For retrieval - line numbers only!
    "metadata": {
        "start_line": 47,
        "end_line": 98,
        "methods": [
            {"name": "get_queryset", "lines": [55, 62]},
            {"name": "get_organization", "lines": [64, 72]}
        ],
        "inherits": ["UserPrivilegeResolution"],
        "docstring": "Mixin for filtering querysets by organization."
    },

    "parent_id": "file:kbhalerao/labcore:associates/role_privileges.py:abc123"
}
```

---

## Retrieval Flow

When a search returns `symbol_index`:

```python
async def get_symbol_code(symbol_chunk: dict, repos_path: Path) -> str:
    """Fetch actual code for a symbol using line numbers"""

    repo_id = symbol_chunk["repo_id"]
    file_path = symbol_chunk["file_path"]
    start_line = symbol_chunk["metadata"]["start_line"]
    end_line = symbol_chunk["metadata"]["end_line"]

    # Path to repo
    repo_path = repos_path / repo_id.replace("/", "_")
    full_path = repo_path / file_path

    # Read specific lines
    with open(full_path) as f:
        lines = f.readlines()
        return "".join(lines[start_line-1:end_line])
```

---

## Storage Comparison

| Approach | 1000-line file, 20 functions |
|----------|------------------------------|
| V1 (current) | ~30KB (file + 20 symbol chunks with code) |
| V2 (redundant) | ~35KB (summaries + code at each level) |
| V3 (normalized) | ~5KB (summaries + line refs only) |

**~85% storage reduction!**

---

## Embedding Strategy

Since we're not storing code, what do we embed?

### Option A: Summary Only
```python
embedding_text = chunk["content"]  # Just the LLM-generated summary
```
**Pro**: Small, focused embeddings
**Con**: May miss code-specific keywords

### Option B: Summary + Code Preview
```python
# At indexing time, include code preview in embedding
def get_embedding_text(chunk: dict, repo_path: Path) -> str:
    summary = chunk["content"]

    if chunk["type"] == "file_index":
        # Include first 50 lines
        code = read_file_lines(repo_path, chunk["file_path"], 1, 50)
        return f"{summary}\n\nCode Preview:\n{code}"

    elif chunk["type"] == "symbol_index":
        # Include full symbol (it's already scoped)
        code = read_file_lines(
            repo_path,
            chunk["file_path"],
            chunk["metadata"]["start_line"],
            chunk["metadata"]["end_line"]
        )
        return f"{summary}\n\n{code}"

    return summary
```
**Pro**: Embeddings capture both semantic meaning AND code patterns
**Con**: Requires repo access at indexing time (which we have)

### Recommendation: Option B

The embedding captures both:
- **Semantic meaning** from LLM summary (answers "what does this do?")
- **Code patterns** from actual code (answers "how is this implemented?")

But we only **store** the summary and line refs. The embedding is computed once and doesn't need the code again.

---

## Migration Path

### Phase 1: Add normalized chunks alongside existing
```python
# Create new chunk types without removing old ones
await create_file_index(file_path, repo_path, repo_id)
await create_symbol_index(symbol, file_path, repo_id)
```

### Phase 2: Update search to prefer new types
```python
# N1QL query modification
SELECT * FROM chunks
WHERE type IN ['file_index', 'symbol_index', 'repo_summary', 'module_summary']
  AND ...
```

### Phase 3: Delete old redundant chunks
```python
# After validation
DELETE FROM chunks WHERE type IN ['file', 'code_chunk', 'function', 'method', 'class']
```

---

## Code Fetcher Service

For retrieval, add a code fetcher that resolves line numbers to actual code:

```python
class CodeFetcher:
    """Fetches actual code from repos using line references"""

    def __init__(self, repos_path: Path):
        self.repos_path = repos_path
        self._cache = {}  # LRU cache of recently read files

    def get_file_content(self, repo_id: str, file_path: str) -> str:
        """Get full file content"""
        path = self.repos_path / repo_id.replace("/", "_") / file_path
        return path.read_text()

    def get_lines(
        self,
        repo_id: str,
        file_path: str,
        start: int,
        end: int
    ) -> str:
        """Get specific line range"""
        content = self.get_file_content(repo_id, file_path)
        lines = content.split("\n")
        return "\n".join(lines[start-1:end])

    def get_symbol_code(self, chunk: dict) -> str:
        """Get code for a symbol_index chunk"""
        return self.get_lines(
            chunk["repo_id"],
            chunk["file_path"],
            chunk["metadata"]["start_line"],
            chunk["metadata"]["end_line"]
        )

    def get_context(self, chunk: dict, context_lines: int = 10) -> str:
        """Get symbol code with surrounding context"""
        start = max(1, chunk["metadata"]["start_line"] - context_lines)
        end = chunk["metadata"]["end_line"] + context_lines
        return self.get_lines(chunk["repo_id"], chunk["file_path"], start, end)
```

---

## RAG Pipeline Update

```python
async def rag_search(query: str, top_k: int = 5) -> List[dict]:
    """Search and retrieve with code fetching"""

    # 1. Embed query
    query_embedding = embed(query)

    # 2. Vector search (returns summaries + line refs)
    results = await vector_search(query_embedding, top_k)

    # 3. Fetch actual code for each result
    code_fetcher = CodeFetcher(repos_path)

    enriched = []
    for chunk in results:
        if chunk["type"] == "symbol_index":
            chunk["code"] = code_fetcher.get_symbol_code(chunk)
        elif chunk["type"] == "file_index":
            # For files, include first 100 lines as context
            chunk["code"] = code_fetcher.get_lines(
                chunk["repo_id"],
                chunk["file_path"],
                1, 100
            )
        enriched.append(chunk)

    return enriched
```

---

## Benefits

1. **~85% storage reduction** - No redundant code
2. **Always fresh** - Code is read from repo at retrieval time
3. **Better embeddings** - Summary + code preview at index time
4. **Simpler updates** - Just update line numbers if code moves
5. **Git-friendly** - Can handle different commits/branches

---

## Questions

1. **Commit pinning**: Should `file_index` be pinned to a specific commit, or always read HEAD?
2. **Stale detection**: How to detect when line numbers are outdated?
3. **Caching**: Cache fetched code in memory/Redis for repeated queries?
