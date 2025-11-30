#!/usr/bin/env python3
"""
V4 Supplementary Document Ingestion

Ingests markdown, RST, and text files that were missed in the initial V4 code ingestion.
Does NOT touch existing code files - only adds document records.

Document types: .md, .rst, .txt

Uses the existing DocumentParser which:
- Chunks large markdown files by headers (# ## ###)
- Stores small files as single chunks
- Extracts frontmatter, titles, and hashtags

Usage:
    python v4/ingest_docs.py                    # All repos
    python v4/ingest_docs.py --repo owner/name  # Single repo
    python v4/ingest_docs.py --dry-run          # Preview only
"""
import asyncio
import hashlib
import sys
from pathlib import Path
from datetime import datetime, timezone
from typing import List, Dict, Optional, Tuple
import argparse

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from semantic_text_splitter import MarkdownSplitter, TextSplitter
from config import WorkerConfig
from storage.couchbase_client import CouchbaseClient
from embeddings.local_generator import LocalEmbeddingGenerator
from parsers.code_parser import should_skip_file

config = WorkerConfig()

# Document extensions to process
DOC_EXTENSIONS = [".md", ".rst", ".txt"]

# Chunking config - target 4000 chars, safe for 8192 token embeddings
CHUNK_CAPACITY = 4000


def extract_header_hierarchy(content: str, full_doc: str) -> Dict:
    """Extract header hierarchy from a markdown chunk.

    Returns dict with:
    - section_title: The immediate header for this chunk
    - header_path: Full path like "Main Title > Section One > Subsection"
    - header_level: 1 for #, 2 for ##, etc.
    """
    import re

    # Find the first header in this chunk
    header_match = re.search(r'^(#{1,6})\s+(.+)$', content, re.MULTILINE)

    if not header_match:
        return {"section_title": None, "header_path": None, "header_level": None}

    header_level = len(header_match.group(1))
    section_title = header_match.group(2).strip()

    # Find position of this chunk in full doc to build hierarchy
    chunk_start = full_doc.find(content[:100])  # Find approximate position
    if chunk_start == -1:
        return {"section_title": section_title, "header_path": section_title, "header_level": header_level}

    # Extract all headers before this position
    doc_before = full_doc[:chunk_start]
    all_headers = re.findall(r'^(#{1,6})\s+(.+)$', doc_before, re.MULTILINE)

    # Build hierarchy - keep most recent header at each level
    hierarchy = {}
    for hashes, title in all_headers:
        level = len(hashes)
        hierarchy[level] = title.strip()
        # Clear lower levels when we see a higher-level header
        for l in list(hierarchy.keys()):
            if l > level:
                del hierarchy[l]

    # Add current header
    hierarchy[header_level] = section_title

    # Build path from hierarchy
    path_parts = [hierarchy[l] for l in sorted(hierarchy.keys()) if l in hierarchy]
    header_path = " > ".join(path_parts) if path_parts else section_title

    return {
        "section_title": section_title,
        "header_path": header_path,
        "header_level": header_level
    }


