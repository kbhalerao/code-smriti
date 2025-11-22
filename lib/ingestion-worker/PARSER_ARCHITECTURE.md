# Extensible Parser Architecture

**Status**: Design Proposal
**Last Updated**: 2025-11-20
**Goal**: Make it trivial to add new language support (Swift, Go, Rust, etc.)

## Current Problem

All language parsing logic lives in one monolithic `code_parser.py` file:
- Adding Swift requires modifying existing code
- No separation of concerns between languages
- Hard to test individual language parsers
- Can't easily contribute language-specific parsers

## Proposed Architecture

### 1. Plugin-Based Language Parsers

Each language gets its own parser class implementing a common interface:

```
parsers/
├── base_parser.py           # Abstract base class
├── code_parser.py            # Main orchestrator
├── languages/
│   ├── __init__.py
│   ├── python_parser.py      # Python-specific logic
│   ├── javascript_parser.py  # JavaScript/TypeScript logic
│   ├── swift_parser.py       # Swift-specific logic (new!)
│   ├── svelte_parser.py      # Svelte multi-section parser
│   ├── html_parser.py        # HTML semantic parser
│   └── css_parser.py         # CSS/SCSS parser
├── document_parser.py        # Markdown, README, etc.
└── commit_parser.py          # Git commit messages
```

### 2. Base Parser Interface

```python
# parsers/base_parser.py

from abc import ABC, abstractmethod
from typing import List, Dict, Optional
from pathlib import Path

class BaseLanguageParser(ABC):
    """
    Abstract base class for language-specific parsers
    All language parsers must implement these methods
    """

    @property
    @abstractmethod
    def language_name(self) -> str:
        """Return language name (e.g., 'python', 'swift')"""
        pass

    @property
    @abstractmethod
    def file_extensions(self) -> List[str]:
        """Return list of file extensions (e.g., ['.py', '.pyx'])"""
        pass

    @property
    @abstractmethod
    def tree_sitter_language(self) -> str:
        """Return tree-sitter language name (e.g., 'python')"""
        pass

    @abstractmethod
    def parse_tree(
        self,
        tree,
        content: bytes,
        file_path: Path,
        repo_id: str,
        metadata: Dict
    ) -> List[CodeChunk]:
        """
        Parse tree-sitter AST and return list of chunks

        Args:
            tree: tree-sitter Tree object
            content: File content as bytes
            file_path: Path to file
            repo_id: Repository identifier
            metadata: Git metadata (commit_hash, author, date, branch)

        Returns:
            List of CodeChunk objects
        """
        pass

    # Optional methods with default implementations

    def should_skip_file(self, file_path: Path) -> bool:
        """
        Check if file should be skipped (minified, generated, etc.)
        Override for language-specific skip logic
        """
        name = file_path.name.lower()

        # Skip minified files
        if '.min.' in name:
            return True

        # Skip generated files
        if any(x in name for x in ['generated', 'codegen', '.g.']):
            return True

        return False

    def extract_imports(self, node, content: bytes) -> List[str]:
        """Extract import statements (override for language specifics)"""
        return []

    def extract_docstring(self, node, content: bytes) -> Optional[str]:
        """Extract docstring/documentation (override for language specifics)"""
        return None

    def split_large_chunk(self, code: str, max_size: int) -> List[str]:
        """
        Split oversized chunk at semantic boundaries
        Default: split at statement boundaries
        Override for language-specific splitting
        """
        # Default implementation: split at double newlines
        if len(code) <= max_size:
            return [code]

        # Simple splitting strategy
        chunks = []
        current = ""
        for line in code.split('\n'):
            if len(current) + len(line) > max_size:
                chunks.append(current)
                current = line
            else:
                current += '\n' + line

        if current:
            chunks.append(current)

        return chunks
```

### 3. Language-Specific Parser Example

