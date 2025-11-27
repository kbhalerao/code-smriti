# CodeSmriti Chunking V2: LLM-Assisted Hierarchical Indexing

**Status**: Design Draft
**Date**: 2025-11-24
**Goal**: Create a search index that enables LLMs to find and understand code better than humans can

---

## Core Philosophy

### The Problem with V1
- Chunks are **isolated** - no context about where they fit
- Chunks are **uniform** - same treatment for a 5-line util and a 500-line core module
- Chunks are **dumb** - pure syntax parsing, no semantic understanding
- Search returns **fragments** - class definition without implementation

### V2 Vision
1. **Hierarchical**: File → Module → Class → Method with bidirectional references
2. **Semantic**: LLM-generated summaries and purpose descriptions
3. **Quality-Scored**: Every chunk has measurable quality metrics
4. **Deduplicated**: Identify patterns, create "canonical" synthetic chunks
5. **Auditable**: Can verify and incrementally improve any chunk

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                         INGESTION PIPELINE                          │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐        │
│  │  Parse   │ → │ Analyze  │ → │ Enrich   │ → │  Store   │        │
│  │(tree-sit)│   │(structure)│   │  (LLM)   │   │(Couchbase)│       │
│  └──────────┘   └──────────┘   └──────────┘   └──────────┘        │
│       │              │              │              │                │
│       ▼              ▼              ▼              ▼                │
│   Raw AST      Hierarchy      Summaries      Embeddings            │
│   Nodes        Graph          + Purpose      + Metadata            │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                         CHUNK HIERARCHY                             │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  Level 0: REPOSITORY                                                │
│    ├── repo_summary: "Django app for agricultural lab management"  │
│    ├── key_modules: ["associates", "samples", "workflows"]         │
│    └── tech_stack: ["Django", "PostGIS", "Celery", "Redis"]        │
│                                                                     │
│  Level 1: MODULE (Django app / package)                            │
│    ├── module_summary: "Handles organization multi-tenancy"        │
│    ├── key_classes: ["FilteredQuerySetMixin", "Organization"]      │
│    └── dependencies: ["django.contrib.auth", "guardian"]           │
│                                                                     │
│  Level 2: FILE                                                      │
│    ├── file_summary: "Permission mixins for queryset filtering"    │
│    ├── purpose: "Ensures users only see data from their org"       │
│    ├── exports: ["FilteredQuerySetMixin", "DealershipRequired"]    │
│    └── full_code: <complete file content>                          │
│                                                                     │
│  Level 3: CLASS/FUNCTION                                            │
│    ├── symbol_summary: "Mixin that filters querysets by org"       │
│    ├── usage_pattern: "Add to view's inheritance chain"            │
│    ├── code: <class/function code>                                 │
│    └── methods: [ref→Level4]                                       │
│                                                                     │
│  Level 4: METHOD (for large classes)                                │
│    ├── method_summary: "Returns filtered queryset for user's org"  │
│    ├── parameters: ["self", "request"]                             │
│    ├── returns: "QuerySet filtered to user's organization"         │
│    └── code: <method code>                                         │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Chunk Types & Schema

### 1. Repository Summary Chunk
Created once per repo, updated on re-index.

```python
{
    "chunk_id": "repo_summary_{repo_id}",
    "type": "repo_summary",
    "repo_id": "kbhalerao/labcore",
    "content": """
    # labcore - Agricultural Laboratory Management System

    A Django-based platform for managing agricultural soil testing laboratories.

    ## Key Capabilities
    - Multi-tenant organization management with role-based access
    - Sample tracking from field to lab results
    - Geospatial field boundary management (PostGIS)
    - Workflow automation for lab processes
    - PDF report generation

    ## Architecture
    - Backend: Django 4.x with Django REST Framework
    - Database: PostgreSQL with PostGIS extension
    - Task Queue: Celery with Redis broker
    - Frontend: Svelte (separate repo: pinionfe)

    ## Key Modules
    - associates/: Organization and user management
    - samples/: Sample lifecycle and results
    - workflows/: Automated business processes
    - common/: Shared utilities and base classes
    """,
    "metadata": {
        "tech_stack": ["django", "postgresql", "postgis", "celery", "redis"],
        "primary_language": "python",
        "frameworks": ["django-rest-framework", "django-guardian"],
        "total_files": 450,
        "total_lines": 85000,
        "last_commit": "abc123",
        "generated_by": "qwen3-3b",
        "generation_date": "2025-11-24"
    },
    "embedding": [...]
}
```

