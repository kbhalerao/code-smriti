# Ingestion Pipeline Analysis

**Date**: 2025-11-20
**Status**: Current implementation review

## Current Architecture

### Pipeline Flow (worker.py)

```
1. Clone/Update Repository
   └─> git clone/pull

2. Parse All Files → Chunks (IN MEMORY)
   ├─> code_parser.parse_repository()  → all_code_chunks
   └─> doc_parser.parse_repository()   → all_doc_chunks

3. Filter by File Changes (if enabled)
   └─> filter_chunks_by_file_changes()  → filtered_chunks

4. Extract Commit Metadata
   └─> commit_parser.extract_commits()  → commit_chunks

5. Generate Embeddings + Stream Write
   ├─> embedding_generator.generate_embeddings()  (batch_size=128)
   └─> batch_callback() → db.batch_upsert()
```

## Context Capacity Analysis

### Memory Bottlenecks

#### ⚠️ **CRITICAL: Step 2 - Parse All Files**

**Problem**: All chunks loaded into memory before filtering

```python
# worker.py:235-241
all_code_chunks = await self.code_parser.parse_repository(repo_path, repo_id)  # ALL chunks
logger.info(f"Parsed {len(all_code_chunks)} total code chunks")

all_doc_chunks = await self.doc_parser.parse_repository(repo_path, repo_id)   # ALL chunks
logger.info(f"Parsed {len(all_doc_chunks)} total document chunks")

# THEN filtering happens (if enabled)
code_chunks, stats = self.filter_chunks_by_file_changes(all_code_chunks, ...)
```

**Impact for Large Repos:**

Example: `kbhalerao/labcore` (improved chunking)
- **Before fix**: ~3k chunks (classes truncated)
- **After fix**: ~15-20k chunks (methods extracted individually)
- **Memory per chunk**: ~500 bytes (metadata + code_text)
- **Total memory**: 20,000 chunks × 500 bytes = **~10 MB**

Still manageable, but what about bigger repos?

Example: Hypothetical large repo
- 5,000 files
- Average 67 chunks/file (like gis.py)
- **Total chunks**: 335,000 chunks
- **Memory**: 335k × 500 bytes = **~168 MB** (just for chunks)
- **With embeddings**: 335k × (500 + 768×4) bytes = **~1.2 GB**

#### ✓ **GOOD: Step 5 - Streaming Writes**

**Current implementation** (worker.py:282-299):

```python
# Callback writes each batch immediately after embedding
async def write_batch(batch_chunks):
    result = await self.db.batch_upsert(batch_chunks)
    total_success += result['success']
    logger.info(f"→ Stored batch: {result['success']} chunks")

# Generate embeddings and stream write batches
await self.embedding_generator.generate_embeddings(
    all_chunks,
    batch_callback=write_batch  # ← Streams to DB
)
```

**Benefits**:
- Chunks written immediately after embedding
- No accumulation of embedded chunks
- Memory freed after each batch

#### ✓ **GOOD: Batching Strategy**

**Embedding batch size**: 128 chunks (local_generator.py:137)

```python
batch_size: int = 128  # Safe with 6K char max per chunk
```

**Why this works**:
- 128 chunks × 6KB max = 768 KB per batch
- Nomic model limit: 8192 tokens (~6K chars)
- GPU/MPS can handle this comfortably

**Batch processing** (local_generator.py:158-188):
- Processes chunks 128 at a time
- Frees memory after each batch
- Streams to DB immediately

### Incremental Update Optimization

**File change detection** (worker.py:139-216):

```python
def filter_chunks_by_file_changes(chunks, repo_id, repo_path):
    # 1. Get all stored files + commits in ONE query
    stored_file_commits = self.db.get_repo_file_commits(repo_id)

    # 2. Build current file -> commit mapping
    current_file_commits = {...}

    # 3. Use set differences to categorize files
    files_new = current_files - stored_files
    files_deleted = stored_files - current_files
    files_updated = {f for f in common if commit_changed(f)}

    # 4. Delete old chunks for updated files
    # 5. Return only new + updated chunks
```

**Efficiency**:
- ✓ Single query for all files (not N queries)
- ✓ Set-based diff (fast)
- ✓ Deletes old chunks before inserting new ones

**But** still loads ALL chunks before filtering!

## Identified Issues

### 1. Memory Spike During Parsing

**Current flow**:
```
Parse all files → Load ALL chunks into memory → Filter → Process
```

**Better flow**:
```
Parse file → Filter → Process → Next file
(OR)
Parse batch of files → Filter → Process → Next batch
```