```python
# parsers/languages/python_parser.py

from typing import List, Dict
from pathlib import Path
from parsers.base_parser import BaseLanguageParser
from parsers.code_parser import CodeChunk

class PythonParser(BaseLanguageParser):
    """Parser for Python files"""

    @property
    def language_name(self) -> str:
        return "python"

    @property
    def file_extensions(self) -> List[str]:
        return [".py", ".pyx", ".pyi"]

    @property
    def tree_sitter_language(self) -> str:
        return "python"

    def parse_tree(
        self,
        tree,
        content: bytes,
        file_path: Path,
        repo_id: str,
        metadata: Dict
    ) -> List[CodeChunk]:
        """Parse Python AST into chunks"""
        chunks = []
        root = tree.root_node

        # Extract module-level docstring
        if root.children and root.children[0].type == "expression_statement":
            first_child = root.children[0]
            if first_child.children and first_child.children[0].type == "string":
                docstring_node = first_child.children[0]
                docstring_text = content[docstring_node.start_byte:docstring_node.end_byte].decode('utf8')

                chunks.append(CodeChunk(
                    repo_id=repo_id,
                    file_path=str(file_path),
                    chunk_type="module_docstring",
                    code_text=docstring_text,
                    language="python",
                    metadata={**metadata, "docstring": True}
                ))

        # Iterate top-level nodes
        for node in root.children:
            if node.type == "function_definition":
                chunks.extend(self._parse_function(node, content, file_path, repo_id, metadata))

            elif node.type == "class_definition":
                chunks.extend(self._parse_class(node, content, file_path, repo_id, metadata))

        return chunks

    def _parse_function(self, node, content, file_path, repo_id, metadata, parent_class=None):
        """Parse function definition"""
        # Extract function name
        name_node = node.child_by_field_name("name")
        function_name = name_node.text.decode("utf8") if name_node else "unknown"

        # Extract parameters
        params_node = node.child_by_field_name("parameters")
        params = params_node.text.decode("utf8") if params_node else "()"

        # Extract decorators
        decorators = []
        for child in node.children:
            if child.type == "decorator":
                decorators.append(child.text.decode("utf8"))

        # Extract docstring
        docstring = self.extract_docstring(node, content)

        # Get full code text
        code_text = content[node.start_byte:node.end_byte].decode('utf8')

        chunk_metadata = {
            **metadata,
            "function_name": function_name,
            "parameters": params,
            "decorators": decorators,
            "docstring": docstring,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
        }

        if parent_class:
            chunk_metadata["class_name"] = parent_class

        return [CodeChunk(
            repo_id=repo_id,
            file_path=str(file_path),
            chunk_type="method" if parent_class else "function",
            code_text=code_text,
            language="python",
            metadata=chunk_metadata
        )]

    def _parse_class(self, node, content, file_path, repo_id, metadata):
        """Parse class definition - recursively extract methods"""
        chunks = []

        # Extract class name
        name_node = node.child_by_field_name("name")
        class_name = name_node.text.decode("utf8") if name_node else "unknown"

        # Extract class docstring
        docstring = self.extract_docstring(node, content)

        # Create class definition chunk (just signature + docstring)
        body_node = node.child_by_field_name("body")
        if body_node:
            # Get class header (everything before body)
            class_header = content[node.start_byte:body_node.start_byte].decode('utf8')
            if docstring:
                class_header += f'\n    """{docstring}"""'

            chunks.append(CodeChunk(
                repo_id=repo_id,
                file_path=str(file_path),
                chunk_type="class",
                code_text=class_header,
                language="python",
                metadata={
                    **metadata,
                    "class_name": class_name,
                    "docstring": docstring,
                    "start_line": node.start_point[0] + 1,
                }
            ))

            # Recursively parse methods inside class
            for child in body_node.children:
                if child.type == "function_definition":
                    chunks.extend(
                        self._parse_function(child, content, file_path, repo_id, metadata, parent_class=class_name)
                    )

        return chunks

    def extract_docstring(self, node, content: bytes) -> Optional[str]:
        """Extract Python docstring from function or class"""
        body_node = node.child_by_field_name("body")
        if not body_node or not body_node.children:
            return None

        # First statement in body
        first_stmt = body_node.children[1] if len(body_node.children) > 1 else None
        if first_stmt and first_stmt.type == "expression_statement":
            expr = first_stmt.children[0] if first_stmt.children else None
            if expr and expr.type == "string":
                docstring = content[expr.start_byte:expr.end_byte].decode('utf8')
                # Strip quotes
                return docstring.strip('"""').strip("'''").strip()

        return None
```

### 4. Swift Parser Example

