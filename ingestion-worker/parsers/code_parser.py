"""
Code Parser using tree-sitter for semantic code chunking
Supports JavaScript, TypeScript, and Python
Part of CodeSmriti - Smriti (स्मृति): memory/remembrance
"""

import hashlib
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime

from loguru import logger
from tree_sitter_languages import get_parser
import git

from config import WorkerConfig

config = WorkerConfig()


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
            "code_text": self.code_text,
            "language": self.language,
            "metadata": storage_metadata,  # Filtered metadata
            "embedding": self.embedding,
            "created_at": self.created_at
        }


class CodeParser:
    """
    Parses code files using tree-sitter for semantic chunking
    """

    def __init__(self):
        """Initialize parsers for supported languages"""
        logger.info("Initializing code parsers")

        # Initialize tree-sitter parsers
        self.parsers = {
            "python": get_parser("python"),
            "javascript": get_parser("javascript"),
            "typescript": get_parser("typescript"),
        }

        logger.info(f"✓ Parsers loaded: {list(self.parsers.keys())}")

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

                # Extract classes
                elif node.type == "class_definition":
                    class_name_node = node.child_by_field_name("name")
                    class_name = class_name_node.text.decode("utf8") if class_name_node else "unknown"

                    code_text = content[node.start_byte:node.end_byte]

                    metadata = {
                        "language": "python",
                        "class_name": class_name,
                        "start_line": node.start_point[0] + 1,
                        "end_line": node.end_point[0] + 1,
                        **git_metadata
                    }

                    chunks.append(CodeChunk(
                        repo_id=repo_id,
                        file_path=relative_path,
                        chunk_type="class",
                        code_text=code_text,
                        language="python",
                        metadata=metadata
                    ))

        except Exception as e:
            logger.error(f"Error parsing Python file {file_path}: {e}")

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

        Args:
            file_path: Path to the file
            content: File content
            repo_id: Repository identifier
            relative_path: Relative path within repo
            git_metadata: Git commit metadata
            is_typescript: Whether this is a TypeScript file

        Returns:
            List of CodeChunk objects
        """
        chunks = []

        try:
            parser_key = "typescript" if is_typescript else "javascript"
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

            # Parse based on language
            if language == "python":
                return await self.parse_python_file(
                    file_path, content, repo_id, relative_path, git_metadata
                )
            elif language == "javascript":
                return await self.parse_javascript_file(
                    file_path, content, repo_id, relative_path, git_metadata, is_typescript=False
                )
            elif language == "typescript":
                return await self.parse_javascript_file(
                    file_path, content, repo_id, relative_path, git_metadata, is_typescript=True
                )

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
                # Skip common ignored directories
                if any(part in file_path.parts for part in [".git", "node_modules", "__pycache__", "venv", ".venv"]):
                    continue

                chunks = await self.parse_file(file_path, repo_path, repo_id)
                all_chunks.extend(chunks)

        logger.info(f"Parsed {len(all_chunks)} code chunks from {repo_path}")
        return all_chunks