### 2. Module Summary Chunk
One per Django app / Python package / major directory.

```python
{
    "chunk_id": "module_{repo_id}_{module_path}",
    "type": "module_summary",
    "repo_id": "kbhalerao/labcore",
    "module_path": "associates",
    "content": """
    # associates - Organization & Access Control Module

    ## Purpose
    Manages multi-tenant organization structure and user permissions.
    Every user belongs to an organization, and all data is scoped accordingly.

    ## Key Patterns
    - FilteredQuerySetMixin: Add to views to auto-filter by user's org
    - DealershipRequiredMixin: Require dealership context for access
    - UserPrivilegeResolution: Resolve effective permissions for a user

    ## Key Models
    - Organization: Top-level tenant entity
    - OrganizationUser: User's membership in an organization
    - Role: Named permission sets (Admin, Sampler, Viewer, etc.)

    ## Integration Points
    - Used by: samples, workflows, reports (all modules)
    - Depends on: django.contrib.auth, guardian
    """,
    "metadata": {
        "files": ["models.py", "views.py", "role_privileges.py", "serializers.py"],
        "key_exports": ["FilteredQuerySetMixin", "Organization", "Role"],
        "parent": "repo_summary_kbhalerao/labcore"
    },
    "embedding": [...]
}
```

### 3. File Chunk
Complete file with summary. This is the **primary search target**.

```python
{
    "chunk_id": "file_{repo_id}_{file_path}_{commit}",
    "type": "file",
    "repo_id": "kbhalerao/labcore",
    "file_path": "associates/role_privileges.py",
    "content": """
    # associates/role_privileges.py

    ## Summary
    Permission mixins for Django views that ensure users only access
    data belonging to their organization. Core to labcore's multi-tenancy.

    ## Key Classes
    - UserPrivilegeResolution: Base class for resolving user's effective org
    - FilteredQuerySetMixin: Mixin for ListView/ListAPIView to filter querysets
    - FilteredObjectMixin: Mixin for DetailView/RetrieveAPIView for single objects
    - DealershipRequiredMixin: Ensures user has dealership context

    ## Usage Pattern
    ```python
    class SampleListView(FilteredQuerySetMixin, ListAPIView):
        queryset = Sample.objects.all()
        # Queryset automatically filtered to user's organization
    ```

    ## Full Code
    ```python
    {full_file_content}
    ```
    """,
    "metadata": {
        "language": "python",
        "line_count": 245,
        "classes": ["UserPrivilegeResolution", "FilteredQuerySetMixin", ...],
        "functions": [],
        "imports": ["django.db.models", "guardian.shortcuts"],
        "commit_hash": "abc123",
        "parent_module": "module_kbhalerao/labcore_associates",
        "children": ["symbol_...FilteredQuerySetMixin", "symbol_...FilteredObjectMixin"],
        "quality_score": 0.85,
        "llm_enriched": true
    },
    "embedding": [...]
}
```

### 4. Symbol Chunk (Class/Function)
For important or large symbols that deserve individual indexing.