```python
# parsers/languages/swift_parser.py

from typing import List, Dict
from pathlib import Path
from parsers.base_parser import BaseLanguageParser
from parsers.code_parser import CodeChunk

class SwiftParser(BaseLanguageParser):
    """Parser for Swift files"""

    @property
    def language_name(self) -> str:
        return "swift"

    @property
    def file_extensions(self) -> List[str]:
        return [".swift"]

    @property
    def tree_sitter_language(self) -> str:
        return "swift"

    def parse_tree(
        self,
        tree,
        content: bytes,
        file_path: Path,
        repo_id: str,
        metadata: Dict
    ) -> List[CodeChunk]:
        """Parse Swift AST into chunks"""
        chunks = []
        root = tree.root_node

        for node in root.children:
            if node.type == "function_declaration":
                chunks.extend(self._parse_function(node, content, file_path, repo_id, metadata))

            elif node.type in ["class_declaration", "struct_declaration", "protocol_declaration"]:
                chunks.extend(self._parse_type(node, content, file_path, repo_id, metadata))

        return chunks

    def _parse_function(self, node, content, file_path, repo_id, metadata, parent_type=None):
        """Parse Swift function"""
        # Extract function name
        name_node = self._find_child_by_type(node, "simple_identifier")
        function_name = name_node.text.decode("utf8") if name_node else "unknown"

        # Get code text
        code_text = content[node.start_byte:node.end_byte].decode('utf8')

        chunk_metadata = {
            **metadata,
            "function_name": function_name,
            "start_line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
        }

        if parent_type:
            chunk_metadata["type_name"] = parent_type

        return [CodeChunk(
            repo_id=repo_id,
            file_path=str(file_path),
            chunk_type="method" if parent_type else "function",
            code_text=code_text,
            language="swift",
            metadata=chunk_metadata
        )]

    def _parse_type(self, node, content, file_path, repo_id, metadata):
        """Parse Swift class/struct/protocol"""
        chunks = []

        # Extract type name
        name_node = self._find_child_by_type(node, "type_identifier")
        type_name = name_node.text.decode("utf8") if name_node else "unknown"

        # Create type definition chunk
        chunks.append(CodeChunk(
            repo_id=repo_id,
            file_path=str(file_path),
            chunk_type=node.type.replace("_declaration", ""),
            code_text=content[node.start_byte:node.end_byte].decode('utf8')[:500],  # Just header
            language="swift",
            metadata={
                **metadata,
                "type_name": type_name,
                "type_kind": node.type,
                "start_line": node.start_point[0] + 1,
            }
        ))

        # Parse methods inside type
        body_node = self._find_child_by_type(node, "class_body")
        if body_node:
            for child in body_node.children:
                if child.type == "function_declaration":
                    chunks.extend(
                        self._parse_function(child, content, file_path, repo_id, metadata, parent_type=type_name)
                    )

        return chunks

    def _find_child_by_type(self, node, type_name):
        """Helper to find child node by type"""
        for child in node.children:
            if child.type == type_name:
                return child
        return None
```

### 5. Parser Registry

```python
# parsers/code_parser.py (refactored)

from typing import Dict, Type
from parsers.base_parser import BaseLanguageParser
from parsers.languages.python_parser import PythonParser
from parsers.languages.javascript_parser import JavaScriptParser
from parsers.languages.swift_parser import SwiftParser

class ParserRegistry:
    """
    Central registry for language parsers
    Auto-discovers parsers and maps file extensions
    """

    def __init__(self):
        self._parsers: Dict[str, BaseLanguageParser] = {}
        self._extension_map: Dict[str, str] = {}

        # Register built-in parsers
        self.register(PythonParser())
        self.register(JavaScriptParser())
        self.register(SwiftParser())

    def register(self, parser: BaseLanguageParser):
        """Register a language parser"""
        self._parsers[parser.language_name] = parser

        # Map file extensions to this parser
        for ext in parser.file_extensions:
            self._extension_map[ext] = parser.language_name

        logger.info(f"Registered parser: {parser.language_name} ({', '.join(parser.file_extensions)})")

    def get_parser(self, file_path: Path) -> Optional[BaseLanguageParser]:
        """Get parser for file based on extension"""
        ext = file_path.suffix.lower()
        language = self._extension_map.get(ext)
        return self._parsers.get(language) if language else None

    def list_supported_extensions(self) -> List[str]:
        """List all supported file extensions"""
        return list(self._extension_map.keys())


class CodeParser:
    """Main code parser orchestrator"""

    def __init__(self):
        self.registry = ParserRegistry()

        # Initialize tree-sitter parsers for all registered languages
        from tree_sitter_languages import get_parser
        self.ts_parsers = {}
        for lang_name, parser in self.registry._parsers.items():
            try:
                self.ts_parsers[lang_name] = get_parser(parser.tree_sitter_language)
            except Exception as e:
                logger.warning(f"Could not load tree-sitter for {lang_name}: {e}")

    async def parse_file(
        self,
        file_path: Path,
        repo_path: Path,
        repo_id: str
    ) -> List[CodeChunk]:
        """Parse file using appropriate language parser"""

        # Get language-specific parser
        lang_parser = self.registry.get_parser(file_path)
        if not lang_parser:
            return []

        # Check if should skip
        if lang_parser.should_skip_file(file_path):
            logger.debug(f"Skipping {file_path.name}")
            return []

        # Read file content
        content = file_path.read_bytes()

        # Get tree-sitter parser
        ts_parser = self.ts_parsers.get(lang_parser.language_name)
        if not ts_parser:
            logger.warning(f"No tree-sitter parser for {lang_parser.language_name}")
            return []

        # Parse AST
        tree = ts_parser.parse(content)

        # Get git metadata
        metadata = self._get_git_metadata(file_path, repo_path)

        # Use language-specific parser
        chunks = lang_parser.parse_tree(tree, content, file_path, repo_id, metadata)

        return chunks
```

