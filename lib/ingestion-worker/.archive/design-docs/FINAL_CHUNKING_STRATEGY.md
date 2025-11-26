# CodeSmriti Chunking Strategy (Final)

**Status**: Implementation Ready
**Last Updated**: 2025-11-20
**Principle**: Metadata-first, file-based, semantically-coherent chunks

## Core Philosophy

**80% of questions are answered by file-level context:**
- "What file handles authentication?"
- "What changed in the last commit?"
- "What does views.py do?"

**20% need drill-down to specific functions/classes.**

## The Strategy

### 1. Metadata-First Chunk (Per File)

**Every file gets a metadata chunk (~1-2k tokens):**

```python
{
  "chunk_type": "file_metadata",
  "file_path": "myapp/views.py",
  "language": "python",
  "metadata": {
    "commit_hash": "abc123",
    "commit_message": "Add user authentication views",
    "commit_author": "developer@example.com",
    "commit_date": "2024-11-20",
    "branch": "main",
    "file_size_bytes": 12500,
    "file_size_tokens": 3000,  # Estimate
    "num_functions": 8,
    "num_classes": 2,
  },
  "code_text": """
# Content: First 200 lines OR module docstring + imports + top-level signatures
# This gives overview without full implementation
\"\"\"
User authentication views.

Handles login, logout, password reset, and profile management.
\"\"\"

from django.contrib.auth import authenticate, login
from django.shortcuts import render, redirect
...

class LoginView(View):
    \"\"\"Handle user login\"\"\"
    def get(self, request): ...
    def post(self, request): ...

class ProfileView(View):
    \"\"\"User profile management\"\"\"
    ...
"""
}
```

**This chunk answers:**
- ✅ What file is this?
- ✅ What language?
- ✅ What's the recent commit about?
- ✅ What's the file's purpose? (docstring)
- ✅ What are the main components? (class/function signatures)

**Search benefit:** "show me authentication code" → finds this chunk → user sees overview → drills into specific function if needed.

### 2. File-Based Chunking

**For small files (<6k tokens / ~4500 chars):**
- **One file = one chunk** (in addition to metadata chunk)
- Keep entire file together
- Most files are small (80% of files <500 lines)

**For large files (>6k tokens):**
- **Split on top-level declarations** using tree-sitter
- Each function/class = one chunk
- Keep function + docstring + implementation together
- Never split mid-function

### 3. Language-Aware Splitting (Tree-sitter)

Use tree-sitter directly (not LSP - simpler, faster):

```python
from tree_sitter_languages import get_parser

parser = get_parser("python")
tree = parser.parse(content.encode())

# Query for top-level nodes
top_level_nodes = [
    node for node in tree.root_node.children
    if node.type in ["function_definition", "class_definition"]
]

for node in top_level_nodes:
    code_text = content[node.start_byte:node.end_byte]

    if len(code_text) < 6000:  # ~4500 tokens
        # Create chunk
        chunks.append(CodeChunk(...))
    else:
        # Split large class into methods
        if node.type == "class_definition":
            body = node.child_by_field_name("body")
            for method in body.children:
                if method.type == "function_definition":
                    # Each method = chunk
                    chunks.append(CodeChunk(...))
```

**Split boundaries:**
- Python: `function_definition`, `class_definition`
- JavaScript: `function_declaration`, `class_declaration`, `arrow_function`
- Svelte: `<script>`, template blocks, `<style>`
- CSS: rule blocks, `@media` queries
- Markdown: H1/H2 headings

### 4. Filter Junk Files

**Skip these patterns:**
```python
SKIP_PATTERNS = [
    # Build/generated
    "node_modules/", ".next/", "dist/", "build/", "__pycache__/",
    ".git/", ".svn/", "vendor/", "target/",

    # Lockfiles
    "package-lock.json", "yarn.lock", "Cargo.lock", "poetry.lock",
    "Pipfile.lock", "Gemfile.lock", "go.sum",

    # Minified/compiled
    "*.min.js", "*.min.css", "*.map", "*.wasm",
    ".bundle.js", ".chunk.js",

    # Binary/media
    "*.png", "*.jpg", "*.gif", "*.ico", "*.pdf",
    "*.woff", "*.woff2", "*.ttf", "*.eot",

    # IDE/system
    ".DS_Store", "Thumbs.db", ".idea/", ".vscode/",
    "*.pyc", "*.pyo", "*.so", "*.dll", "*.exe",

    # Generated code markers
    "*generated*", "*codegen*", "*.g.dart", "*.pb.go",
]

def should_skip(file_path: Path) -> bool:
    # Check patterns
    for pattern in SKIP_PATTERNS:
        if fnmatch.fnmatch(str(file_path), f"*{pattern}"):
            return True

    # Check file size (skip huge files)
    if file_path.stat().st_size > 1_000_000:  # 1MB
        return True

    # Check if looks like generated code
    if file_path.name.startswith(".") and file_path.suffix in [".cache", ".lock"]:
        return True

    return False
```

