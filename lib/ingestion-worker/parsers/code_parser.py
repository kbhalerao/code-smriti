import hashlib
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from loguru import logger
import git

# Try to import tree-sitter, but handle missing languages gracefully
try:
    from tree_sitter import Parser, Language
    HAS_TREE_SITTER = True
except ImportError:
    HAS_TREE_SITTER = False
    logger.warning("tree-sitter not installed")

from config import WorkerConfig

config = WorkerConfig()


def should_skip_file(file_path: Path) -> bool:
    """
    Check if a file should be skipped during ingestion

    Skips:
    - Minified files (.min.js, .min.css)
    - Build artifacts (dist/, build/, .next/)
    - Dependencies (node_modules/)
    - Generated files (*generated*, *.g.dart, *.pb.go)
    - Large binary/media files
    """
    path_str = str(file_path)
    file_name = file_path.name

    # Skip minified files by extension
    if file_name.endswith('.min.js') or file_name.endswith('.min.css'):
        return True

    # Skip bundle files (often minified even without .min. extension)
    if any(keyword in file_name.lower() for keyword in ['bundle', 'vendor', 'chunk', 'runtime']):
        return True

    # Skip source maps
    if file_name.endswith('.map'):
        return True

    # Skip lock files (huge and not useful for search)
    if file_name in ['package-lock.json', 'yarn.lock', 'Cargo.lock', 'poetry.lock',
                     'Pipfile.lock', 'Gemfile.lock', 'go.sum', 'pnpm-lock.yaml']:
        return True

    # Skip build directories and static assets
    build_dirs = ['node_modules', 'dist', 'build', '__pycache__', '.next',
                  'target', 'vendor', '.venv', 'venv', '.git', '.svn',
                  'staticfiles', 'static', 'assets/vendor', 'public/vendor']
    if any(f'/{dir}/' in path_str or path_str.startswith(f'{dir}/') for dir in build_dirs):
        return True

    # Skip generated files
    if 'generated' in file_name.lower() or 'codegen' in file_name.lower():
        return True

    # Skip protocol buffer and generated code
    if file_name.endswith('.pb.go') or file_name.endswith('.g.dart'):
        return True

    # Skip very large files (>500KB - likely minified/bundled)
    try:
        file_size = file_path.stat().st_size
        if file_size > 500_000:
            logger.debug(f"Skipping large file: {file_name} ({file_size} bytes)")
            return True

        # For JS/CSS files, detect minified content by checking line length
        if file_name.endswith(('.js', '.css')) and file_size > 50_000:
            # Read first 10KB to check if minified
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                sample = f.read(10_000)
                lines = sample.split('\n')[:10]  # Check first 10 lines
                # If any line is super long (>500 chars), likely minified
                if any(len(line) > 500 for line in lines):
                    logger.debug(f"Skipping minified file (long lines): {file_name}")
                    return True
    except Exception as e:
        logger.debug(f"Error checking file size/content for {file_name}: {e}")
        pass

    return False


class CodeChunk:
    """Represents a parsed code chunk with metadata"""

    def __init__(
        self,
        repo_id: str,
        file_path: str,
        chunk_type: str,
        code_text: str,
        language: str,
        metadata: Dict
    ):
        # Generate deterministic chunk ID based on git commit, location, and content
        # This ensures uniqueness: same file + same code = same ID
        # Different code in same file = different ID (via content hash)
        commit_hash = metadata.get("commit_hash", "no_commit")

        # Content fingerprint: first 16 chars of SHA256 hash of code
        content_hash = hashlib.sha256(code_text.encode()).hexdigest()[:16]

        # Chunk ID: hash(repo:file:commit:content_fingerprint)
        # This guarantees uniqueness while supporting incremental updates at file level
        chunk_key = f"{repo_id}:{file_path}:{commit_hash}:{content_hash}"
        self.chunk_id = hashlib.sha256(chunk_key.encode()).hexdigest()

        # Debug logging (sample for verification)
        import random
        if random.random() < 0.0002:  # Log ~0.02% of chunks
            logger.debug(f"Chunk ID: {self.chunk_id[:16]}... from key: {chunk_key}")

        self.type = "code_chunk"
        self.repo_id = repo_id
        self.file_path = file_path
        self.chunk_type = chunk_type  # function, class, import, etc.
        self.code_text = code_text
        self.language = language
        self.metadata = metadata
        self.embedding = None  # Will be populated by EmbeddingGenerator
        self.created_at = datetime.utcnow().isoformat()

    def to_dict(self) -> Dict:
        """Convert to dictionary for storage

        Note: Removes commit_message from metadata before storage
        (commit messages are stored in separate CommitChunk documents)
        """
        # Create a copy of metadata without commit_message
        storage_metadata = {k: v for k, v in self.metadata.items() if k != "commit_message"}

        return {
            "chunk_id": self.chunk_id,
            "type": self.type,
            "repo_id": self.repo_id,
            "file_path": self.file_path,
            "chunk_type": self.chunk_type,
            "content": self.code_text,  # Unified schema: code_text -> content
            "language": self.language,
            "metadata": storage_metadata,  # Filtered metadata
            "embedding": self.embedding,
            "created_at": self.created_at
        }


