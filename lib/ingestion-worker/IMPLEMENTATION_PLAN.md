# Implementation Plan: New Chunker

**Goal**: Build metadata-first, file-based, async streaming chunker

## Current State Analysis

### What's Broken (code_parser.py:266-291)
```python
elif node.type == "class_definition":
    # ❌ Problem: Extracts ENTIRE class as one chunk
    code_text = content[node.start_byte:node.end_byte]
    code_text = self.truncate_chunk_text(...)  # Then truncates!

    chunks.append(CodeChunk(...))  # One chunk for whole class
```

**Issue**: No recursion into class body → methods never extracted individually

## The Fix: Step-by-Step

### Phase 1: Fix Python Class Chunking

**Replace lines 266-291 with:**

```python
elif node.type == "class_definition":
    # Extract class metadata
    class_name_node = node.child_by_field_name("name")
    class_name = class_name_node.text.decode("utf8") if class_name_node else "unknown"

    # Get class body
    body_node = node.child_by_field_name("body")

    if body_node:
        # 1. Create class header chunk (definition + docstring only)
        class_header = extract_class_header(node, content)
        chunks.append(CodeChunk(
            chunk_type="class_header",
            code_text=class_header,
            metadata={"class_name": class_name, ...}
        ))

        # 2. Extract each method separately
        for child in body_node.children:
            if child.type == "function_definition":
                method_chunk = extract_method(child, content, parent_class=class_name)
                chunks.append(method_chunk)
```

### Phase 2: Add Helper Functions

```python
def extract_class_header(node, content):
    """Get class def + docstring, not full body"""
    body_node = node.child_by_field_name("body")

    # Just the signature
    header_end = body_node.start_byte if body_node else node.end_byte
    header = content[node.start_byte:header_end]

    # Add docstring if exists
    docstring = extract_docstring(node)
    if docstring:
        header += f'\n    """{docstring}"""'

    return header

def extract_method(node, content, parent_class=None):
    """Extract single method with metadata"""
    name_node = node.child_by_field_name("name")
    method_name = name_node.text.decode("utf8") if name_node else "unknown"

    code_text = content[node.start_byte:node.end_byte]

    return CodeChunk(
        chunk_type="method",
        code_text=code_text,
        metadata={
            "method_name": method_name,
            "class_name": parent_class,
            ...
        }
    )
```

### Phase 3: Add File-Size Logic

```python
async def parse_file(file_path, ...):
    content = file_path.read_text()

    # Estimate token count
    char_count = len(content)
    estimated_tokens = char_count * 0.75  # ~0.75 tokens per char

    chunks = []

    # 1. Always create metadata chunk first
    metadata_chunk = create_metadata_chunk(file_path, content, git_metadata)
    chunks.append(metadata_chunk)

    # 2. Check file size
    if estimated_tokens < 6000:  # Small file
        # Keep whole file as one chunk
        chunks.append(CodeChunk(
            chunk_type="full_file",
            code_text=content,
            ...
        ))
    else:  # Large file
        # Split using tree-sitter
        parsed_chunks = parse_with_tree_sitter(content, language)
        chunks.extend(parsed_chunks)

    return chunks
```

### Phase 4: Add Junk File Filter

```python
SKIP_PATTERNS = [
    # Build artifacts
    "node_modules/", "dist/", "build/", "__pycache__/",
    ".next/", "target/", "vendor/",

    # Lockfiles
    "*lock.json", "*.lock", "go.sum",

    # Minified
    "*.min.js", "*.min.css", "*.map",

    # Generated
    "*generated*", "*codegen*", "*.g.dart",
]

def should_skip_file(file_path: Path) -> bool:
    # Check patterns
    path_str = str(file_path)
    for pattern in SKIP_PATTERNS:
        if fnmatch.fnmatch(path_str, f"*{pattern}*"):
            return True

    # Check file size
    if file_path.stat().st_size > 1_000_000:  # 1MB
        logger.warning(f"Skipping huge file: {file_path.name} ({file_path.stat().st_size} bytes)")
        return True

    return False
```

### Phase 5: Metadata-First Chunk

