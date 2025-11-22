# Better Approach: Use Tree-sitter Queries

**Status**: Design Proposal V2
**Last Updated**: 2025-11-20
**Insight**: Don't write code to traverse ASTs - use tree-sitter's declarative query language

## The Problem with Our Current Approach

We're manually walking ASTs and writing if/else logic for each node type:

```python
for node in root.children:
    if node.type == "function_definition":
        # extract function
    elif node.type == "class_definition":
        # extract class
        for child in class_body:
            if child.type == "function_definition":
                # extract method
```

This is:
- Verbose
- Language-specific
- Fragile (breaks if tree structure changes)
- Hard to maintain

## The Better Way: Tree-sitter Queries

Tree-sitter has a **query language** specifically designed for extracting code structures. It's declarative and language-agnostic.

### Example: Python Query

```scheme
;; queries/python.scm

; Capture all function definitions
(function_definition
  name: (identifier) @function.name
  parameters: (parameters) @function.params
  body: (block) @function.body) @function.definition

; Capture all class definitions
(class_definition
  name: (identifier) @class.name
  body: (block) @class.body) @class.definition

; Capture methods inside classes
(class_definition
  body: (block
    (function_definition
      name: (identifier) @method.name
      parameters: (parameters) @method.params) @method.definition))

; Capture docstrings
(expression_statement
  (string) @docstring)
```

### Example: Swift Query

```scheme
;; queries/swift.scm

; Capture functions
(function_declaration
  name: (simple_identifier) @function.name
  body: (function_body) @function.body) @function.definition

; Capture classes/structs
(class_declaration
  name: (type_identifier) @class.name
  body: (class_body) @class.body) @class.definition

(struct_declaration
  name: (type_identifier) @struct.name
  body: (class_body) @struct.body) @struct.definition
```

### Example: Svelte Query

```scheme
;; queries/svelte.scm

; Capture script blocks
(script_element
  (start_tag)
  (raw_text) @script.content
  (end_tag)) @script.block

; Capture style blocks
(style_element
  (start_tag)
  (raw_text) @style.content
  (end_tag)) @style.block
```

## Implementation

### 1. Query Files (One per Language)

```
parsers/
  queries/
    python.scm
    javascript.scm
    typescript.scm
    swift.scm
    svelte.scm
    go.scm
    rust.scm
    ...
```

### 2. Generic Query Executor

```python
# parsers/query_parser.py

from tree_sitter import Language, Parser, Query
from pathlib import Path
from typing import List, Dict

class QueryBasedParser:
    """
    Universal parser using tree-sitter queries
    No language-specific code needed!
    """

    def __init__(self):
        # Load queries for all languages
        self.queries = self._load_queries()

        # Initialize parsers
        from tree_sitter_languages import get_parser
        self.parsers = {}
        for lang in self.queries.keys():
            self.parsers[lang] = get_parser(lang)

    def _load_queries(self) -> Dict[str, str]:
        """Load .scm query files"""
        queries = {}
        query_dir = Path(__file__).parent / "queries"

        for query_file in query_dir.glob("*.scm"):
            lang = query_file.stem
            queries[lang] = query_file.read_text()
            logger.info(f"Loaded queries for {lang}")

        return queries

    async def parse_file(
        self,
        file_path: Path,
        repo_id: str,
        language: str
    ) -> List[CodeChunk]:
        """Parse file using queries"""

        # Read file
        content = file_path.read_bytes()

        # Parse with tree-sitter
        tree = self.parsers[language].parse(content)

        # Execute query
        query = self.queries[language]
        captures = self._execute_query(tree, query, language)

        # Convert captures to chunks
        chunks = self._captures_to_chunks(captures, content, file_path, repo_id)

        return chunks

    def _execute_query(self, tree, query_text: str, language: str):
        """Execute tree-sitter query"""
        from tree_sitter_languages import get_language

        lang = get_language(language)
        query = lang.query(query_text)

        return query.captures(tree.root_node)

    def _captures_to_chunks(self, captures, content, file_path, repo_id):
        """Convert query captures to CodeChunks"""
        chunks = []

        # Group captures by type
        functions = [c for c in captures if 'function' in c[1]]
        classes = [c for c in captures if 'class' in c[1]]
        methods = [c for c in captures if 'method' in c[1]]

        # Create chunks from captures
        for node, capture_name in functions:
            code_text = content[node.start_byte:node.end_byte].decode('utf8')

            chunks.append(CodeChunk(
                repo_id=repo_id,
                file_path=str(file_path),
                chunk_type="function",
                code_text=code_text,
                language=language,
                metadata={
                    "start_line": node.start_point[0] + 1,
                    "end_line": node.end_point[0] + 1,
                }
            ))

        # Similar for classes, methods, etc.

        return chunks
```