### 6. Configuration (Auto-Detect)

```python
# config.py (simplified)

class WorkerConfig(BaseSettings):
    """Worker configuration"""

    # No need to manually list extensions anymore!
    # Parser registry auto-discovers them

    @property
    def supported_code_extensions(self) -> List[str]:
        """Get supported extensions from parser registry"""
        from parsers.code_parser import CodeParser
        parser = CodeParser()
        return parser.registry.list_supported_extensions()
```

## Adding a New Language (3 Steps)

### Step 1: Create Language Parser

```bash
touch parsers/languages/swift_parser.py
```

### Step 2: Implement Parser Class

```python
from parsers.base_parser import BaseLanguageParser

class SwiftParser(BaseLanguageParser):
    @property
    def language_name(self) -> str:
        return "swift"

    @property
    def file_extensions(self) -> List[str]:
        return [".swift"]

    @property
    def tree_sitter_language(self) -> str:
        return "swift"

    def parse_tree(self, tree, content, file_path, repo_id, metadata):
        # Your Swift-specific parsing logic here
        pass
```

### Step 3: Register in Registry

```python
# parsers/code_parser.py

from parsers.languages.swift_parser import SwiftParser

class ParserRegistry:
    def __init__(self):
        # ...
        self.register(SwiftParser())  # ← Just add this line!
```

**That's it!** No other files need modification.

## Benefits

1. **Separation of Concerns**: Each language in its own file
2. **Easy Testing**: Test each parser independently
3. **Community Contributions**: Contributors can add languages without touching core code
4. **Discoverability**: `registry.list_supported_extensions()` auto-updates
5. **Shared Logic**: Base class provides common utilities (skip logic, splitting)
6. **Type Safety**: Abstract base class enforces interface
7. **No Config Updates**: Extensions auto-registered from parser classes

## Migration Plan

### Phase 1: Extract Base Class
1. Create `parsers/base_parser.py` with abstract interface
2. No changes to existing code

### Phase 2: Extract Python Parser
1. Create `parsers/languages/python_parser.py`
2. Move Python-specific logic from `code_parser.py`
3. Test with existing Python repos

### Phase 3: Create Registry
1. Implement `ParserRegistry` in `code_parser.py`
2. Register Python parser
3. Verify no functionality changes

### Phase 4: Extract Other Languages
1. Create `javascript_parser.py`, `typescript_parser.py`
2. Move language-specific logic
3. Test with JS/TS repos

### Phase 5: Add New Languages
1. Implement `swift_parser.py`, `svelte_parser.py`, etc.
2. Register in registry
3. Celebrate easy extensibility!

## Tree-Sitter Support

Tree-sitter supports 70+ languages out of the box. Adding support is trivial:

```python
# Check if tree-sitter has the language
from tree_sitter_languages import get_language, get_parser

try:
    parser = get_parser("rust")  # Rust support!
    print("✓ Rust supported")
except:
    print("✗ Need to install tree-sitter-rust")
```

Supported languages include:
- Python, JavaScript, TypeScript, Swift, Go, Rust, C, C++, Java, Kotlin
- Ruby, PHP, C#, Scala, Elixir, Erlang, Clojure, Haskell
- HTML, CSS, JSON, YAML, TOML, Markdown
- Bash, Dockerfile, SQL
- And many more...

## Example: Adding Go Support

```python
# parsers/languages/go_parser.py

class GoParser(BaseLanguageParser):
    @property
    def language_name(self) -> str:
        return "go"

    @property
    def file_extensions(self) -> List[str]:
        return [".go"]

    @property
    def tree_sitter_language(self) -> str:
        return "go"

    def parse_tree(self, tree, content, file_path, repo_id, metadata):
        chunks = []
        root = tree.root_node

        for node in root.children:
            if node.type == "function_declaration":
                # Parse Go function
                pass
            elif node.type == "type_declaration":
                # Parse Go struct/interface
                pass

        return chunks
```

Register it:
```python
self.register(GoParser())
```

Done! Now `.go` files are supported.

## Future: Plugin System

For ultimate extensibility, load parsers dynamically:

```python
# parsers/code_parser.py

class ParserRegistry:
    def load_plugins(self, plugin_dir: Path):
        """Load language parsers from plugin directory"""
        for py_file in plugin_dir.glob("*_parser.py"):
            module = importlib.import_module(f"parsers.languages.{py_file.stem}")
            for item in dir(module):
                cls = getattr(module, item)
                if isinstance(cls, type) and issubclass(cls, BaseLanguageParser) and cls != BaseLanguageParser:
                    self.register(cls())
```

Then users can add custom parsers without modifying core code:
```bash
cp my_custom_parser.py parsers/languages/
# Automatically loaded!
```