```python
{
    "chunk_id": "symbol_{repo_id}_{file_path}_{symbol_name}_{commit}",
    "type": "symbol",
    "repo_id": "kbhalerao/labcore",
    "file_path": "associates/role_privileges.py",
    "symbol_name": "FilteredQuerySetMixin",
    "symbol_type": "class",
    "content": """
    # FilteredQuerySetMixin

    ## Purpose
    A Django mixin that automatically filters querysets to only include
    objects belonging to the current user's organization. Essential for
    multi-tenant data isolation.

    ## How It Works
    1. Resolves user's organization via UserPrivilegeResolution
    2. Applies organization filter to the view's queryset
    3. Handles edge cases (superuser, no org, etc.)

    ## Key Methods
    - get_queryset(): Returns filtered queryset
    - get_organization(): Resolves current org from request

    ## Usage
    ```python
    class MyListView(FilteredQuerySetMixin, ListAPIView):
        queryset = MyModel.objects.all()
    ```

    ## Code
    ```python
    class FilteredQuerySetMixin(UserPrivilegeResolution):
        \"\"\"
        Mixin for filtering querysets by organization.
        Add to any ListView or ListAPIView.
        \"\"\"

        def get_queryset(self):
            qs = super().get_queryset()
            org = self.get_organization()
            if org and hasattr(qs.model, 'organization'):
                return qs.filter(organization=org)
            return qs

        def get_organization(self):
            # ... implementation
    ```
    """,
    "metadata": {
        "parent_file": "file_kbhalerao/labcore_associates/role_privileges.py",
        "start_line": 45,
        "end_line": 78,
        "methods": ["get_queryset", "get_organization"],
        "inherits": ["UserPrivilegeResolution"],
        "used_by": ["samples/views.py", "workflows/views.py"],  # Cross-ref
        "quality_score": 0.92
    },
    "embedding": [...]
}
```

### 5. Commit Chunk (unchanged from V1)
```python
{
    "chunk_id": "commit_{repo_id}_{commit_hash}",
    "type": "commit",
    "repo_id": "kbhalerao/labcore",
    "commit_hash": "abc123",
    "content": "fix: Ensure FilteredQuerySetMixin handles null organization gracefully",
    "metadata": {
        "author": "kbhalerao@example.com",
        "date": "2025-11-20",
        "files_changed": ["associates/role_privileges.py"]
    },
    "embedding": [...]
}
```

---

## LLM-Assisted Enrichment Pipeline

### Phase 1: Structure Analysis (No LLM)
Fast, deterministic extraction using tree-sitter.

```python
async def analyze_structure(file_path: Path, content: str) -> FileStructure:
    """Extract AST structure without LLM"""
    parser = get_parser(detect_language(file_path))
    tree = parser.parse(content.encode())

    return FileStructure(
        classes=[extract_class_info(node) for node in find_classes(tree)],
        functions=[extract_func_info(node) for node in find_functions(tree)],
        imports=extract_imports(tree),
        line_count=content.count('\n'),
        complexity_estimate=estimate_complexity(tree)
    )
```

### Phase 2: Importance Scoring (Heuristics + Optional LLM)
Decide which files/symbols need deep LLM enrichment.

```python
def calculate_importance(file_path: str, structure: FileStructure) -> float:
    """Score 0-1 for how important this file is to index deeply"""
    score = 0.0

    # Boost for key files
    if any(kw in file_path for kw in ['views', 'models', 'serializers', 'admin']):
        score += 0.3
    if 'test' in file_path.lower():
        score -= 0.2  # Tests are less critical for search
    if '__init__' in file_path:
        score -= 0.1

    # Boost for larger/more complex files
    if structure.line_count > 200:
        score += 0.2
    if len(structure.classes) > 2:
        score += 0.2

    # Boost for files with docstrings (developer thought it was important)
    if structure.has_module_docstring:
        score += 0.1

    return min(1.0, max(0.0, score))
```

### Phase 3: LLM Enrichment (Qwen3-3B)
For high-importance files, generate summaries.

```python
async def enrich_with_llm(
    file_path: str,
    content: str,
    structure: FileStructure,
    llm: LocalLLM
) -> EnrichedContent:
    """Use local LLM to generate semantic summaries"""

    prompt = f"""Analyze this Python file and provide:
1. A 2-3 sentence summary of what this file does
2. Key classes/functions and their purpose (1 line each)
3. Usage patterns (how would a developer use this?)
4. Integration points (what does this connect to?)

File: {file_path}

```python
{content[:8000]}  # Truncate for context window
```

Respond in this JSON format:
{{
    "summary": "...",
    "key_symbols": [
        {{"name": "ClassName", "purpose": "...", "importance": "high|medium|low"}}
    ],
    "usage_pattern": "...",
    "integrations": ["module1", "module2"]
}}
"""

    response = await llm.generate(prompt, temperature=0.3)
    return parse_enrichment(response)
```

### Phase 4: Cross-Reference Analysis
Build the graph of relationships.