### 3. Language Config (Just Extensions)

```python
# config.py

LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".ts": "typescript",
    ".swift": "swift",
    ".svelte": "svelte",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".cpp": "cpp",
    ".c": "c",
    # Just map extensions to tree-sitter language names
}
```

## Adding a New Language (2 Steps)

### Step 1: Create Query File

```bash
# Create queries/swift.scm
cat > parsers/queries/swift.scm << 'EOF'
(function_declaration
  name: (simple_identifier) @function.name) @function.definition

(class_declaration
  name: (type_identifier) @class.name) @class.definition
EOF
```

### Step 2: Add Extension Mapping

```python
# config.py
LANGUAGE_MAP = {
    ".swift": "swift",  # ← Just add this line
}
```

**Done!** No code changes, no classes, no plugins.

## Why This Is Better

1. **Declarative, not imperative**: Queries describe WHAT to extract, not HOW
2. **Language-agnostic**: Same code handles all languages
3. **Leverage tree-sitter ecosystem**: Queries already exist for many languages
4. **Easy to test**: Just test query files, not code
5. **Easy to extend**: Add a .scm file, no Python code
6. **Maintained by tree-sitter community**: Queries are standard

## Real-World Example

Tree-sitter already has query files for syntax highlighting. We can reuse them!

GitHub uses tree-sitter queries for code navigation. Examples:
- https://github.com/tree-sitter/tree-sitter-python/blob/master/queries/highlights.scm
- https://github.com/tree-sitter/tree-sitter-swift/blob/master/queries/highlights.scm

We just adapt these for our chunking needs.

## Complete Example: Python

```scheme
;; queries/python.scm

; Top-level functions
(module
  (function_definition
    name: (identifier) @function.name
    parameters: (parameters) @function.params
    body: (block) @function.body) @function.top_level)

; Classes
(module
  (class_definition
    name: (identifier) @class.name
    superclasses: (argument_list)? @class.bases
    body: (block) @class.body) @class.definition)

; Methods (functions inside classes)
(class_definition
  body: (block
    (function_definition
      name: (identifier) @method.name
      parameters: (parameters) @method.params
      body: (block) @method.body) @method.definition))

; Decorators
(decorated_definition
  (decorator) @decorator
  definition: (_) @decorated.definition)

; Docstrings (first string in function/class)
(function_definition
  body: (block
    (expression_statement
      (string) @function.docstring) .))

(class_definition
  body: (block
    (expression_statement
      (string) @class.docstring) .))
```

This single query file handles:
- Top-level functions
- Classes with inheritance
- Methods inside classes
- Decorators
- Docstrings

No Python code needed!

## Comparison

### Before (Manual AST Walking)
```python
# 300+ lines of if/else logic
if node.type == "function_definition":
    name_node = node.child_by_field_name("name")
    if name_node:
        function_name = name_node.text.decode("utf8")
        # ... 20 more lines
    # ... handle nested cases
```

### After (Query-Based)
```scheme
# 10 lines of declarative query
(function_definition
  name: (identifier) @function.name) @function.definition
```

## Migration Path

1. **Keep existing code_parser.py** for Python (already working)
2. **Add QueryBasedParser** as alternative
3. **Test with Python queries** first
4. **Add Swift queries** - prove it works for new language
5. **Gradually migrate** other languages to queries
6. **Eventually deprecate** manual AST walking

## Resources

- Tree-sitter query syntax: https://tree-sitter.github.io/tree-sitter/using-parsers#pattern-matching-with-queries
- Example queries: https://github.com/tree-sitter/tree-sitter-python/tree/master/queries
- Query testing: tree-sitter can test queries against example code

## The Big Win

**Adding Swift support goes from "write 200 lines of Python" to "write 20 lines of queries".**

And those queries are:
- Easier to read
- Easier to test
- Language-portable
- Community-maintained

This is how tools like GitHub's code search and VS Code's symbol navigation work.
