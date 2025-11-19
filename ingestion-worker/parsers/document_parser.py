"""
Document Parser for markdown, text, and configuration files
"""

import hashlib
import json
import yaml
from pathlib import Path
from typing import List, Dict
from datetime import datetime

from loguru import logger
import frontmatter
import git

from config import WorkerConfig

config = WorkerConfig()


class DocumentChunk:
    """Represents a parsed document chunk"""

    def __init__(
        self,
        repo_id: str,
        file_path: str,
        doc_type: str,
        content: str,
        metadata: Dict
    ):
        # Generate deterministic chunk ID based on git commit and content
        # Documents are stored as whole files
        commit_hash = metadata.get("commit_hash", "no_commit")

        # Content fingerprint: first 16 chars of SHA256 hash
        content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]

        # Chunk ID: hash(repo:file:commit:content_fingerprint)
        # This guarantees uniqueness and supports file-level incremental updates
        chunk_key = f"{repo_id}:{file_path}:{commit_hash}:{content_hash}"
        self.chunk_id = hashlib.sha256(chunk_key.encode()).hexdigest()

        self.type = "document"
        self.repo_id = repo_id
        self.file_path = file_path
        self.doc_type = doc_type  # markdown, json, yaml, text
        self.content = content
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
            "doc_type": self.doc_type,
            "content": self.content,
            "metadata": storage_metadata,  # Filtered metadata
            "embedding": self.embedding,
            "created_at": self.created_at
        }