```python
async def build_cross_references(repo_chunks: List[Chunk]) -> Dict[str, List[str]]:
    """Analyze import/usage patterns to find relationships"""

    references = {}

    for chunk in repo_chunks:
        if chunk.type == "file":
            # Find what this file imports
            for imp in chunk.metadata.get("imports", []):
                # Map import to chunk_id of the imported file
                target = find_chunk_for_import(imp, repo_chunks)
                if target:
                    references.setdefault(chunk.chunk_id, []).append(target)

    return references
```

---

## Chunk Quality Metrics

Every chunk gets a quality score (0-1) based on:

```python
def calculate_quality_score(chunk: Chunk) -> float:
    """Calculate quality score for a chunk"""
    score = 0.5  # Base score

    # Content quality
    if len(chunk.content) > 100:
        score += 0.1
    if chunk.metadata.get("has_docstring"):
        score += 0.1
    if chunk.metadata.get("llm_enriched"):
        score += 0.15

    # Metadata completeness
    required_fields = ["file_path", "language", "commit_hash"]
    present = sum(1 for f in required_fields if chunk.metadata.get(f))
    score += (present / len(required_fields)) * 0.1

    # Hierarchy completeness
    if chunk.metadata.get("parent_module"):
        score += 0.05
    if chunk.metadata.get("children"):
        score += 0.05

    # Penalize truncated content
    if "truncated" in chunk.content.lower():
        score -= 0.2

    return min(1.0, max(0.0, score))
```

---

## Incremental Improvement Strategy

### Audit Mode
Run without modifying, just report quality issues.

```python
async def audit_repo(repo_id: str, db: CouchbaseClient) -> AuditReport:
    """Analyze existing chunks for quality issues"""

    chunks = await db.get_all_chunks(repo_id)
    issues = []

    for chunk in chunks:
        # Check for empty/near-empty content
        if len(chunk.content.strip()) < 50:
            issues.append(QualityIssue(
                chunk_id=chunk.chunk_id,
                issue="empty_content",
                severity="high"
            ))

        # Check for missing summaries
        if chunk.type == "file" and not chunk.metadata.get("llm_enriched"):
            issues.append(QualityIssue(
                chunk_id=chunk.chunk_id,
                issue="missing_llm_summary",
                severity="medium"
            ))

        # Check for orphaned chunks (no parent reference)
        if chunk.type in ["file", "symbol"] and not chunk.metadata.get("parent_module"):
            issues.append(QualityIssue(
                chunk_id=chunk.chunk_id,
                issue="orphaned_chunk",
                severity="low"
            ))

        # Check for stale chunks (old commit)
        # ...

    return AuditReport(
        repo_id=repo_id,
        total_chunks=len(chunks),
        issues=issues,
        quality_distribution=calculate_distribution(chunks)
    )
```

### Selective Re-enrichment
Re-process only low-quality chunks.

```python
async def improve_low_quality_chunks(
    repo_id: str,
    min_quality: float = 0.6,
    db: CouchbaseClient,
    llm: LocalLLM
):
    """Find and re-enrich low-quality chunks"""

    low_quality = await db.query(f"""
        SELECT * FROM chunks
        WHERE repo_id = '{repo_id}'
        AND metadata.quality_score < {min_quality}
        ORDER BY metadata.quality_score ASC
        LIMIT 100
    """)

    for chunk in low_quality:
        # Re-enrich with LLM
        enriched = await enrich_with_llm(
            chunk.file_path,
            chunk.content,
            llm
        )

        # Update chunk
        chunk.content = build_enriched_content(chunk, enriched)
        chunk.metadata["llm_enriched"] = True
        chunk.metadata["quality_score"] = calculate_quality_score(chunk)

        await db.upsert(chunk)
```

---

## Synthetic Chunks (Future Foundation)

### Pattern Detection
Identify duplicated code patterns across repos.

```python
async def detect_patterns(repos: List[str], db: CouchbaseClient) -> List[Pattern]:
    """Find similar code patterns across repositories"""

    # Get all symbol chunks
    symbols = await db.query("""
        SELECT chunk_id, content, embedding, repo_id, symbol_name
        FROM chunks
        WHERE type = 'symbol'
    """)

    # Cluster by embedding similarity
    clusters = cluster_by_similarity(symbols, threshold=0.85)

    patterns = []
    for cluster in clusters:
        if len(cluster) > 2:  # Pattern appears 3+ times
            patterns.append(Pattern(
                instances=cluster,
                canonical_content=select_best_instance(cluster),
                occurrence_count=len(cluster)
            ))

    return patterns
```

