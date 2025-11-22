# LSP-Based Parsing: The Right Way

**Status**: Design Proposal V3 (THE ONE)
**Last Updated**: 2025-11-20
**Insight**: Use Language Server Protocol - already solves this problem

## The Breakthrough

Language servers (LSP) already understand every language's structure. VS Code, IntelliJ, and other IDEs use them for:
- Go to definition
- Find references
- Symbol outline
- Code navigation

**We should use the same infrastructure for chunking!**

## How LSP Solves Our Problem

The Language Server Protocol has a `textDocument/documentSymbol` request that returns the complete symbol hierarchy:

### Example: Python Response

```json
{
  "uri": "file:///path/to/file.py",
  "symbols": [
    {
      "name": "MyClass",
      "kind": 5,  // SymbolKind.Class
      "range": {
        "start": { "line": 10, "character": 0 },
        "end": { "line": 50, "character": 1 }
      },
      "selectionRange": { ... },  // Just the class name
      "detail": "class MyClass(BaseClass)",
      "children": [
        {
          "name": "__init__",
          "kind": 6,  // SymbolKind.Method
          "range": { "start": { "line": 12, "character": 4 }, "end": { "line": 15, "character": 20 } },
          "detail": "(self, name: str)"
        },
        {
          "name": "process",
          "kind": 6,  // SymbolKind.Method
          "range": { "start": { "line": 17, "character": 4 }, "end": { "line": 25, "character": 30 } },
          "detail": "(self, data: Dict) -> bool"
        }
      ]
    },
    {
      "name": "helper_function",
      "kind": 12,  // SymbolKind.Function
      "range": { "start": { "line": 52, "character": 0 }, "end": { "line": 60, "character": 15 } }
    }
  ]
}
```

**This gives us:**
- ✅ All functions, classes, methods
- ✅ Exact byte ranges
- ✅ Hierarchy (methods inside classes)
- ✅ Type information (parameters, returns)
- ✅ Works for ANY language with an LSP server

## Symbol Kinds (Standardized)

```python
class SymbolKind:
    File = 1
    Module = 2
    Namespace = 3
    Package = 4
    Class = 5
    Method = 6
    Property = 7
    Field = 8
    Constructor = 9
    Enum = 10
    Interface = 11
    Function = 12
    Variable = 13
    Constant = 14
    # ... and more
```

Every language maps to these standard kinds. Swift classes, Python classes, Go structs - all use `SymbolKind.Class`.

## Implementation

### 1. Language Server Manager