### 2. No File-Level Junk Filtering

**Current**: Parses ALL files, including:
- `node_modules/` (thousands of files)
- `.min.js` (minified code)
- `package-lock.json` (huge lockfiles)
- Generated files

**Impact**: Wastes memory + time parsing junk

### 3. No Size-Based Chunking Logic

**Current**: All files split using tree-sitter, even tiny ones

**Better**:
- Files <6k tokens → Keep whole file as one chunk
- Files >6k tokens → Split with tree-sitter

### 4. Synchronous Parsing

**Current**: `await code_parser.parse_repository()` is sequential

**Better**: Parse multiple files concurrently

```python
# Current (sequential)
for file in files:
    chunks = await parse_file(file)
    all_chunks.extend(chunks)

# Better (concurrent)
tasks = [parse_file(f) for f in files]
results = await asyncio.gather(*tasks)
all_chunks = [c for chunks in results for c in chunks]
```

## Capacity Limits

### Current Configuration

| Limit | Value | Source |
|-------|-------|--------|
| Max chunk size | 6,000 chars | `local_generator.py:121` |
| Embedding batch size | 128 chunks | `local_generator.py:137` |
| Memory per batch | ~768 KB | 128 × 6KB |
| Couchbase batch size | ? | Need to check `storage/couchbase_client.py` |

### Estimated Capacity

**Small repo** (100 files, ~500 chunks):
- Memory: ~250 KB
- Time: <1 min

**Medium repo** (1,000 files, ~5,000 chunks):
- Memory: ~2.5 MB (chunks only)
- Time: ~5-10 min
- Bottleneck: Embedding generation

**Large repo** (10,000 files, ~50,000 chunks):
- Memory: ~25 MB (chunks only)
- Time: ~30-60 min
- Bottleneck: Parsing + Embedding

**Huge repo** (100,000 files, ~500,000 chunks):
- Memory: ~250 MB (chunks only) + ~1.5 GB (with embeddings)
- Time: Several hours
- **Risk**: OOM during parsing phase

### MPS GPU Memory

**Apple Silicon** (M1/M2/M3):
- Shared memory pool (16GB typical)
- MPS backend for PyTorch
- Batch size 128 is safe

**Potential issue**: Large batches + other apps could OOM

## Recommendations

### Priority 1: Stream Parsing

**Replace**: Load all → Filter → Process
**With**: Stream file → Filter → Process → Next

```python
async def stream_parse_repository(repo_path, repo_id):
    """Parse files one at a time, yielding chunks"""
    for file_path in walk_files(repo_path):
        # Skip junk files immediately
        if should_skip_file(file_path):
            continue

        # Parse single file
        chunks = await parse_file(file_path)

        # Yield chunks immediately
        yield chunks
```

### Priority 2: Junk File Filter

Add `SKIP_PATTERNS` before parsing:

```python
SKIP_PATTERNS = [
    "node_modules/", "dist/", "build/", "__pycache__/",
    "*.min.js", "*.min.css", "*.map",
    "package-lock.json", "yarn.lock", "Cargo.lock",
    "*generated*", "*.pb.go",
]
```

### Priority 3: Concurrent Parsing

Parse multiple files concurrently:

```python
async def parse_repository_concurrent(repo_path, repo_id, max_concurrent=10):
    files = list_files(repo_path)

    # Process in batches of max_concurrent files
    for i in range(0, len(files), max_concurrent):
        batch = files[i:i + max_concurrent]
        tasks = [parse_file(f) for f in batch]
        results = await asyncio.gather(*tasks)

        for chunks in results:
            yield chunks
```

### Priority 4: File-Size Chunking

```python
async def parse_file(file_path, ...):
    content = file_path.read_text()
    estimated_tokens = len(content) * 0.75

    if estimated_tokens < 6000:
        # Small file: keep whole
        return [create_whole_file_chunk(content)]
    else:
        # Large file: split with tree-sitter
        return split_with_tree_sitter(content)
```

## Success Metrics

After improvements, we should achieve:

- ✓ **Constant memory usage** (not O(n) with file count)
- ✓ **Skip junk files** (50-80% reduction in parsing time)
- ✓ **Concurrent parsing** (5-10x faster for large repos)
- ✓ **Stream processing** (can handle repos of any size)

## Next Steps

1. Implement junk file filter (quick win)
2. Add file-size logic (quick win)
3. Refactor to streaming architecture (bigger change)
4. Add concurrent file parsing (performance boost)
5. Monitor memory usage with large repos
