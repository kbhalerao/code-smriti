# CodeSmriti Chunking Strategy

**Status**: Design Phase
**Last Updated**: 2025-11-20
**Current Implementation**: ❌ BROKEN (see "Current State" below)

## Executive Summary

CodeSmriti must chunk code, documentation, and commits into semantically meaningful units that fit within embedding model constraints (Nomic: 8192 tokens ≈ 6000 chars) while preserving searchability and context.

## Current State

### What's Working
- ✅ Tree-sitter AST parsing (Python, JavaScript, TypeScript)
- ✅ Top-level function extraction
- ✅ Git metadata extraction (commit hash, author, date)
- ✅ Document parsing (Markdown, frontmatter)

### What's Broken
- ❌ **Class chunking**: Entire classes ingested as single chunks
  - Example: `ClientViewTests` = 88,868 chars → truncated to 6,000 chars (90% loss)
  - **Root cause**: Only iterates `root.children`, never recursing into class bodies
  - **Impact**: Methods inside classes are NOT extracted individually
- ❌ **Truncation strategy**: Keeps first 4500 + last 1400 chars, loses middle context
- ❌ **No docstring prioritization**: Class/method docstrings buried in truncated content
- ❌ **Incomplete metadata**: Missing parent class, decorators, type hints, git branch
- ❌ **Svelte/HTML/CSS ignored**: Files with `.svelte`, `.html`, `.css` extensions completely skipped
- ❌ **No branch tracking**: Not recording which git branch was cloned/ingested

### Test Proof
```bash
cd /Users/kaustubh/Documents/code/code-smriti/lib/ingestion-worker
venv/bin/python3 test_chunking.py
```

**Expected**: 6 chunks (1 per method)
**Actual**: 3 chunks (classes lumped together)

## Design Goals

### 1. Semantic Boundaries
Chunks must align with logical code units:
- Python: modules → classes → methods → properties
- JavaScript/TypeScript: modules → classes → methods, standalone functions
- Svelte: script blocks → template blocks → style blocks
- HTML: semantic sections, components
- CSS: rulesets, media queries

### 2. Context Preservation
Each chunk must be independently searchable:
- **Hierarchical metadata**: `module.Class.method` path
- **Scope context**: Parent class name, decorators, type hints
- **Docstrings**: Prioritized for semantic understanding
- **Location**: File path, line numbers

### 3. Size Constraints
- **Hard limit**: 6,000 characters (~4,500-7,500 tokens)
- **No truncation**: If oversized, split further (e.g., split large methods)
- **Overlap allowed**: For docs, overlapping chunks preserve continuity

### 4. Language-Specific Intelligence

#### Python/Django
- **Module-level**: Imports, module docstring (1 chunk)
- **Class-level**: Class definition + docstring (1 chunk)
- **Method-level**: Each method (1 chunk each)
  - Include: Decorators, type hints, docstring, signature, body
  - Django models: Each field = 1 chunk (with help_text, validators)
- **Properties**: `@property` methods (1 chunk each)
- **Nested classes**: Recurse and extract separately

#### JavaScript/TypeScript
- **Module-level**: Imports, exports, module comment
- **Class-level**: Class definition + JSDoc
- **Method-level**: Each method (constructors, getters, setters)
- **Functions**: Top-level and arrow functions
- **React/Svelte components**: Component definition + props interface

#### Svelte (.svelte)
Svelte components have three distinct sections that must be parsed separately:

**Script Block** (`<script>` or `<script lang="ts">`):
- Extract entire script content → parse as JavaScript/TypeScript
- Apply same chunking rules as JS/TS (functions, classes, exports)
- Tag with `section: "script"` in metadata

**Template Block** (HTML-like markup):
- Extract template content (everything outside script/style)
- Chunk by logical component boundaries:
  - Each top-level block element (`{#if}`, `{#each}`, `{#await}`)
  - Each major semantic section (`<header>`, `<main>`, `<footer>`)
  - Slots and component instances
- Preserve reactive statements (`$:`) in context
- Tag with `section: "template"` in metadata

**Style Block** (`<style>` or `<style lang="scss">`):
- Extract entire style content → parse as CSS/SCSS
- Apply CSS chunking rules (see below)
- Tag with `section: "style"`, `scoped: true/false` in metadata

**Example Metadata**:
```python
{
    "file_path": "components/Button.svelte",
    "section": "script",  # or "template", "style"
    "chunk_type": "function",
    "function_name": "handleClick",
    "is_svelte_component": true
}
```