class DocumentIngester:
    """Ingest documentation files into V4 index."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run
        self.cb_client: Optional[CouchbaseClient] = None
        self.embedder: Optional[EmbeddingGenerator] = None
        self.md_splitter = MarkdownSplitter(capacity=CHUNK_CAPACITY)
        self.text_splitter = TextSplitter(capacity=CHUNK_CAPACITY)
        self.stats = {
            "files_found": 0,
            "files_processed": 0,
            "files_skipped": 0,
            "files_failed": 0,
            "chunks_created": 0,
        }

    async def initialize(self):
        """Initialize connections."""
        if not self.dry_run:
            self.cb_client = CouchbaseClient()  # Connects in __init__
            self.embedder = LocalEmbeddingGenerator()  # Uses sentence-transformers locally
        logger.info(f"Document ingester initialized (dry_run={self.dry_run})")

    async def close(self):
        """Close connections."""
        if self.cb_client:
            self.cb_client.close()

    def discover_docs(self, repo_path: Path) -> List[Path]:
        """Find all document files in a repository."""
        docs = []
        for ext in DOC_EXTENSIONS:
            for file_path in repo_path.rglob(f"*{ext}"):
                if should_skip_file(file_path):
                    continue
                # Additional doc-specific filters
                if self._should_skip_doc(file_path):
                    continue
                docs.append(file_path)
        return docs

    def _should_skip_doc(self, file_path: Path) -> bool:
        """Additional filters specific to documentation files."""
        name = file_path.name.lower()
        path_str = str(file_path).lower()

        # Skip common non-useful docs
        skip_names = [
            "changelog.md", "changelog.txt",
            "license.md", "license.txt", "license",
            "authors.md", "authors.txt",
            "contributors.md",
            "code_of_conduct.md",
            "security.md",
        ]
        if name in skip_names:
            return True

        # Skip docs in certain directories
        skip_dirs = [
            "/node_modules/",
            "/.git/",
            "/vendor/",
            "/.venv/",
            "/venv/",
            "/site-packages/",
            "/__pycache__/",
        ]
        if any(d in path_str for d in skip_dirs):
            return True

        return False

    async def process_doc(
        self,
        file_path: Path,
        repo_path: Path,
        repo_id: str,
    ) -> int:
        """Process a single document file using semantic-text-splitter.

        Returns number of chunks created.
        """
        try:
            # Read content
            content = file_path.read_text(encoding="utf-8", errors="ignore")
            rel_path = str(file_path.relative_to(repo_path))

            # Skip empty or tiny files
            if len(content.strip()) < 100:
                logger.debug(f"Skipping {rel_path}: too small ({len(content)} chars)")
                return 0

            ext = file_path.suffix.lower()

            # Determine doc type and choose splitter
            if ext == ".md":
                doc_type = "markdown"
                chunks = self.md_splitter.chunks(content)
            elif ext == ".rst":
                doc_type = "restructuredtext"
                # RST is similar enough to markdown for the splitter
                chunks = self.md_splitter.chunks(content)
            else:
                doc_type = "plaintext"
                chunks = self.text_splitter.chunks(content)

            if not chunks:
                logger.warning(f"Skipping {rel_path}: splitter returned 0 chunks")
                return 0

            # Filter out tiny chunks (just whitespace or minimal content)
            original_count = len(chunks)
            chunks = [c for c in chunks if len(c.strip()) >= 100]

            if not chunks:
                logger.warning(f"Skipping {rel_path}: all {original_count} chunks too small after filtering")
                return 0

            # Generate embeddings and store each chunk
            chunks_created = 0
            for idx, chunk_content in enumerate(chunks):
                # Generate deterministic chunk ID
                chunk_key = f"{repo_id}:{rel_path}:{idx}"
                chunk_id = hashlib.sha256(chunk_key.encode()).hexdigest()[:16]
                doc_id = f"document::{chunk_id}"

                # Extract header hierarchy for markdown/rst
                if doc_type in ("markdown", "restructuredtext"):
                    hierarchy = extract_header_hierarchy(chunk_content, content)
                else:
                    hierarchy = {"section_title": None, "header_path": None, "header_level": None}

                # Create document record
                doc_record = {
                    "type": "document",
                    "repo_id": repo_id,
                    "file_path": rel_path,
                    "doc_type": doc_type,
                    "content": chunk_content,
                    "chunk_index": idx,
                    "total_chunks": len(chunks),
                    "section_title": hierarchy["section_title"],
                    "header_path": hierarchy["header_path"],
                    "header_level": hierarchy["header_level"],
                    "created_at": datetime.now(timezone.utc).isoformat(),
                }

                # Generate embedding (sync method)
                doc_record["embedding"] = self.embedder.generate_embedding(chunk_content)

                # Store in Couchbase (use collection directly for raw dicts)
                self.cb_client.collection.upsert(doc_id, doc_record)
                chunks_created += 1

            return chunks_created

        except Exception as e:
            import traceback
            logger.error(f"Failed to process {file_path}: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            return 0

    async def process_repo(self, repo_path: Path, repo_id: str) -> Dict:
        """Process all documents in a repository."""
        logger.info(f"Processing docs for: {repo_id}")

        # Discover documents
        docs = self.discover_docs(repo_path)
        self.stats["files_found"] += len(docs)
        logger.info(f"Found {len(docs)} document files")

        if self.dry_run:
            for doc in docs[:10]:
                logger.info(f"  Would process: {doc.relative_to(repo_path)}")
            if len(docs) > 10:
                logger.info(f"  ... and {len(docs) - 10} more")
            return {"docs_found": len(docs)}

        # Process each document
        files_processed = 0
        files_failed = 0
        total_chunks = 0

        for doc_path in docs:
            chunks_created = await self.process_doc(doc_path, repo_path, repo_id)
            if chunks_created > 0:
                files_processed += 1
                total_chunks += chunks_created
                self.stats["chunks_created"] += chunks_created
            else:
                files_failed += 1
                self.stats["files_failed"] += 1

            # Progress logging
            if (files_processed + files_failed) % 50 == 0:
                logger.info(f"  Progress: {files_processed + files_failed}/{len(docs)} files, {total_chunks} chunks")

        self.stats["files_processed"] += files_processed
        logger.info(f"Completed {repo_id}: {files_processed} files -> {total_chunks} chunks, {files_failed} failed")

        return {"files_processed": files_processed, "chunks_created": total_chunks, "failed": files_failed}

    async def get_indexed_repos(self) -> List[Tuple[str, str]]:
        """Get list of repos already in the V4 index."""
        if self.dry_run:
            # For dry run, scan the repos directory
            # Directory format: owner_reponame (underscore)
            # Repo ID format: owner/reponame (slash)
            repos_path = Path(config.repos_path)
            repos = []
            for repo_dir in repos_path.iterdir():
                if repo_dir.is_dir() and not repo_dir.name.startswith("."):
                    # Convert owner_repo to owner/repo
                    parts = repo_dir.name.split("_", 1)
                    if len(parts) == 2:
                        repo_id = f"{parts[0]}/{parts[1]}"
                    else:
                        repo_id = repo_dir.name
                    repos.append((repo_id, str(repo_dir)))
            return repos

        # Query Couchbase for distinct repo_ids from V4 index
        query = """
            SELECT DISTINCT repo_id
            FROM code_kosha
            WHERE type IN ['file_index', 'symbol_index', 'repo_summary']
        """
        results = list(self.cb_client.cluster.query(query))

        repos = []
        repos_path = Path(config.repos_path)
        for row in results:
            repo_id = row.get("repo_id")
            if repo_id:
                # Convert owner/repo to owner_repo for disk path
                dir_name = repo_id.replace("/", "_")
                repo_path = repos_path / dir_name
                if repo_path.exists():
                    repos.append((repo_id, str(repo_path)))
                else:
                    logger.warning(f"Repo path not found: {repo_path}")

        return repos

    async def run(self, repo_filter: Optional[str] = None):
        """Run document ingestion."""
        await self.initialize()

        try:
            # Get repos to process
            repos = await self.get_indexed_repos()

            if repo_filter:
                repos = [(r, p) for r, p in repos if repo_filter in r]

            if not repos:
                logger.warning("No repositories found to process")
                return

            logger.info(f"Processing {len(repos)} repositories")
            logger.info("=" * 60)

            for repo_id, repo_path in repos:
                await self.process_repo(Path(repo_path), repo_id)

            # Print summary
            logger.info("=" * 60)
            logger.info("DOCUMENT INGESTION COMPLETE")
            logger.info("=" * 60)
            logger.info(f"Files found:     {self.stats['files_found']}")
            logger.info(f"Files processed: {self.stats['files_processed']}")
            logger.info(f"Files failed:    {self.stats['files_failed']}")
            logger.info(f"Chunks created:  {self.stats['chunks_created']}")

        finally:
            await self.close()


async def main():
    parser = argparse.ArgumentParser(description="V4 Document Ingestion")
    parser.add_argument("--repo", help="Process specific repo (owner/name)")
    parser.add_argument("--dry-run", action="store_true", help="Preview without writing")
    args = parser.parse_args()

    ingester = DocumentIngester(dry_run=args.dry_run)
    await ingester.run(repo_filter=args.repo)


if __name__ == "__main__":
    asyncio.run(main())
