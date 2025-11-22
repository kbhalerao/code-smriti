# Async File-Atomic Pipeline Design

## Current Architecture (Problematic)

```
1. Parse ALL files → all_chunks (MEMORY SPIKE)
2. Filter chunks (if incremental updates)
3. Generate embeddings for ALL chunks
   └─> Batch by 128 → embed → callback → upsert
4. Extract + embed commits
```

**Problems:**
- All files loaded into memory before processing
- No parallelism between parsing and embedding
- Memory scales with repository size
- Long wait before seeing any results

## Proposed Architecture (File-Atomic + Async)

```
┌─────────────────────────────────────────┐
│ Setup Phase                             │
├─────────────────────────────────────────┤
│ 1. Clone/update repository              │
│ 2. Get all file → commit mappings (DB)  │  <-- Single query
│ 3. List all files to process            │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ File Processing (Async, Concurrent)     │
├─────────────────────────────────────────┤
│ For each file (limit 10 concurrent):    │
│   1. Parse file → chunks                 │  <-- CPU-bound
│   2. Check if changed (vs cache)         │
│   3. If changed:                         │
│      a. Delete old chunks (if update)    │  <-- Async I/O
│      b. Embed chunks (batch if needed)   │  <-- GPU-bound
│      c. Upsert chunks                    │  <-- Async I/O
│   4. Collect commit metadata             │
└─────────────────────────────────────────┘
           ↓
┌─────────────────────────────────────────┐
│ Commit Processing                       │
├─────────────────────────────────────────┤
│ 1. Deduplicate commits                  │
│ 2. Embed commit messages                │
│ 3. Upsert commits                       │
└─────────────────────────────────────────┘
```

## Benefits

1. **Constant Memory**: Only file-level chunks in memory at any time
2. **True Streaming**: Results appear as files complete
3. **Async Concurrency**: Parse next file while embedding current file
4. **Atomic Failures**: File-level retry/error boundaries
5. **Scalable**: Can handle unlimited repository size

## Async Opportunities Analysis

### CPU-Bound Operations (Threading)
- **File parsing** (tree-sitter, frontmatter, etc.)
  - Run in thread pool via `asyncio.to_thread()`
  - Allows I/O operations to proceed while parsing

### GPU-Bound Operations (Sequential per batch)
- **Embedding generation** (`model.encode()`)
  - Not truly parallelizable on single GPU
  - But file-level concurrency means next file parses while this embeds
  - Keep existing batch logic (128 chunks/batch)

### I/O-Bound Operations (Async)
- **Database operations** (upsert, query, delete)
  - Already async via Couchbase SDK
  - Multiple DB operations can run concurrently

### Concurrency Model

```python
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Limit concurrent file processing (too many = memory bloat)
semaphore = asyncio.Semaphore(10)  # Process 10 files at once

# Thread pool for CPU-bound parsing
executor = ThreadPoolExecutor(max_workers=4)

async def process_file_atomic(file_path):
    async with semaphore:  # Limit concurrency
        # 1. Parse (CPU-bound) - run in thread
        chunks = await asyncio.to_thread(parse_file, file_path)

        # 2. Check if changed (I/O-bound) - async
        if not await file_needs_processing(chunks):
            return None

        # 3. Delete old chunks (I/O-bound) - async
        await delete_old_chunks(file_path)

        # 4. Embed (GPU-bound) - run in thread to not block event loop
        await asyncio.to_thread(embed_chunks, chunks)

        # 5. Upsert (I/O-bound) - async
        result = await db.batch_upsert(chunks)

        return chunks

# Process all files concurrently (up to semaphore limit)
tasks = [process_file_atomic(f) for f in files]
results = await asyncio.gather(*tasks, return_exceptions=True)
```

## Incremental Update Strategy

**Current (inefficient):**
```python
# Load ALL chunks, then filter
all_chunks = parse_all_files()
filtered = filter_changed(all_chunks)  # Wasted parsing
```

**New (file-level check):**
```python
# Get file→commit cache once at start
file_commits = await db.get_repo_file_commits(repo_id)

# Check each file individually
async def process_file_atomic(file_path):
    current_commit = get_file_commit(file_path)
    cached_commit = file_commits.get(file_path)

    if current_commit == cached_commit:
        return None  # Skip unchanged file

    # File changed - process it
    chunks = await parse_file(file_path)
    ...
```

## Error Handling

**File-level atomicity means**:
- If file A fails, files B, C, D continue
- Retry logic at file level
- Partial progress is saved (completed files)

```python
results = await asyncio.gather(*tasks, return_exceptions=True)

for file_path, result in zip(files, results):
    if isinstance(result, Exception):
        logger.error(f"Failed to process {file_path}: {result}")
    else:
        logger.info(f"✓ Processed {file_path}")
```

## Performance Comparison

**Current Pipeline (Sequential)**:
```
Parse all files (5 min) → Embed all (10 min) → Upsert all (2 min)
Total: 17 minutes (no overlap)
```

**Async Pipeline (Concurrent)**:
```
File 1: [Parse 5s][Embed 10s][Upsert 2s] = 17s
File 2:     [Parse 5s][Embed 10s][Upsert 2s] = 17s (starts at 5s)
File 3:         [Parse 5s][Embed 10s][Upsert 2s] = 17s (starts at 10s)
...
Total: ~5 minutes (with 10 concurrent files)
```

**Expected speedup: 3-4x for large repos**

## Implementation Plan

### Phase 1: File-Atomic Processing (Core)
1. Create `process_file_atomic()` function
2. Implement file-level incremental check
3. Add async semaphore for concurrency control

### Phase 2: Async Parsing
1. Move parsing to thread pool
2. Update parsers to be thread-safe

### Phase 3: Concurrent File Processing
1. Use `asyncio.gather()` for concurrent files
2. Collect commits across all files
3. Process commits after files complete

### Phase 4: Testing & Optimization
1. Test with small repo (validate correctness)
2. Test with large repo (validate performance)
3. Tune concurrency limits (semaphore value)
4. Monitor memory usage

## Configuration

```python
# config.py additions
class WorkerConfig:
    # File processing concurrency
    max_concurrent_files: int = 10  # Process N files at once
    max_parsing_threads: int = 4     # Thread pool for CPU-bound parsing

    # Existing
    embedding_batch_size: int = 128  # Unchanged
```

## Migration Strategy

**Option 1: Feature Flag**
- Add `enable_async_pipeline` config flag
- Keep both implementations during transition
- Gradual rollout

**Option 2: Direct Replacement**
- Replace existing pipeline entirely
- Faster iteration, cleaner code
- More risk

**Recommendation: Option 2** (Direct replacement)
- Code is well-tested
- Benefits are clear
- Easier to maintain one implementation

## Success Metrics

1. **Memory**: Constant regardless of repo size
2. **Speed**: 3-4x faster for repos >1000 files
3. **Reliability**: File-level failures don't stop processing
4. **Observability**: Progress visible as files complete

## Next Steps

1. Write design doc ✓
2. Implement `process_file_atomic()`
3. Test with small repo
4. Replace worker.py pipeline
5. Test with labcore (production validation)