#### HTML (.html, .htm)
- **Semantic sections**: Each `<header>`, `<main>`, `<footer>`, `<article>`, `<section>` → 1 chunk
- **Custom elements**: Web components, template tags → 1 chunk per component
- **Inline scripts**: Extract to script chunks with `inline: true` metadata
- **Inline styles**: Extract to style chunks with `inline: true` metadata
- **Forms**: Each `<form>` element → 1 chunk
- **Large sections**: Split at logical boundaries (div containers, lists)

**Example Metadata**:
```python
{
    "file_path": "templates/index.html",
    "chunk_type": "html_section",
    "section_tag": "main",  # or "header", "article"
    "has_scripts": true,
    "has_forms": true
}
```

#### CSS (.css, .scss, .sass)
- **Rule blocks**: Group related selectors → 1 chunk per logical group
  - Example: `.btn`, `.btn-primary`, `.btn-secondary` → 1 chunk
- **Media queries**: Each `@media` block → 1 chunk
- **Keyframes**: Each `@keyframes` animation → 1 chunk
- **Imports**: Track `@import` statements in metadata
- **CSS variables**: `:root` declarations → 1 chunk
- **SCSS/SASS specifics**: Mixins, functions, nested rules preserved

**Example Metadata**:
```python
{
    "file_path": "styles/theme.scss",
    "chunk_type": "css_rules",
    "selectors": [".btn", ".btn-primary"],
    "media_query": "@media (min-width: 768px)",  # if applicable
    "has_variables": true,
    "language": "scss"  # or "css", "sass"
}
```

## Proposed Chunking Strategy

### Code Chunking (AST-Based)

```python
# Hierarchical extraction with metadata

For each source file:
    1. Extract module-level docstring + imports → 1 chunk

    2. For each top-level function:
        → 1 chunk with metadata: {module, function_name, params, decorators}

    3. For each class:
        a. Class definition + docstring → 1 chunk
           Metadata: {module, class_name, base_classes, decorators}

        b. For each method in class:
           → 1 chunk with metadata: {module, class_name, method_name, params, decorators}

        c. For each property:
           → 1 chunk with metadata: {module, class_name, property_name, getter/setter}

        d. For each class variable/field:
           → 1 chunk with metadata: {module, class_name, field_name, type, default}

        e. For nested classes:
           → Recurse to step 3
```

#### Chunk Metadata Schema

```python
{
    # Location
    "repo_id": "owner/repo",
    "file_path": "path/to/file.py",
    "start_line": 42,
    "end_line": 58,

    # Hierarchy (module.class.method)
    "module": "myapp.models",
    "class_name": "UserProfile",      # null for top-level functions
    "function_name": "save",           # or "method_name"
    "property_name": "full_name",      # for @property
    "field_name": "email",             # for class attributes

    # Semantic info
    "chunk_type": "method",            # module|class|method|function|property|field
    "decorators": ["@override", "@transaction.atomic"],
    "parameters": "(self, force_insert=False)",
    "return_type": "None",             # if available
    "type_annotation": "str",          # for fields

    # Docstring (if present)
    "docstring": "Saves the user profile...",
    "docstring_summary": "Saves the user profile",  # first line

    # Django-specific
    "is_model": true,
    "model_fields": ["email", "bio"],  # for class chunks

    # Git metadata
    "commit_hash": "abc123",
    "commit_date": "2024-11-20",
    "author": "user@example.com",
    "branch": "main"  # or "develop", "feature/x", etc.
}
```

### Document Chunking (Structure-Based)

```python
For each Markdown/README file:
    1. Frontmatter → 1 chunk (metadata extraction)

    2. For each top-level heading (# or ##):
        a. Heading + content until next heading → 1 chunk
        b. If content > 6000 chars:
           → Split at paragraph boundaries
           → Create overlapping chunks (last 500 chars of chunk N = first 500 of chunk N+1)

    3. Code blocks:
        → Extract language, include in parent chunk
        → If > 1000 chars, also create separate chunk

    4. Lists/Tables:
        → Include in section chunk
        → If > 2000 chars, split intelligently (e.g., per list item)
```

#### Document Metadata Schema

```python
{
    "repo_id": "owner/repo",
    "file_path": "README.md",
    "chunk_type": "document_section",

    # Structure
    "heading": "Installation",
    "heading_level": 2,
    "section_index": 3,

    # Content classification
    "has_code_blocks": true,
    "languages": ["bash", "python"],
    "has_links": true,
    "has_images": false,

    # Frontmatter (if present)
    "title": "API Documentation",
    "tags": ["api", "rest"],
    "category": "developer-guide"
}
```