### 5. Documentation Priority

**Markdown/docs get special treatment:**
- Keep whole if <8k tokens (most docs are)
- If larger, split on H1/H2 headings
- Allow overlap (last 200 chars of chunk N = first 200 of chunk N+1)
- Priority files: README.md, CONTRIBUTING.md, docs/, API.md

```python
def chunk_markdown(content: str) -> List[str]:
    """Split Markdown by headings, preserve context"""
    sections = split_on_headings(content)  # H1, H2

    chunks = []
    for i, section in enumerate(sections):
        if len(section) < 8000:
            chunks.append(section)
        else:
            # Split long section, add overlap
            sub_chunks = split_with_overlap(section, size=6000, overlap=200)
            chunks.extend(sub_chunks)

    return chunks
```

### 6. Chunk Metadata Schema

**Every chunk includes:**

```python
{
    # Identity
    "chunk_id": "sha256_hash",  # Deterministic ID
    "chunk_type": "file_metadata" | "code" | "document",

    # Location
    "repo_id": "owner/repo",
    "file_path": "myapp/views.py",
    "start_line": 42,
    "end_line": 68,

    # Content
    "code_text": "...",  # Actual code/content
    "language": "python",

    # Structure (from tree-sitter)
    "symbol_kind": "function" | "class" | "method" | "module",
    "symbol_name": "LoginView",
    "parent_symbol": "AuthenticationModule",  # If nested

    # Git metadata
    "commit_hash": "abc123",
    "commit_message": "Add user authentication",
    "commit_author": "dev@example.com",
    "commit_date": "2024-11-20",
    "branch": "main",

    # Semantic
    "docstring": "Handle user login flow",  # Extracted docstring
    "decorators": ["@login_required"],  # Python decorators
    "imports": ["django.contrib.auth"],  # Dependencies

    # Size
    "char_count": 1234,
    "estimated_tokens": 924,

    # Embedding
    "embedding": [0.123, -0.456, ...],  # 768-dim vector
}
```

## Implementation

### Step 1: Parse File with Tree-sitter

```python
async def parse_file(file_path: Path, repo_path: Path, repo_id: str):
    # Check if should skip
    if should_skip(file_path):
        return []

    # Read content
    content = file_path.read_text()
    language = detect_language(file_path)

    # Get git metadata
    git_metadata = get_git_metadata(file_path, repo_path)

    chunks = []

    # 1. Create metadata chunk
    metadata_chunk = create_metadata_chunk(
        file_path, content, language, git_metadata
    )
    chunks.append(metadata_chunk)

    # 2. Check file size
    estimated_tokens = len(content) * 0.75  # ~0.75 tokens per char

    if estimated_tokens < 6000:
        # Small file: one chunk
        chunks.append(CodeChunk(
            chunk_type="code",
            code_text=content,
            ...
        ))
    else:
        # Large file: split with tree-sitter
        parser = get_parser(language)
        tree = parser.parse(content.encode())

        top_level_nodes = extract_top_level_nodes(tree, language)

        for node in top_level_nodes:
            node_text = content[node.start_byte:node.end_byte]

            chunks.append(CodeChunk(
                chunk_type=node.type,  # function, class, etc.
                code_text=node_text,
                symbol_name=extract_name(node),
                docstring=extract_docstring(node),
                start_line=node.start_point[0],
                end_line=node.end_point[0],
                ...
            ))

    return chunks
```

### Step 2: Create Metadata Chunk