class CodeParser:
    """
    Parses code files using tree-sitter for semantic chunking
    """

    # Max chunk size to stay within Nomic's 8192 token limit
    # ~0.75 tokens per char for code, so 6000 chars ≈ 4500-7500 tokens (safe margin)
    MAX_CHUNK_SIZE = 6000  # characters

    def __init__(self):
        """Initialize parsers for supported languages"""
        logger.info("Initializing code parsers")

        # Initialize tree-sitter parsers
        self.parsers = {}

        # Try to load tree-sitter-languages
        try:
            from tree_sitter_languages import get_parser
            self.parsers = {
                "python": get_parser("python"),
                "javascript": get_parser("javascript"),
                "typescript": get_parser("typescript"),
                "html": get_parser("html"),
                "css": get_parser("css"),
            }

            # Try to add Svelte parser from tree-sitter-svelte
            try:
                import tree_sitter_svelte
                from tree_sitter import Parser
                svelte_parser = Parser()
                svelte_parser.set_language(tree_sitter_svelte.language())
                self.parsers["svelte"] = svelte_parser
            except ImportError:
                logger.debug("tree-sitter-svelte not available, will use regex-based Svelte parser")
            except Exception as e:
                logger.debug(f"Could not load Svelte parser: {e}")

            logger.info(f"✓ Tree-sitter parsers loaded: {list(self.parsers.keys())}")
        except ImportError:
            logger.warning("tree-sitter-languages not available, will use regex fallback")
        except Exception as e:
            logger.warning(f"Failed to load tree-sitter parsers: {e}, will use regex fallback")

        logger.info(f"✓ Parsers initialized: {list(self.parsers.keys()) if self.parsers else 'regex fallback mode'}")

    def create_metadata_chunk(self, file_path: str, content: str, language: str, git_metadata: Dict, repo_id: str) -> CodeChunk:
        """
        Create a metadata chunk for the file.
        Contains module docstring, imports, and list of top-level symbols.
        """
        lines = content.split('\n')
        # First 50 lines or until first class/def
        preview_lines = []
        for line in lines[:50]:
            if line.strip().startswith(('class ', 'def ', 'func ')):
                break
            preview_lines.append(line)
        
        preview = "\n".join(preview_lines)
        
        # TODO: Extract symbols using tree-sitter (simplified for now)
        # In a full implementation, we would parse the tree to get all top-level names
        
        return CodeChunk(
            repo_id=repo_id,
            file_path=file_path,
            chunk_type="file_metadata",
            code_text=preview,
            language=language,
            metadata={
                **git_metadata,
                "file_size": len(content),
                "line_count": len(lines)
            }
        )

    def add_context_header(self, code_text: str, relative_path: str, container_name: str = None) -> str:
        """
        Prepend context header to code chunk.
        """
        header = f"# Context: {relative_path}\n"
        if container_name:
            header += f"# Inside: {container_name}\n"
        return header + code_text

    def truncate_chunk_text(self, text: str, context: str = "") -> str:
        """
        Truncate oversized chunks to stay within Nomic's 8192 token limit
        Keeps beginning and end for context, indicates truncation
        Logs warning when truncation occurs

        Args:
            text: Original chunk text
            context: Optional context for logging (e.g., "file.py:ClassName")

        Returns:
            Truncated text if needed
        """
        if len(text) <= self.MAX_CHUNK_SIZE:
            return text

        # Keep first 4500 and last 1400 chars with truncation marker (≈6K total)
        keep_start = 4500
        keep_end = 1400
        chars_truncated = len(text) - keep_start - keep_end

        logger.warning(
            f"Truncating oversized chunk: {len(text)} chars → {self.MAX_CHUNK_SIZE} chars "
            f"(removed {chars_truncated} chars) {context}"
        )

        truncated = (
            text[:keep_start] +
            f"\n... [truncated {chars_truncated} chars] ...\n" +
            text[-keep_end:]
        )
        return truncated

    def detect_language(self, file_path: Path) -> Optional[str]:
        """
        Detect programming language from file extension

        Args:
            file_path: Path to the file

        Returns:
            Language name or None if not supported
        """
        extension_map = {
            ".py": "python",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
            ".svelte": "svelte",
            ".vue": "vue",
            ".html": "html",
            ".css": "css",
            ".scss": "css",
            ".sass": "css",
        }

        return extension_map.get(file_path.suffix)

    def get_git_metadata(self, repo_path: Path, file_path: str) -> Dict:
        """
        Extract git metadata for a file
        Note: commit_message is stored separately in CommitChunk documents

        Args:
            repo_path: Path to the repository
            file_path: Relative path to the file

        Returns:
            Dictionary with commit information (excluding message)
        """
        try:
            repo = git.Repo(repo_path)

            # Get the latest commit that modified this file
            commits = list(repo.iter_commits(paths=file_path, max_count=1))

            if commits:
                commit = commits[0]
                return {
                    "commit_hash": commit.hexsha,
                    "commit_date": commit.committed_datetime.isoformat(),
                    "author": commit.author.email,
                    # Note: commit_message stored in separate CommitChunk for deduplication
                    # Temporary: keep it for CommitParser extraction, will be removed from storage
                    "commit_message": commit.message.strip()
                }
        except Exception as e:
            logger.warning(f"Could not extract git metadata for {file_path}: {e}")

        return {}

    async def parse_python_file(
        self,
        file_path: Path,
        content: str,
        repo_id: str,
        relative_path: str,
        git_metadata: Dict
    ) -> List[CodeChunk]:
        """
        Parse a Python file into semantic chunks

        Args:
            file_path: Path to the file
            content: File content
            repo_id: Repository identifier
            relative_path: Relative path within repo
            git_metadata: Git commit metadata

        Returns:
            List of CodeChunk objects
        """
        chunks = []

        try:
            # Fallback if parser not available
            if "python" not in self.parsers:
                logger.warning("Parser for python not found, using regex fallback")
                return self._regex_parse_python(content, relative_path, repo_id, git_metadata)

            parser = self.parsers["python"]
            tree = parser.parse(bytes(content, "utf8"))
            root = tree.root_node

            # Extract functions
            for node in root.children:
                if node.type == "function_definition":
                    func_name_node = node.child_by_field_name("name")
                    func_name = func_name_node.text.decode("utf8") if func_name_node else "unknown"

                    # Get parameters
                    params_node = node.child_by_field_name("parameters")
                    params = params_node.text.decode("utf8") if params_node else "()"

                    # Get docstring if available
                    docstring = None
                    body = node.child_by_field_name("body")
                    if body and body.children:
                        first_stmt = body.children[0]
                        if first_stmt.type == "expression_statement":
                            expr = first_stmt.children[0]
                            if expr.type == "string":
                                docstring = expr.text.decode("utf8")

                    code_text = content[node.start_byte:node.end_byte]
                    code_text = self.truncate_chunk_text(
                        code_text,
                        context=f"in {relative_path}::{func_name}()"
                    )
                    
                    # Add context header
                    code_text = self.add_context_header(code_text, relative_path)

                    metadata = {
                        "language": "python",
                        "function_name": func_name,
                        "parameters": params,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        "docstring": docstring,
                        **git_metadata
                    }

                    chunks.append(CodeChunk(
                        repo_id=repo_id,
                        file_path=relative_path,
                        chunk_type="function",
                        code_text=code_text,
                        language="python",
                        metadata=metadata
                    ))

                # Extract classes and their methods
                elif node.type == "class_definition":
                    class_name_node = node.child_by_field_name("name")
                    class_name = class_name_node.text.decode("utf8") if class_name_node else "unknown"

                    # Get class body to extract methods
                    body_node = node.child_by_field_name("body")

                    if body_node:
                        # Create class header chunk (definition + docstring, not full body)
                        # Extract just the class signature + docstring
                        class_header_end = body_node.start_byte
                        class_header = content[node.start_byte:class_header_end].rstrip()

                        # Get class docstring if available
                        class_docstring = None
                        if body_node.children and len(body_node.children) > 1:
                            first_stmt = body_node.children[1]  # Skip the colon
                            if first_stmt.type == "expression_statement":
                                expr = first_stmt.children[0] if first_stmt.children else None
                                if expr and expr.type == "string":
                                    class_docstring = expr.text.decode("utf8")

                        # Add docstring to header if present
                        if class_docstring:
                            class_header += f":\n{class_docstring}"

                        # Create class header chunk
                        chunks.append(CodeChunk(
                            repo_id=repo_id,
                            file_path=relative_path,
                            chunk_type="class",
                            code_text=self.add_context_header(class_header[:6000], relative_path),  # Keep within limit
                            language="python",
                            metadata={
                                "language": "python",
                                "class_name": class_name,
                                "start_line": node.start_point[0] + 1,
                                "end_line": body_node.start_point[0] + 2,
                                "docstring": class_docstring,
                                **git_metadata
                            }
                        ))

                        # Extract each method in the class separately
                        for child in body_node.children:
                            if child.type == "function_definition":
                                method_name_node = child.child_by_field_name("name")
                                method_name = method_name_node.text.decode("utf8") if method_name_node else "unknown"

                                # Get method parameters
                                params_node = child.child_by_field_name("parameters")
                                params = params_node.text.decode("utf8") if params_node else "()"

                                # Get method docstring
                                method_docstring = None
                                method_body = child.child_by_field_name("body")
                                if method_body and method_body.children:
                                    first_stmt = method_body.children[0]
                                    if first_stmt.type == "expression_statement":
                                        expr = first_stmt.children[0]
                                        if expr.type == "string":
                                            method_docstring = expr.text.decode("utf8")

                                # Get method code
                                method_code = content[child.start_byte:child.end_byte]
                                method_code = self.truncate_chunk_text(
                                    method_code,
                                    context=f"in {relative_path}::{class_name}.{method_name}()"
                                )
                                
                                # Add context header
                                method_code = self.add_context_header(method_code, relative_path, class_name)

                                # Create method chunk
                                chunks.append(CodeChunk(
                                    repo_id=repo_id,
                                    file_path=relative_path,
                                    chunk_type="method",
                                    code_text=method_code,
                                    language="python",
                                    metadata={
                                        "language": "python",
                                        "class_name": class_name,
                                        "function_name": method_name,
                                        "method_name": method_name,
                                        "parameters": params,
                                        "start_line": child.start_point[0] + 1,
                                        "end_line": child.end_point[0] + 1,
                                        "docstring": method_docstring,
                                        **git_metadata
                                    }
                                ))
                    else:
                        # Class without body (shouldn't happen, but handle it)
                        code_text = content[node.start_byte:node.end_byte]
                        chunks.append(CodeChunk(
                            repo_id=repo_id,
                            file_path=relative_path,
                            chunk_type="class",
                            code_text=code_text[:6000],
                            language="python",
                            metadata={
                                "language": "python",
                                "class_name": class_name,
                                "start_line": node.start_point[0] + 1,
                                "end_line": node.end_point[0] + 1,
                                **git_metadata
                            }
                        ))

        except Exception as e:
            logger.error(f"Error parsing Python file {file_path}: {e}")

        return chunks

    def _regex_parse_python(self, content: str, relative_path: str, repo_id: str, git_metadata: Dict) -> List[CodeChunk]:
        """Fallback regex parser for Python"""
        import re
        chunks = []
        
        # Find functions
        func_pattern = re.compile(r'^def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', re.MULTILINE)
        for match in func_pattern.finditer(content):
            func_name = match.group(1)
            start_idx = match.start()
            
            # Simple heuristic for end index (indentation based or next def)
            # This is imperfect but better than nothing
            next_match = func_pattern.search(content, match.end())
            end_idx = next_match.start() if next_match else len(content)
            
            code_text = content[start_idx:end_idx]
            code_text = self.truncate_chunk_text(code_text, context=f"in {relative_path}::{func_name}()")
            code_text = self.add_context_header(code_text, relative_path)
            
            chunks.append(CodeChunk(
                repo_id=repo_id,
                file_path=relative_path,
                chunk_type="function",
                code_text=code_text,
                language="python",
                metadata={
                    "language": "python",
                    "function_name": func_name,
                    "start_line": content.count('\n', 0, start_idx) + 1,
                    "end_line": content.count('\n', 0, end_idx) + 1,
                    **git_metadata
                }
            ))
            
        # Find classes
        class_pattern = re.compile(r'^class\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*[:\(]', re.MULTILINE)
        for match in class_pattern.finditer(content):
            class_name = match.group(1)
            start_idx = match.start()
            next_match = class_pattern.search(content, match.end())
            end_idx = next_match.start() if next_match else len(content)
            
            code_text = content[start_idx:end_idx]
            code_text = self.truncate_chunk_text(code_text, context=f"in {relative_path}::class {class_name}")
            code_text = self.add_context_header(code_text, relative_path)
            
            chunks.append(CodeChunk(
                repo_id=repo_id,
                file_path=relative_path,
                chunk_type="class",
                code_text=code_text,
                language="python",
                metadata={
                    "language": "python",
                    "class_name": class_name,
                    "start_line": content.count('\n', 0, start_idx) + 1,
                    "end_line": content.count('\n', 0, end_idx) + 1,
                    **git_metadata
                }
            ))
            
        return chunks

    def _regex_parse_javascript(self, content: str, relative_path: str, repo_id: str, git_metadata: Dict, is_typescript: bool) -> List[CodeChunk]:
        """Fallback regex parser for JS/TS"""
        import re
        chunks = []
        lang = "typescript" if is_typescript else "javascript"
        
        # Find functions (function foo() {})
        func_pattern = re.compile(r'function\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(', re.MULTILINE)
        for match in func_pattern.finditer(content):
            func_name = match.group(1)
            start_idx = match.start()
            # Very rough end detection
            end_idx = content.find('}', start_idx) + 1
            if end_idx <= 0: end_idx = len(content)
            
            code_text = content[start_idx:end_idx]
            code_text = self.truncate_chunk_text(code_text, context=f"in {relative_path}::{func_name}()")
            code_text = self.add_context_header(code_text, relative_path)
            
            chunks.append(CodeChunk(
                repo_id=repo_id,
                file_path=relative_path,
                chunk_type="function",
                code_text=code_text,
                language=lang,
                metadata={
                    "language": lang,
                    "function_name": func_name,
                    "start_line": content.count('\n', 0, start_idx) + 1,
                    "end_line": content.count('\n', 0, end_idx) + 1,
                    **git_metadata
                }
            ))

        # Find const/let exports (export const MyComponent = ...)
        export_pattern = re.compile(r'export\s+(?:const|let|var)\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*=', re.MULTILINE)
        for match in export_pattern.finditer(content):
            name = match.group(1)
            start_idx = match.start()
            end_idx = content.find(';', start_idx) + 1
            if end_idx <= 0: end_idx = len(content)
            
            code_text = content[start_idx:end_idx]
            code_text = self.truncate_chunk_text(code_text, context=f"in {relative_path}::{name}")
            code_text = self.add_context_header(code_text, relative_path)
            
            chunks.append(CodeChunk(
                repo_id=repo_id,
                file_path=relative_path,
                chunk_type="variable",
                code_text=code_text,
                language=lang,
                metadata={
                    "language": lang,
                    "function_name": name,
                    "start_line": content.count('\n', 0, start_idx) + 1,
                    "end_line": content.count('\n', 0, end_idx) + 1,
                    **git_metadata
                }
            ))
            
        return chunks

    async def parse_javascript_file(
        self,
        file_path: Path,
        content: str,
        repo_id: str,
        relative_path: str,
        git_metadata: Dict,
        is_typescript: bool = False
    ) -> List[CodeChunk]:
        """
        Parse a JavaScript/TypeScript file into semantic chunks
        """
        chunks = []

        try:
            parser_key = "typescript" if is_typescript else "javascript"
            
            # Fallback if parser not available
            if parser_key not in self.parsers:
                logger.warning(f"Parser for {parser_key} not found, using regex fallback")
                return self._regex_parse_javascript(content, relative_path, repo_id, git_metadata, is_typescript)

            parser = self.parsers[parser_key]
            tree = parser.parse(bytes(content, "utf8"))
            root = tree.root_node

            def extract_functions(node, parent_chunks):
                """Recursively extract function declarations"""

                # Function declaration
                if node.type in ["function_declaration", "function"]:
                    name_node = node.child_by_field_name("name")
                    func_name = name_node.text.decode("utf8") if name_node else "anonymous"

                    code_text = content[node.start_byte:node.end_byte]
                    code_text = self.truncate_chunk_text(
                        code_text,
                        context=f"in {relative_path}::{func_name}()"
                    )
                    
                    # Add context header
                    code_text = self.add_context_header(code_text, relative_path)

                    metadata = {
                        "language": parser_key,
                        "function_name": func_name,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        **git_metadata
                    }

                    parent_chunks.append(CodeChunk(
                        repo_id=repo_id,
                        file_path=relative_path,
                        chunk_type="function",
                        code_text=code_text,
                        language=parser_key,
                        metadata=metadata
                    ))

                # Arrow function
                elif node.type == "arrow_function":
                    code_text = content[node.start_byte:node.end_byte]
                    code_text = self.truncate_chunk_text(
                        code_text,
                        context=f"in {relative_path}::arrow_function"
                    )
                    
                    # Add context header
                    code_text = self.add_context_header(code_text, relative_path)

                    metadata = {
                        "language": parser_key,
                        "function_name": "arrow_function",
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        **git_metadata
                    }

                    parent_chunks.append(CodeChunk(
                        repo_id=repo_id,
                        file_path=relative_path,
                        chunk_type="arrow_function",
                        code_text=code_text,
                        language=parser_key,
                        metadata=metadata
                    ))

                # Class declaration
                elif node.type == "class_declaration":
                    name_node = node.child_by_field_name("name")
                    class_name = name_node.text.decode("utf8") if name_node else "anonymous"

                    code_text = content[node.start_byte:node.end_byte]
                    code_text = self.truncate_chunk_text(
                        code_text,
                        context=f"in {relative_path}::class {class_name}"
                    )
                    
                    # Add context header
                    code_text = self.add_context_header(code_text, relative_path)

                    metadata = {
                        "language": parser_key,
                        "class_name": class_name,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        **git_metadata
                    }

                    parent_chunks.append(CodeChunk(
                        repo_id=repo_id,
                        file_path=relative_path,
                        chunk_type="class",
                        code_text=code_text,
                        language=parser_key,
                        metadata=metadata
                    ))

                # Recurse into children
                for child in node.children:
                    extract_functions(child, parent_chunks)

            extract_functions(root, chunks)

        except Exception as e:
            logger.error(f"Error parsing {'TypeScript' if is_typescript else 'JavaScript'} file {file_path}: {e}")

        return chunks

    async def parse_svelte_file(
        self,
        file_path: Path,
        content: str,
        repo_id: str,
        relative_path: str,
        git_metadata: Dict
    ) -> List[CodeChunk]:
        """
        Parse Svelte file by extracting script, template, and style sections

        Args:
            file_path: Path to the file
            content: File content
            repo_id: Repository identifier
            relative_path: Relative path within repo
            git_metadata: Git commit metadata

        Returns:
            List of CodeChunk objects
        """
        import re
        chunks = []

        # Extract <script> section (can be TS or JS)
        script_match = re.search(r'<script(?:\s+lang=["\']ts["\'])?(?:\s+context=["\']module["\'])?\s*>(.*?)</script>', content, re.DOTALL)
        if script_match:
            script_content = script_match.group(1).strip()
            is_typescript = 'lang="ts"' in script_match.group(0) or "lang='ts'" in script_match.group(0)

            if script_content:
                # Parse script section as JS/TS
                try:
                    script_chunks = await self.parse_javascript_file(
                        file_path, script_content, repo_id, relative_path + " <script>",
                        git_metadata, is_typescript=is_typescript
                    )
                    chunks.extend(script_chunks)
                except Exception as e:
                    logger.debug(f"Could not parse Svelte script section: {e}")
                    # Fallback: create single chunk for script
                    chunks.append(CodeChunk(
                        repo_id=repo_id,
                        file_path=relative_path,
                        chunk_type="svelte_script",
                        code_text=self.truncate_chunk_text(script_content, f"in {relative_path} <script>"),
                        language="svelte",
                        metadata={
                            "language": "svelte",
                            "section": "script",
                            **git_metadata
                        }
                    ))

        # Extract <style> section
        style_match = re.search(r'<style(?:\s+lang=["\'](?:scss|sass)["\'])?\s*>(.*?)</style>', content, re.DOTALL)
        if style_match:
            style_content = style_match.group(1).strip()
            if style_content:
                chunks.append(CodeChunk(
                    repo_id=repo_id,
                    file_path=relative_path,
                    chunk_type="svelte_style",
                    code_text=self.truncate_chunk_text(style_content, f"in {relative_path} <style>"),
                    language="svelte",
                    metadata={
                        "language": "svelte",
                        "section": "style",
                        **git_metadata
                    }
                ))

        # Extract template (everything outside script/style)
        template = content
        template = re.sub(r'<script(?:\s+[^>]*)?>.*?</script>', '', template, flags=re.DOTALL)
        template = re.sub(r'<style(?:\s+[^>]*)?>.*?</style>', '', template, flags=re.DOTALL)
        template = template.strip()

        if template:
            chunks.append(CodeChunk(
                repo_id=repo_id,
                file_path=relative_path,
                chunk_type="svelte_template",
                code_text=self.truncate_chunk_text(template, f"in {relative_path} <template>"),
                language="svelte",
                metadata={
                    "language": "svelte",
                    "section": "template",
                    **git_metadata
                }
            ))

        return chunks

    async def parse_file(
        self,
        file_path: Path,
        repo_path: Path,
        repo_id: str
    ) -> List[CodeChunk]:
        """
        Parse a code file into chunks

        Args:
            file_path: Path to the file
            repo_path: Path to the repository root
            repo_id: Repository identifier

        Returns:
            List of CodeChunk objects
        """
        language = self.detect_language(file_path)

        if not language:
            return []

        try:
            # Read file content
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Get relative path
            relative_path = str(file_path.relative_to(repo_path))

            # Get git metadata
            git_metadata = self.get_git_metadata(repo_path, relative_path)

            # 1. Create metadata chunk (for all files)
            metadata_chunk = self.create_metadata_chunk(
                relative_path, content, language, git_metadata, repo_id
            )
            chunks = [metadata_chunk]

            # 2. Check file size - if small, index as one chunk
            if len(content) < 6000:
                chunks.append(CodeChunk(
                    repo_id=repo_id,
                    file_path=relative_path,
                    chunk_type="code",
                    code_text=self.add_context_header(content, relative_path),
                    language=language,
                    metadata=git_metadata
                ))
                return chunks

            # 3. Large file: split with tree-sitter
            if language == "python":
                chunks.extend(await self.parse_python_file(
                    file_path, content, repo_id, relative_path, git_metadata
                ))
            elif language == "javascript":
                chunks.extend(await self.parse_javascript_file(
                    file_path, content, repo_id, relative_path, git_metadata, is_typescript=False
                ))
            elif language == "typescript":
                chunks.extend(await self.parse_javascript_file(
                    file_path, content, repo_id, relative_path, git_metadata, is_typescript=True
                ))
            elif language == "svelte" or language == "vue":
                chunks.extend(await self.parse_svelte_file(
                    file_path, content, repo_id, relative_path, git_metadata
                ))
            elif language == "html" or language == "css":
                # For HTML/CSS, create single file chunk
                chunks.append(self.create_metadata_chunk(
                    relative_path, content, language, git_metadata, repo_id
                ))
            
            return chunks

        except Exception as e:
            logger.error(f"Error parsing file {file_path}: {e}")

        return []

    async def parse_repository(
        self,
        repo_path: Path,
        repo_id: str
    ) -> List[CodeChunk]:
        """
        Parse all code files in a repository

        Args:
            repo_path: Path to the repository
            repo_id: Repository identifier

        Returns:
            List of all CodeChunk objects from the repository
        """
        all_chunks = []

        logger.info(f"Parsing repository: {repo_path}")

        # Find all code files
        for ext in config.supported_code_extensions:
            for file_path in repo_path.rglob(f"*{ext}"):
                # Skip junk files using comprehensive filter
                if should_skip_file(file_path):
                    logger.debug(f"Skipping junk file: {file_path.name}")
                    continue

                chunks = await self.parse_file(file_path, repo_path, repo_id)
                all_chunks.extend(chunks)

        logger.info(f"Parsed {len(all_chunks)} code chunks from {repo_path}")
        return all_chunks