### Commit Chunking (Message-Based)

```python
For each commit:
    1. Commit message → 1 chunk
       Metadata: {commit_hash, author, date, files_changed, insertions, deletions}

    2. If message > 1000 chars:
       → Summary (first 200 chars) + full message in separate chunks

    3. Deduplication:
       → Same commit message across files? Store once with file list
```

#### Commit Metadata Schema

```python
{
    "repo_id": "owner/repo",
    "commit_hash": "abc123",
    "chunk_type": "commit_message",

    "author": "user@example.com",
    "commit_date": "2024-11-20T10:30:00",

    "files_changed": ["models.py", "views.py"],
    "insertions": 42,
    "deletions": 15,

    "branch": "main",
    "is_merge": false,

    # Message breakdown
    "message_summary": "Add user profile model",  # first line
    "message_body": "Full commit message...",
}
```

## Docstring Prioritization Strategy

### Why Docstrings Matter
Docstrings contain the highest semantic density:
- **Classes**: Purpose, attributes, examples
- **Methods**: Parameters, return values, side effects
- **Modules**: Package-level context

### Prioritization Rules

1. **Class chunks**: MUST include full class docstring
   ```python
   # Chunk content:
   class UserProfile(models.Model):
       """
       User profile with extended information.

       Attributes:
           user: OneToOne link to auth.User
           bio: User biography
       """
   ```

2. **Method chunks**: Include method docstring + signature
   ```python
   # Chunk content:
   def save(self, force_insert=False):
       """
       Saves the profile with validation.

       Args:
           force_insert: Skip update attempt
       """
       # ... method body ...
   ```

3. **If oversized**: Extract just signature + docstring as separate chunk
   ```python
   # Strategy for 15KB method:
   # Chunk 1: signature + docstring (300 chars)
   # Chunk 2: first 5700 chars of body
   # Chunk 3: next 5700 chars of body
   # Chunk 4: remaining chars
   ```

## Size Limit Handling

### Strategy: Recursive Splitting

```python
def chunk_code_unit(node, max_chars=6000):
    code = extract_code(node)
    metadata = extract_metadata(node)

    if len(code) <= max_chars:
        return [create_chunk(code, metadata)]

    # Try splitting at semantic boundaries
    if node.type == "class_definition":
        # Split into: class def + individual methods
        return split_class(node, max_chars)

    elif node.type == "function_definition":
        # Split into: signature+docstring + body parts
        return split_function(node, max_chars)

    else:
        # Last resort: split at statement boundaries
        return split_statements(node, max_chars)
```

### Boundary Priorities (in order)

1. **Class boundaries**: Never split a method across chunks
2. **Method boundaries**: Keep method signature + docstring together
3. **Statement boundaries**: Split at complete statements
4. **Line boundaries**: Only if statements too large
5. **Character boundaries**: Absolute last resort

### Overlap for Continuity

For documents and large code blocks:
- **Overlap**: Last 500 chars of chunk N = first 500 chars of chunk N+1
- **Purpose**: Preserve context for semantic search
- **Only for**: Documents, large methods (>10KB)

## Architecture Decision: Use LSP Instead of Manual Parsing

**NEW DIRECTION** (2025-11-20): Instead of manually walking ASTs with tree-sitter, we should use **Language Server Protocol (LSP)**.

### Why LSP?
- Language servers already understand every language's structure
- VS Code, IntelliJ, and other IDEs use them for code navigation
- They provide `textDocument/documentSymbol` which returns ALL functions/classes/methods with exact ranges
- Works for ANY language (Python, Swift, Go, Rust, etc.) - just install the LSP server
- Zero language-specific parsing code needed
- Maintained by language communities

### How It Works
```python
# 1. Ask LSP server for symbols
symbols = await lsp.get_document_symbols(file_path)

# 2. LSP returns hierarchy:
{
  "name": "MyClass",
  "kind": 5,  # Class
  "range": { "start": {"line": 10}, "end": {"line": 50} },
  "children": [
    {"name": "method1", "kind": 6, "range": {...}},
    {"name": "method2", "kind": 6, "range": {...}}
  ]
}

# 3. Convert to chunks (no parsing code needed!)
```

**See `LSP_ARCHITECTURE.md` for complete design.**

## Implementation Plan (LSP-Based)

### Phase 1: Implement LSP Parser
**Goal**: Universal parser using Language Server Protocol

1. Modify `parse_python_file()`:
   - When `class_definition` found, extract class metadata
   - Iterate `class.body.children` to find all `function_definition` nodes
   - Create chunks: 1 for class def, 1 per method