### Synthetic Chunk Schema
```python
{
    "chunk_id": "pattern_{pattern_hash}",
    "type": "pattern",
    "content": """
    # Common Pattern: Filtered ListView Mixin

    ## Description
    A mixin pattern used across labcore, topsoil, and pinionbe
    for filtering querysets by organization.

    ## Canonical Implementation
    ```python
    class FilteredQuerySetMixin:
        def get_queryset(self):
            qs = super().get_queryset()
            org = self.get_organization()
            return qs.filter(organization=org) if org else qs
    ```

    ## Instances
    - kbhalerao/labcore: associates/role_privileges.py
    - ContinuumAgInc/topsoil2.0: associates/role_privileges.py
    - kbhalerao/pinionbe: core/mixins.py

    ## Variations
    - Some implementations add superuser bypass
    - Some use dealership instead of organization
    """,
    "metadata": {
        "pattern_type": "mixin",
        "instances": ["chunk_id_1", "chunk_id_2", "chunk_id_3"],
        "repos": ["labcore", "topsoil2.0", "pinionbe"],
        "similarity_score": 0.92
    },
    "embedding": [...]
}
```

---

## Implementation Phases

### Phase 1: Foundation (Week 1)
- [ ] Update chunk schema to support hierarchy
- [ ] Add quality_score field to all chunks
- [ ] Create audit tool to analyze current state
- [ ] Test tree-sitter on ingestion machine

### Phase 2: Hierarchical Indexing (Week 2)
- [ ] Implement module detection (Django apps, Python packages)
- [ ] Create repo_summary and module_summary generators
- [ ] Add parent/children references to chunks
- [ ] Update search to leverage hierarchy

### Phase 3: LLM Enrichment (Week 3)
- [ ] Integrate Qwen3-3B for file summarization
- [ ] Implement importance scoring
- [ ] Create enrichment pipeline with rate limiting
- [ ] Add minimax-m2 for complex analysis (optional)

### Phase 4: Quality Improvement (Week 4)
- [ ] Run full audit across all 94 repos
- [ ] Prioritize re-enrichment for low-quality chunks
- [ ] Implement cross-reference analysis
- [ ] Measure search quality improvements

### Phase 5: Synthetic Chunks (Future)
- [ ] Pattern detection algorithm
- [ ] Canonical instance selection
- [ ] Pattern chunk generation
- [ ] Search integration

---

## Success Metrics

1. **Search Relevance**: Top-5 results contain answer 80%+ of time (up from ~60%)
2. **Chunk Quality**: Average quality score > 0.75
3. **Coverage**: 100% of files have summaries
4. **RAG Accuracy**: No hallucinated file paths or implementations
5. **Pattern Detection**: Identify 50+ common patterns across repos

---

## Configuration

```python
# config.py additions

class ChunkingV2Config:
    # Hierarchy
    create_repo_summaries: bool = True
    create_module_summaries: bool = True
    max_hierarchy_depth: int = 4

    # LLM Enrichment
    llm_model: str = "qwen3-3b"  # or "minimax-m2" for complex
    llm_endpoint: str = "http://localhost:11434"
    enrich_threshold: float = 0.4  # Importance score to trigger LLM
    max_llm_calls_per_file: int = 5

    # Quality
    min_quality_score: float = 0.5
    reindex_below_quality: float = 0.6

    # Synthetic (future)
    enable_pattern_detection: bool = False
    pattern_similarity_threshold: float = 0.85
```

---

## Questions for Review

1. **LLM Budget**: How many files can we reasonably enrich? All 60k, or prioritize top 20%?
2. **Module Detection**: Use Django app detection, or folder heuristics, or both?
3. **Embedding Model**: Stay with current, or upgrade for better semantic matching?
4. **Re-index Strategy**: Full re-index, or incremental enrichment of existing?
5. **Pattern Scope**: Detect patterns within repos, across repos, or both?