```python
def create_metadata_chunk(file_path, content, git_metadata):
    """First 200 lines + file summary"""
    lines = content.split('\n')
    preview_lines = lines[:200]
    preview = '\n'.join(preview_lines)

    # Extract module docstring if available
    docstring = extract_module_docstring(content)

    # Count symbols with tree-sitter
    tree = parse_tree(content)
    num_functions = count_nodes(tree, "function_definition")
    num_classes = count_nodes(tree, "class_definition")

    return CodeChunk(
        chunk_id=...,
        chunk_type="file_metadata",
        code_text=preview[:4000],  # Cap at 4k chars
        metadata={
            **git_metadata,
            "file_size_bytes": len(content),
            "file_size_tokens": int(len(content) * 0.75),
            "num_functions": num_functions,
            "num_classes": num_classes,
            "module_docstring": docstring,
        }
    )
```

### Phase 6: Rich Metadata

```python
def extract_rich_metadata(node, content, git_metadata):
    """Extract all metadata for a code node"""
    metadata = {**git_metadata}

    # Basic info
    metadata["start_line"] = node.start_point[0] + 1
    metadata["end_line"] = node.end_point[0] + 1
    metadata["char_count"] = node.end_byte - node.start_byte

    # Docstring
    docstring = extract_docstring(node)
    if docstring:
        metadata["docstring"] = docstring
        metadata["docstring_summary"] = docstring.split('\n')[0]  # First line

    # For functions/methods
    if node.type in ["function_definition", "function_declaration"]:
        metadata["function_name"] = extract_name(node)
        metadata["parameters"] = extract_parameters(node)
        metadata["decorators"] = extract_decorators(node)

        # Detect if it's a test
        if metadata["function_name"].startswith("test_"):
            metadata["is_test"] = True

    # For classes
    elif node.type in ["class_definition", "class_declaration"]:
        metadata["class_name"] = extract_name(node)
        metadata["base_classes"] = extract_base_classes(node)
        metadata["decorators"] = extract_decorators(node)

        # Detect Django models
        if "models.Model" in metadata.get("base_classes", []):
            metadata["is_django_model"] = True

    return metadata
```

## Testing Strategy

### 1. Unit Test: Class Chunking
```python
# test_chunking.py already exists
venv/bin/python3 test_chunking.py

# Expected output:
# TOTAL CHUNKS CREATED: 6
#   - top_level_function: 1 chunk
#   - TestClass.__init__: 1 chunk
#   - TestClass.method1: 1 chunk
#   - TestClass.method2: 1 chunk
#   - TestClass.method3: 1 chunk
#   - AnotherClass.another_method: 1 chunk
```

### 2. Integration Test: Small Repo
```python
# Test with tiny repo first
GITHUB_REPOS="test/tiny-repo" venv/bin/python3 worker.py

# Check:
# - All files processed?
# - Metadata chunks created?
# - No truncation warnings?
# - Chunk size distribution?
```

### 3. Production Test: labcore
```bash
# Full repo test
GITHUB_REPOS="kbhalerao/labcore" venv/bin/python3 worker.py

# Metrics to check:
# - Total chunks (expect 3-5x increase from method extraction)
# - Average chunk size (target: 1-5k tokens)
# - Zero truncation (no chunks >6k tokens)
# - Processing time (should be similar to current)
```

## Implementation Order

1. ✅ **Fix class chunking** (parsers/code_parser.py:266-291)
   - Extract methods individually
   - Test with test_chunking.py

2. **Add metadata chunk** (new function in code_parser.py)
   - create_metadata_chunk()
   - Test that every file gets metadata chunk

3. **Add junk filter** (new function in code_parser.py)
   - should_skip_file()
   - Test that node_modules/ skipped

4. **Add file-size logic** (modify parse_file())
   - Small files: whole file chunk
   - Large files: tree-sitter splitting

5. **Add rich metadata extraction** (enhance existing metadata)
   - Docstrings, decorators, parameters
   - Branch name from git

6. **Test end-to-end** (with real repos)
   - Verify chunk counts
   - Verify no truncation
   - Verify search quality

## Success Criteria

- ✅ test_chunking.py produces 6 chunks (not 3)
- ✅ No truncation warnings in logs
- ✅ Average chunk size 1-5k tokens
- ✅ Every file has metadata chunk
- ✅ Junk files (node_modules, .min.js) skipped
- ✅ labcore ingestion produces 50-75k chunks (3-5x current)

## After Chunker is Solid

**Then** we'll add:
- Async streaming pipeline (chunk → embed → upsert in parallel)
- Deduplication via chunk hash matching
- Performance monitoring

But first: **Get chunking right.**