class DocumentParser:
    """
    Parses documentation files (markdown, text, JSON, YAML)
    """

    def __init__(self):
        """Initialize document parser"""
        logger.info("Initializing document parser")

    def get_git_metadata(self, repo_path: Path, file_path: str) -> Dict:
        """
        Extract git metadata for a file
        Note: commit_message is stored separately in CommitChunk documents

        Args:
            repo_path: Path to the repository
            file_path: Relative path to the file

        Returns:
            Dictionary with commit information (message kept for CommitParser extraction)
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
                    # Note: commit_message kept temporarily for CommitParser extraction
                    # Removed before storage in to_dict()
                    "commit_message": commit.message.strip()
                }
        except Exception as e:
            logger.warning(f"Could not extract git metadata for {file_path}: {e}")

        return {}

    async def parse_markdown(
        self,
        file_path: Path,
        content: str,
        repo_id: str,
        relative_path: str,
        git_metadata: Dict
    ) -> List[DocumentChunk]:
        """
        Parse a markdown file, extracting frontmatter and content

        Args:
            file_path: Path to the file
            content: File content
            repo_id: Repository identifier
            relative_path: Relative path within repo

        Returns:
            List of DocumentChunk objects
        """
        chunks = []

        try:
            # Parse frontmatter if present
            post = frontmatter.loads(content)

            metadata = {
                "format": "markdown",
                "frontmatter": post.metadata if post.metadata else {},
                "file_size": len(content),
                **git_metadata
            }

            # Extract hashtags from frontmatter or content
            hashtags = []
            if "tags" in post.metadata:
                hashtags = post.metadata.get("tags", [])
            elif "hashtags" in post.metadata:
                hashtags = post.metadata.get("hashtags", [])

            # Simple hashtag extraction from content
            import re
            content_hashtags = re.findall(r'#(\w+)', post.content)
            hashtags.extend(content_hashtags)
            hashtags = list(set(hashtags))  # Remove duplicates

            metadata["hashtags"] = hashtags

            # For large markdown files, we could split by headers
            # For now, store as single chunk
            chunks.append(DocumentChunk(
                repo_id=repo_id,
                file_path=relative_path,
                doc_type="markdown",
                content=post.content,
                metadata=metadata
            ))

        except Exception as e:
            logger.error(f"Error parsing markdown {file_path}: {e}")

        return chunks

    async def parse_json(
        self,
        file_path: Path,
        content: str,
        repo_id: str,
        relative_path: str,
        git_metadata: Dict
    ) -> List[DocumentChunk]:
        """Parse a JSON file"""
        chunks = []

        try:
            # Parse JSON
            data = json.loads(content)

            metadata = {
                "format": "json",
                "keys": list(data.keys()) if isinstance(data, dict) else [],
                "file_size": len(content),
                **git_metadata
            }

            # Store pretty-printed JSON as content
            formatted_content = json.dumps(data, indent=2)

            chunks.append(DocumentChunk(
                repo_id=repo_id,
                file_path=relative_path,
                doc_type="json",
                content=formatted_content,
                metadata=metadata
            ))

        except Exception as e:
            logger.error(f"Error parsing JSON {file_path}: {e}")

        return chunks

    async def parse_yaml(
        self,
        file_path: Path,
        content: str,
        repo_id: str,
        relative_path: str,
        git_metadata: Dict
    ) -> List[DocumentChunk]:
        """Parse a YAML file"""
        chunks = []

        try:
            # Parse YAML
            data = yaml.safe_load(content)

            metadata = {
                "format": "yaml",
                "keys": list(data.keys()) if isinstance(data, dict) else [],
                "file_size": len(content),
                **git_metadata
            }

            chunks.append(DocumentChunk(
                repo_id=repo_id,
                file_path=relative_path,
                doc_type="yaml",
                content=content,
                metadata=metadata
            ))

        except Exception as e:
            logger.error(f"Error parsing YAML {file_path}: {e}")

        return chunks

    async def parse_text(
        self,
        file_path: Path,
        content: str,
        repo_id: str,
        relative_path: str,
        git_metadata: Dict
    ) -> List[DocumentChunk]:
        """Parse a plain text file"""
        chunks = []

        try:
            metadata = {
                "format": "text",
                "line_count": len(content.splitlines()),
                "file_size": len(content),
                **git_metadata
            }

            chunks.append(DocumentChunk(
                repo_id=repo_id,
                file_path=relative_path,
                doc_type="text",
                content=content,
                metadata=metadata
            ))

        except Exception as e:
            logger.error(f"Error parsing text {file_path}: {e}")

        return chunks

    async def parse_file(
        self,
        file_path: Path,
        repo_path: Path,
        repo_id: str
    ) -> List[DocumentChunk]:
        """
        Parse a document file

        Args:
            file_path: Path to the file
            repo_path: Path to the repository root
            repo_id: Repository identifier

        Returns:
            List of DocumentChunk objects
        """
        try:
            # Read file content
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Get relative path
            relative_path = str(file_path.relative_to(repo_path))

            # Get git metadata for this file
            git_metadata = self.get_git_metadata(repo_path, relative_path)

            # Determine file type and parse accordingly
            suffix = file_path.suffix.lower()

            if suffix == ".md":
                return await self.parse_markdown(file_path, content, repo_id, relative_path, git_metadata)
            elif suffix == ".json":
                return await self.parse_json(file_path, content, repo_id, relative_path, git_metadata)
            elif suffix in [".yaml", ".yml"]:
                return await self.parse_yaml(file_path, content, repo_id, relative_path, git_metadata)
            elif suffix == ".txt":
                return await self.parse_text(file_path, content, repo_id, relative_path, git_metadata)

        except Exception as e:
            logger.error(f"Error parsing document {file_path}: {e}")

        return []

    async def parse_repository(
        self,
        repo_path: Path,
        repo_id: str
    ) -> List[DocumentChunk]:
        """
        Parse all document files in a repository

        Args:
            repo_path: Path to the repository
            repo_id: Repository identifier

        Returns:
            List of all DocumentChunk objects from the repository
        """
        all_chunks = []

        logger.info(f"Parsing documents in repository: {repo_path}")

        # Find all document files
        for ext in config.supported_doc_extensions:
            for file_path in repo_path.rglob(f"*{ext}"):
                # Skip common ignored directories
                if any(part in file_path.parts for part in [".git", "node_modules", "__pycache__", "venv", ".venv"]):
                    continue

                chunks = await self.parse_file(file_path, repo_path, repo_id)
                all_chunks.extend(chunks)

        logger.info(f"Parsed {len(all_chunks)} document chunks from {repo_path}")
        return all_chunks