```python
def create_metadata_chunk(file_path, content, language, git_metadata):
    """First 200 lines + summary info"""

    lines = content.split('\n')
    preview_lines = lines[:200]

    # Extract module docstring if available
    docstring = extract_module_docstring(content, language)

    # Get top-level signatures
    signatures = extract_signatures(content, language)

    # Build metadata preview
    preview = ""
    if docstring:
        preview += f'"""{docstring}"""\n\n'

    preview += "\n".join(preview_lines)

    # Count symbols
    tree = parse_tree(content, language)
    num_functions = count_nodes(tree, "function_definition")
    num_classes = count_nodes(tree, "class_definition")

    return CodeChunk(
        chunk_type="file_metadata",
        code_text=preview[:4000],  # Cap at 4k chars
        metadata={
            **git_metadata,
            "file_size_bytes": len(content),
            "file_size_tokens": int(len(content) * 0.75),
            "num_functions": num_functions,
            "num_classes": num_classes,
            "symbols": signatures[:10],  # Top 10 symbols
        }
    )
```

### Step 3: Language-Specific Node Types

```python
TOP_LEVEL_NODE_TYPES = {
    "python": ["function_definition", "class_definition", "import_statement"],
    "javascript": ["function_declaration", "class_declaration", "lexical_declaration"],
    "typescript": ["function_declaration", "class_declaration", "interface_declaration"],
    "go": ["function_declaration", "type_declaration", "method_declaration"],
    "rust": ["function_item", "struct_item", "impl_item", "trait_item"],
    "swift": ["function_declaration", "class_declaration", "struct_declaration"],
    "java": ["method_declaration", "class_declaration", "interface_declaration"],
}

def extract_top_level_nodes(tree, language):
    """Get top-level declarations from AST"""
    node_types = TOP_LEVEL_NODE_TYPES.get(language, [])

    return [
        node for node in tree.root_node.children
        if node.type in node_types
    ]
```

## Benefits

### 1. Fast Initial Search
Query: "authentication code"
→ Finds metadata chunks with "authentication" in commit/docstring
→ User sees file overview
→ Can drill into specific function if needed

### 2. Semantic Coherence
- Functions kept whole (not split mid-implementation)
- Class + all methods available together
- Docstrings preserved with code

### 3. Token Efficiency
- Most chunks 1-5k tokens (optimal for embedding)
- No wasted tokens on junk files
- Metadata chunk is lightweight but informative

### 4. Language Agnostic
- Works for 20+ languages via tree-sitter
- Easy to add new languages (just add node types)
- No custom parsing per language

### 5. Git-Aware
- Every chunk knows its commit context
- Recent changes prioritized
- Can filter by branch/author/date

## Examples

### Small File (views.py, 150 lines)

**2 chunks:**
1. Metadata chunk (file path + commit + first 150 lines)
2. Full file chunk (entire file)

**Search**: "login view" → finds metadata → user sees overview + full code

### Large File (models.py, 2000 lines, 30 classes)

**31 chunks:**
1. Metadata chunk (file path + commit + first 200 lines + all class signatures)
2-31. One chunk per class (each with docstring + methods)

**Search**: "User model" → finds metadata + User class chunk

### Documentation (README.md, 1000 lines)

**6 chunks:**
1. Metadata chunk
2-6. Split by H1 headings with 200-char overlap

**Search**: "installation" → finds Installation section chunk

## Migration from Current

1. Keep existing tree-sitter code
2. Add metadata chunk generation
3. Add file-size check (whole file vs split)
4. Add junk file filtering
5. Test with labcore repo
6. Measure chunk size distribution
7. Deploy!

## Success Metrics

- **Average chunk size**: 1-5k tokens (target: 2-3k)
- **Chunks per file**:
  - Small files (<500 lines): 2 chunks (metadata + full)
  - Large files (>500 lines): 1 metadata + N top-level nodes
- **Search relevance**: Metadata chunks answer 80% of queries
- **Zero truncation**: No chunks >8k tokens
- **Language coverage**: Works for Python, JS, TS, Go, Rust, Swift, Java automatically

## Tree-sitter Quick Reference

```python
from tree_sitter_languages import get_parser

# Supported out of box:
parsers = {
    "python": get_parser("python"),
    "javascript": get_parser("javascript"),
    "typescript": get_parser("typescript"),
    "go": get_parser("go"),
    "rust": get_parser("rust"),
    "java": get_parser("java"),
    "swift": get_parser("swift"),
    "c": get_parser("c"),
    "cpp": get_parser("cpp"),
    "ruby": get_parser("ruby"),
    "php": get_parser("php"),
    "c_sharp": get_parser("c_sharp"),
    # ... 20+ more
}
```

## This is the way.

Simpler, faster, more practical than LSP.
Tree-sitter gives us just enough parsing.
Metadata-first gives us 80% hit rate on first chunk.
File-based keeps things coherent.
