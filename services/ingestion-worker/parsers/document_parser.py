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
from parsers.code_parser import should_skip_file

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

    def split_markdown_by_headers(self, content: str) -> List[tuple]:
        """
        Split markdown content by headers (# ## ### etc.)

        Returns:
            List of (header_text, section_content, header_level) tuples
        """
        import re

        lines = content.splitlines(keepends=True)
        sections = []
        current_header = None
        current_header_level = 0
        current_section_lines = []

        # Regex for markdown headers: # Header, ## Header, etc.
        header_pattern = re.compile(r'^(#{1,6})\s+(.+)$')

        for line in lines:
            match = header_pattern.match(line.strip())

            if match:
                # Save previous section if exists
                if current_header is not None or current_section_lines:
                    section_content = ''.join(current_section_lines)
                    sections.append((current_header, section_content, current_header_level))

                # Start new section
                current_header_level = len(match.group(1))  # Count # characters
                current_header = match.group(2).strip()
                current_section_lines = [line]  # Include the header line
            else:
                # Add line to current section
                current_section_lines.append(line)

        # Don't forget the last section
        if current_header is not None or current_section_lines:
            section_content = ''.join(current_section_lines)
            sections.append((current_header, section_content, current_header_level))

        return sections

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

        For large files (>6000 chars), splits by headers (# ## ###).
        For small files, stores as single chunk.

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

            # Extract hashtags from frontmatter or content
            hashtags = []
            if "tags" in post.metadata:
                hashtags = post.metadata.get("tags", [])
            elif "hashtags" in post.metadata:
                hashtags = post.metadata.get("hashtags", [])

            # Simple hashtag extraction from content (but not from headers)
            import re
            content_hashtags = re.findall(r'#(\w+)', post.content)
            hashtags.extend(content_hashtags)
            hashtags = list(set(hashtags))  # Remove duplicates

            # Extract document title (first # header)
            doc_title = None
            title_match = re.search(r'^#\s+(.+)$', post.content, re.MULTILINE)
            if title_match:
                doc_title = title_match.group(1).strip()

            # Check file size and decide on chunking strategy
            if len(post.content) <= 6000:
                # Small file: store as single chunk
                metadata = {
                    "format": "markdown",
                    "frontmatter": post.metadata if post.metadata else {},
                    "file_size": len(content),
                    "hashtags": hashtags,
                    **git_metadata
                }

                if doc_title:
                    metadata["title"] = doc_title

                chunks.append(DocumentChunk(
                    repo_id=repo_id,
                    file_path=relative_path,
                    doc_type="markdown",
                    content=post.content,
                    metadata=metadata
                ))

            else:
                # Large file: split by headers
                sections = self.split_markdown_by_headers(post.content)

                logger.info(f"Splitting large markdown file {relative_path} ({len(post.content):,} chars) into {len(sections)} sections")

                for idx, (header_text, section_content, header_level) in enumerate(sections):
                    # Skip empty sections or sections that are just headers (too short)
                    if len(section_content.strip()) < 50:
                        continue

                    metadata = {
                        "format": "markdown",
                        "frontmatter": post.metadata if post.metadata else {},
                        "file_size": len(content),
                        "section_index": idx,
                        "total_sections": len(sections),
                        "hashtags": hashtags,
                        **git_metadata
                    }

                    if doc_title:
                        metadata["document_title"] = doc_title

                    if header_text:
                        metadata["section_title"] = header_text
                        metadata["header_level"] = header_level

                    chunks.append(DocumentChunk(
                        repo_id=repo_id,
                        file_path=relative_path,
                        doc_type="markdown",
                        content=section_content.strip(),
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

    def split_rst_by_sections(self, content: str) -> List[tuple]:
        """
        Split RST content by section headers

        Returns:
            List of (section_title, section_content) tuples
        """
        lines = content.splitlines(keepends=True)
        sections = []
        current_section_title = None
        current_section_lines = []

        i = 0
        while i < len(lines):
            line = lines[i]

            # Check if next line is an underline (section header)
            if i + 1 < len(lines):
                next_line = lines[i + 1]
                underline_chars = set(next_line.strip())

                # Valid RST underline: single char repeated, common chars: = - ` : ' " ~ ^ _ * + # < >
                if (len(underline_chars) == 1 and
                    list(underline_chars)[0] in '=-`:\'"~^_*+#<>' and
                    len(next_line.strip()) >= 3 and
                    line.strip()):

                    # Save previous section if exists
                    if current_section_title is not None or current_section_lines:
                        section_content = ''.join(current_section_lines)
                        sections.append((current_section_title, section_content))

                    # Start new section
                    current_section_title = line.strip()
                    current_section_lines = [line, lines[i + 1]]  # Include title and underline
                    i += 2  # Skip the underline
                    continue

            # Add line to current section
            current_section_lines.append(line)
            i += 1

        # Don't forget the last section
        if current_section_title is not None or current_section_lines:
            section_content = ''.join(current_section_lines)
            sections.append((current_section_title, section_content))

        return sections

    async def parse_rst(
        self,
        file_path: Path,
        content: str,
        repo_id: str,
        relative_path: str,
        git_metadata: Dict
    ) -> List[DocumentChunk]:
        """
        Parse a reStructuredText file
        Used for Sphinx documentation and other RST files

        For large files (>6000 chars), splits by section headers.
        For small files, stores as single chunk.
        """
        chunks = []

        try:
            lines = content.splitlines()

            # Extract main document title (first header)
            doc_title = None
            for i in range(len(lines) - 1):
                if lines[i].strip() and lines[i+1].strip():
                    underline_chars = set(lines[i+1].strip())
                    if len(underline_chars) == 1 and list(underline_chars)[0] in '=-#~^"':
                        doc_title = lines[i].strip()
                        break

            # Check file size and decide on chunking strategy
            if len(content) <= 6000:
                # Small file: store as single chunk
                metadata = {
                    "format": "rst",
                    "line_count": len(lines),
                    "file_size": len(content),
                    **git_metadata
                }

                if doc_title:
                    metadata["title"] = doc_title

                chunks.append(DocumentChunk(
                    repo_id=repo_id,
                    file_path=relative_path,
                    doc_type="rst",
                    content=content,
                    metadata=metadata
                ))

            else:
                # Large file: split by sections
                sections = self.split_rst_by_sections(content)

                logger.info(f"Splitting large RST file {relative_path} ({len(content):,} chars) into {len(sections)} sections")

                for idx, (section_title, section_content) in enumerate(sections):
                    # Skip empty sections
                    if not section_content.strip():
                        continue

                    metadata = {
                        "format": "rst",
                        "file_size": len(content),
                        "section_index": idx,
                        "total_sections": len(sections),
                        **git_metadata
                    }

                    if doc_title:
                        metadata["document_title"] = doc_title

                    if section_title:
                        metadata["section_title"] = section_title

                    chunks.append(DocumentChunk(
                        repo_id=repo_id,
                        file_path=relative_path,
                        doc_type="rst",
                        content=section_content.strip(),
                        metadata=metadata
                    ))

        except Exception as e:
            logger.error(f"Error parsing RST {file_path}: {e}")

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
            elif suffix == ".rst":
                return await self.parse_rst(file_path, content, repo_id, relative_path, git_metadata)
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
                # Skip junk files using comprehensive filter
                if should_skip_file(file_path):
                    logger.debug(f"Skipping junk file: {file_path.name}")
                    continue

                chunks = await self.parse_file(file_path, repo_path, repo_id)
                all_chunks.extend(chunks)

        logger.info(f"Parsed {len(all_chunks)} document chunks from {repo_path}")
        return all_chunks