2. Add docstring extraction:
   - First statement in body if `type == "string"`
   - Store in metadata

3. Test with `test_chunking.py`:
   - Expected: 6 chunks from test class
   - Verify metadata completeness

### Phase 2: Add Rich Metadata
**Goal**: Full hierarchical paths (module.class.method)

1. Track parent context during recursion
2. Extract decorators, type hints, parameters
3. Add Django model detection (subclass of `models.Model`)
4. Extract and store git branch name in metadata
5. Store complete metadata schema

### Phase 3: Handle Size Limits
**Goal**: No truncation, smart splitting

1. Remove `truncate_chunk_text()` function
2. Implement `split_at_semantic_boundaries()`
3. Add overlap for large methods
4. Log warnings for unsplittable chunks > 6KB

### Phase 4: Document Chunking
**Goal**: Structure-based with overlap

1. Parse Markdown AST (headings, paragraphs, code blocks)
2. Chunk by section
3. Add overlap for continuity
4. Extract frontmatter metadata

### Phase 5: JavaScript/TypeScript
**Goal**: Match Python granularity

1. Extract classes → methods
2. Handle arrow functions, getters, setters
3. Add JSDoc/TSDoc extraction
4. React component detection

### Phase 6: Svelte/HTML/CSS Support
**Goal**: Parse web development files with section-aware chunking

1. **Svelte Parser** (`parsers/svelte_parser.py`):
   - Detect and extract `<script>`, template, `<style>` blocks
   - Pass script block to JS/TS parser
   - Chunk template by logical sections (`{#if}`, `{#each}`, semantic tags)
   - Pass style block to CSS parser
   - Tag all chunks with `section` metadata

2. **HTML Parser** (`parsers/html_parser.py`):
   - Extract semantic sections (`<header>`, `<main>`, `<footer>`)
   - Detect and extract inline `<script>` and `<style>` tags
   - Chunk forms separately
   - Handle web components/custom elements

3. **CSS Parser** (`parsers/css_parser.py`):
   - Group related selector rules
   - Extract media queries separately
   - Handle `@keyframes`, `@import`, CSS variables
   - Support SCSS/SASS syntax (nested rules, mixins, functions)

4. **Test Coverage**:
   - Test Svelte component with all three sections
   - Test HTML with inline scripts/styles
   - Test SCSS with nested rules and mixins

### Phase 7: Commit Deduplication
**Goal**: One commit message per hash

1. Extract commit messages separately
2. Link commits to affected files
3. Store once per hash

## Success Metrics

### Before Fix
- 25,325 chunks for kbhalerao/labcore
- 105 truncated classes (loss of context)
- No method-level granularity

### After Fix (Target)
- ~50,000-75,000 chunks (3x increase from method extraction)
- 0 truncated chunks
- Average chunk size: 500-2000 chars
- 100% of methods searchable individually

### Quality Checks
1. Search for method name → finds exact method chunk
2. Search for class → finds class definition + all methods
3. Docstring content fully preserved
4. Metadata complete for all chunk types

## Open Questions

1. **Nested classes**: Extract as separate chunks or keep with parent?
   - **Decision**: Extract separately with `parent_class` in metadata

2. **Import statements**: Include with every chunk or just module-level?
   - **Decision**: Module-level only, reduce duplication

3. **Test methods**: Special handling for pytest/unittest classes?
   - **Decision**: Yes, add `is_test: true` to metadata

4. **Overlap size**: 500 chars enough for context?
   - **Decision**: Make configurable, test with 500/1000/1500

5. **Django fields**: Chunk per field or keep with model?
   - **Decision**: Keep with model class chunk, list in metadata

6. **Svelte template chunking**: Split by every block (`{#if}`, `{#each}`) or group related blocks?
   - **Proposed**: Start with one chunk per top-level block, adjust if too granular

7. **CSS selector grouping**: How to determine "related" selectors for grouping?
   - **Proposed**: Group by prefix (`.btn-*`), or adjacent declarations, or max 1000 chars

8. **Branch tracking**: Store branch name from `HEAD` or from remote's default?
   - **Decision**: Store active branch from cloned repo's `HEAD`

9. **Svelte reactive statements**: Include `$:` statements with template or script chunks?
   - **Proposed**: Include with script block since they're JavaScript

## References

- Tree-sitter Python grammar: https://github.com/tree-sitter/tree-sitter-python
- Nomic embedding limits: 8192 tokens
- Current implementation: `parsers/code_parser.py:192-296`
- Test script: `test_chunking.py`