```python
# parsers/lsp_parser.py

import asyncio
import json
from pathlib import Path
from typing import List, Dict, Optional

class LSPParser:
    """
    Universal parser using Language Server Protocol
    Works for ANY language with an LSP server
    """

    # Map file extensions to LSP servers
    LSP_SERVERS = {
        ".py": {
            "command": ["pylsp"],  # python-lsp-server
            "name": "python"
        },
        ".js": {
            "command": ["typescript-language-server", "--stdio"],
            "name": "javascript"
        },
        ".ts": {
            "command": ["typescript-language-server", "--stdio"],
            "name": "typescript"
        },
        ".swift": {
            "command": ["sourcekit-lsp"],
            "name": "swift"
        },
        ".go": {
            "command": ["gopls"],
            "name": "go"
        },
        ".rs": {
            "command": ["rust-analyzer"],
            "name": "rust"
        },
        ".java": {
            "command": ["jdtls"],
            "name": "java"
        },
        # Add more as needed - just install the LSP server!
    }

    def __init__(self):
        self.servers = {}  # Active LSP server processes

    async def get_document_symbols(
        self,
        file_path: Path,
        content: str
    ) -> List[Dict]:
        """
        Get all symbols from file using LSP
        Returns standardized symbol hierarchy
        """
        ext = file_path.suffix.lower()

        if ext not in self.LSP_SERVERS:
            logger.warning(f"No LSP server configured for {ext}")
            return []

        # Get or start LSP server
        server = await self._get_server(ext)

        # Open document
        await self._lsp_request(server, "textDocument/didOpen", {
            "textDocument": {
                "uri": file_path.as_uri(),
                "languageId": self.LSP_SERVERS[ext]["name"],
                "version": 1,
                "text": content
            }
        })

        # Request symbols
        response = await self._lsp_request(server, "textDocument/documentSymbol", {
            "textDocument": {
                "uri": file_path.as_uri()
            }
        })

        # Close document
        await self._lsp_request(server, "textDocument/didClose", {
            "textDocument": {
                "uri": file_path.as_uri()
            }
        })

        return response.get("result", [])

    async def _get_server(self, ext: str):
        """Get or start LSP server for file type"""
        if ext not in self.servers:
            config = self.LSP_SERVERS[ext]
            self.servers[ext] = await self._start_server(config["command"])
        return self.servers[ext]

    async def _start_server(self, command: List[str]):
        """Start LSP server process"""
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        # Initialize server
        await self._lsp_request(process, "initialize", {
            "processId": None,
            "rootUri": None,
            "capabilities": {}
        })

        await self._lsp_request(process, "initialized", {})

        return process

    async def _lsp_request(self, process, method: str, params: Dict) -> Dict:
        """Send JSON-RPC request to LSP server"""
        request_id = id(params)

        request = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
            "params": params
        }

        # Send request
        message = json.dumps(request)
        content = f"Content-Length: {len(message)}\r\n\r\n{message}"
        process.stdin.write(content.encode())
        await process.stdin.drain()

        # Read response
        response = await self._read_lsp_response(process)
        return response

    async def _read_lsp_response(self, process) -> Dict:
        """Read JSON-RPC response from LSP server"""
        # Read headers
        headers = {}
        while True:
            line = await process.stdout.readline()
            if line == b'\r\n':
                break
            key, value = line.decode().strip().split(': ')
            headers[key] = value

        # Read content
        content_length = int(headers.get('Content-Length', 0))
        content = await process.stdout.read(content_length)

        return json.loads(content)

    async def symbols_to_chunks(
        self,
        symbols: List[Dict],
        content: str,
        file_path: Path,
        repo_id: str,
        metadata: Dict
    ) -> List[CodeChunk]:
        """Convert LSP symbols to CodeChunks"""
        chunks = []

        for symbol in symbols:
            # Extract range
            start_line = symbol["range"]["start"]["line"]
            end_line = symbol["range"]["end"]["line"]
            start_char = symbol["range"]["start"]["character"]
            end_char = symbol["range"]["end"]["character"]

            # Get code text from range
            lines = content.split('\n')
            code_text = '\n'.join(lines[start_line:end_line + 1])

            # Map LSP SymbolKind to our chunk types
            chunk_type = self._symbol_kind_to_chunk_type(symbol["kind"])

            # Create chunk
            chunks.append(CodeChunk(
                repo_id=repo_id,
                file_path=str(file_path),
                chunk_type=chunk_type,
                code_text=code_text,
                language=self.LSP_SERVERS[file_path.suffix]["name"],
                metadata={
                    **metadata,
                    "symbol_name": symbol["name"],
                    "symbol_kind": symbol["kind"],
                    "detail": symbol.get("detail", ""),
                    "start_line": start_line + 1,
                    "end_line": end_line + 1,
                }
            ))

            # Recursively process children
            if "children" in symbol:
                child_chunks = await self.symbols_to_chunks(
                    symbol["children"],
                    content,
                    file_path,
                    repo_id,
                    {**metadata, "parent_symbol": symbol["name"]}
                )
                chunks.extend(child_chunks)

        return chunks

    def _symbol_kind_to_chunk_type(self, kind: int) -> str:
        """Map LSP SymbolKind to our chunk type"""
        KIND_MAP = {
            5: "class",      # Class
            6: "method",     # Method
            9: "method",     # Constructor
            12: "function",  # Function
            7: "property",   # Property
            8: "field",      # Field
            10: "enum",      # Enum
            11: "interface", # Interface
        }
        return KIND_MAP.get(kind, "code")
```

### 2. Usage

```python
# worker.py

async def parse_file(file_path: Path):
    parser = LSPParser()

    content = file_path.read_text()

    # Get symbols from LSP
    symbols = await parser.get_document_symbols(file_path, content)

    # Convert to chunks
    chunks = await parser.symbols_to_chunks(
        symbols,
        content,
        file_path,
        repo_id="owner/repo",
        metadata={"commit_hash": "abc123"}
    )

    return chunks
```

## Adding a New Language (1 Step!)

### Just install the LSP server:

```bash
# Swift
brew install sourcekit-lsp

# Go
go install golang.org/x/tools/gopls@latest

# Rust
rustup component add rust-analyzer

# Ruby
gem install solargraph

# PHP
npm install -g intelephense
```

Then add to config:
```python
LSP_SERVERS[".rb"] = {
    "command": ["solargraph", "stdio"],
    "name": "ruby"
}
```

**Done!** No parsing code, no queries, no tree-sitter integration.

## Benefits

1. **Zero language-specific code**: LSP does all parsing
2. **Community maintained**: Language teams maintain LSP servers
3. **Rich metadata**: Types, docs, ranges all included
4. **Battle-tested**: Used by millions via VS Code
5. **Easy to extend**: Just install LSP server
6. **Accurate parsing**: LSP servers are authoritative for their language
7. **Works for everything**: Even esoteric languages if they have LSP

## LSP Servers Available

Almost every language has an LSP server:

| Language | Server | Install |
|----------|--------|---------|
| Python | pylsp | `pip install python-lsp-server` |
| JavaScript/TypeScript | typescript-language-server | `npm install -g typescript-language-server` |
| Swift | sourcekit-lsp | Built into Xcode / Swift toolchain |
| Go | gopls | `go install golang.org/x/tools/gopls@latest` |
| Rust | rust-analyzer | `rustup component add rust-analyzer` |
| Java | jdtls | Eclipse JDT Language Server |
| C/C++ | clangd | `brew install llvm` |
| Ruby | solargraph | `gem install solargraph` |
| PHP | intelephense | `npm install -g intelephense` |
| C# | omnisharp | OmniSharp server |
| Elixir | elixir-ls | ElixirLS |
| Haskell | hls | Haskell Language Server |
| Svelte | svelte-language-server | `npm install -g svelte-language-server` |

**And many more!** https://langserver.org/

## Why This Wins

### Before: Custom Parser for Each Language
```
Python parser: 300 lines
JavaScript parser: 250 lines
Swift parser: 200 lines
Svelte parser: 150 lines
...
Total: 1000+ lines, all fragile
```

### After: LSP-Based Universal Parser
```
LSP parser: 200 lines (works for ALL languages)
Config: 50 lines (just server commands)
Total: 250 lines, robust
```

## Performance Considerations

**Concern**: "Won't spawning LSP servers be slow?"

**Solution**: Pool and reuse servers
```python
class LSPServerPool:
    """Reuse LSP servers across files"""

    def __init__(self):
        self.servers = {}  # One server per language

    async def parse_repository(self, files: List[Path]):
        # Start servers once
        await self._start_all_servers()

        # Parse all files with the same servers
        for file in files:
            symbols = await self.get_symbols(file)
            ...

        # Shutdown servers after repo is done
        await self._shutdown_all_servers()
```

**Benchmarks** (estimated):
- Starting LSP server: 100-500ms (once per language)
- Getting symbols per file: 10-50ms
- For 1000 files: ~10-50 seconds (vs hours of dev time per language)

## Real-World Usage

This is exactly how tools like:
- **GitHub Copilot** understands your code
- **VS Code** provides symbol outline
- **Sourcegraph** does code navigation
- **GitHub's code search** finds definitions

We're leveraging the same infrastructure they use.

## Migration Path

1. **Implement LSPParser** alongside existing code_parser.py
2. **Add LSP servers for Python, JavaScript** (languages we already support)
3. **Test that output matches** current parsing
4. **Switch to LSP** for new languages (Swift, Go, etc.)
5. **Gradually migrate** existing languages
6. **Eventually deprecate** tree-sitter parsing

## Dependencies

```bash
# Python LSP library
pip install pygls lsprotocol

# Install LSP servers (per language)
pip install python-lsp-server  # Python
npm install -g typescript-language-server typescript  # JS/TS
# etc.
```

## The Win

**To add Swift support:**

Before (tree-sitter):
- Write 200 lines of Python to walk AST
- Learn Swift's AST node types
- Handle edge cases
- Test extensively
- Maintain as Swift evolves

After (LSP):
```bash
# Install server
brew install sourcekit-lsp

# Add config
LSP_SERVERS[".swift"] = {"command": ["sourcekit-lsp"], "name": "swift"}
```

**5 minutes vs 5 days.**

## This is the way.
