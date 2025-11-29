#!/usr/bin/env python3
"""
CodeSmriti Ingestion Pipeline V3.5 - Symbol Name Fix

This is a PATCH release that fixes the symbol name extraction bug in V3.
It preserves existing LLM summaries and embeddings.

The bug: ingest_v3.py looked for `symbol_name` in metadata, but code_parser
returns `function_name`, `class_name`, `method_name` instead.

What this does:
1. Reads existing file_index docs with protect_from_update=true
2. Re-extracts symbols with correct name mapping
3. Updates metadata.symbols with proper names
4. Regenerates symbol_index docs with correct symbol_name
5. Preserves content (LLM summary) and embedding

Usage:
    python ingest_v3_5.py --repo kbhalerao/labcore
    python ingest_v3_5.py --repo kbhalerao/labcore --dry-run
    python ingest_v3_5.py --all  # Process all repos
"""

import asyncio
import argparse
import hashlib
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from loguru import logger

from config import WorkerConfig
from parsers.code_parser import CodeParser
from embeddings.local_generator import LocalEmbeddingGenerator
from storage.couchbase_client import CouchbaseClient
from llm_chunker import is_underchunked
from chunk_versioning import (
    SchemaVersion, EnrichmentLevel,
    create_version_metadata
)

config = WorkerConfig()

SCHEMA_VERSION = "v3.5"


@dataclass
class SymbolRef:
    """Reference to a symbol (function, class, method) - NO CODE"""
    name: str
    symbol_type: str  # function, class, method
    start_line: int
    end_line: int
    docstring: Optional[str] = None
    methods: List[Dict] = field(default_factory=list)


class V35Patcher:
    """
    V3.5 patcher - fixes symbol names while preserving LLM summaries.
    """

    def __init__(
        self,
        db: CouchbaseClient,
        embedding_generator: LocalEmbeddingGenerator,
        dry_run: bool = False
    ):
        self.db = db
        self.bucket = config.couchbase_bucket
        self.embedding_generator = embedding_generator
        self.code_parser = CodeParser()
        self.dry_run = dry_run

        # Stats
        self.stats = {
            "files_processed": 0,
            "files_updated": 0,
            "files_skipped": 0,
            "symbols_fixed": 0,
            "symbol_indexes_created": 0,
            "symbol_indexes_deleted": 0,
            "errors": 0
        }

    def extract_symbol_name(self, chunk_metadata: Dict, chunk_type: str) -> str:
        """
        Extract symbol name from code_parser metadata.

        Fixed mapping:
        - function -> function_name
        - class -> class_name
        - method -> method_name (with class context)
        """
        if chunk_type == "function":
            return chunk_metadata.get("function_name", "unknown")
        elif chunk_type == "class":
            return chunk_metadata.get("class_name", "unknown")
        elif chunk_type == "method":
            method_name = chunk_metadata.get("method_name", "unknown")
            class_name = chunk_metadata.get("class_name", "")
            if class_name and method_name != "unknown":
                return f"{class_name}.{method_name}"
            return method_name
        else:
            # Fallback chain
            return (
                chunk_metadata.get("function_name") or
                chunk_metadata.get("class_name") or
                chunk_metadata.get("method_name") or
                chunk_metadata.get("symbol_name") or
                "unknown"
            )

    async def extract_symbols_fixed(
        self,
        file_path_str: str,
        content: str,
        language: str
    ) -> List[SymbolRef]:
        """
        Extract symbols with FIXED name mapping.

        Args:
            file_path_str: File path as string (for metadata, file may not exist on disk)
            content: File content (from git show, not from disk)
            language: Detected language
        """
        symbols = []

        if language not in ("python", "javascript", "typescript"):
            return symbols

        # Create a dummy Path for the parser (it only uses it for metadata)
        dummy_path = Path(file_path_str)

        try:
            if language == "python":
                chunks = await self.code_parser.parse_python_file(
                    dummy_path, content, "", file_path_str, {}
                )
                for chunk in chunks:
                    name = self.extract_symbol_name(chunk.metadata, chunk.chunk_type)
                    symbols.append(SymbolRef(
                        name=name,
                        symbol_type=chunk.chunk_type,
                        start_line=chunk.metadata.get("start_line", 0),
                        end_line=chunk.metadata.get("end_line", 0),
                        docstring=chunk.metadata.get("docstring"),
                        methods=chunk.metadata.get("methods", [])
                    ))
            elif language in ("javascript", "typescript"):
                chunks = await self.code_parser.parse_javascript_file(
                    dummy_path, content, "", file_path_str, {},
                    is_typescript=(language == "typescript")
                )
                for chunk in chunks:
                    name = self.extract_symbol_name(chunk.metadata, chunk.chunk_type)
                    symbols.append(SymbolRef(
                        name=name,
                        symbol_type=chunk.chunk_type,
                        start_line=chunk.metadata.get("start_line", 0),
                        end_line=chunk.metadata.get("end_line", 0),
                        docstring=chunk.metadata.get("docstring"),
                        methods=chunk.metadata.get("methods", [])
                    ))
        except Exception as e:
            logger.debug(f"Symbol extraction failed for {file_path_str}: {e}")

        return symbols

    def get_file_at_commit(self, repo_id: str, file_path: str, commit_hash: str) -> Optional[str]:
        """
        Read file content at a specific commit using git show.

        This ensures we're reading the EXACT version that was indexed,
        not the current HEAD which may have changed.
        """
        import subprocess
        import os

        # Repos are stored in REPOS_PATH with format: owner_repo (underscore replaces slash)
        repos_path = os.getenv("REPOS_PATH", "/Users/kaustubh/Documents/codesmriti-repos")
        repo_dir_name = repo_id.replace("/", "_")
        repo_dir = Path(repos_path) / repo_dir_name

        if not repo_dir.exists():
            logger.warning(f"Repo not found: {repo_dir}")
            return None

        if not commit_hash:
            # Fallback to current file if no commit hash
            full_path = repo_dir / file_path
            if full_path.exists():
                try:
                    return full_path.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    return None
            return None

        try:
            # Use git show to get file content at specific commit
            result = subprocess.run(
                ["git", "show", f"{commit_hash}:{file_path}"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                return result.stdout
            else:
                # File might not exist at that commit, or commit not found
                # Try short hash
                short_hash = commit_hash[:12]
                result = subprocess.run(
                    ["git", "show", f"{short_hash}:{file_path}"],
                    cwd=repo_dir,
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0:
                    return result.stdout

                logger.debug(f"git show failed for {file_path}@{commit_hash}: {result.stderr}")
                return None

        except subprocess.TimeoutExpired:
            logger.warning(f"git show timeout for {file_path}@{commit_hash}")
            return None
        except Exception as e:
            logger.warning(f"Could not read {file_path}@{commit_hash}: {e}")
            return None

    async def patch_file_index(self, doc: Dict) -> Tuple[bool, int]:
        """
        Patch a single file_index document.

        Returns:
            (updated: bool, symbols_fixed: int)
        """
        chunk_id = doc.get("chunk_id")
        repo_id = doc.get("repo_id")
        file_path = doc.get("file_path")
        commit_hash = doc.get("metadata", {}).get("commit_hash", "")
        language = doc.get("metadata", {}).get("language", "unknown")

        # Read file content at the EXACT commit that was indexed
        content = self.get_file_at_commit(repo_id, file_path, commit_hash)
        if not content:
            logger.warning(f"Could not read file: {repo_id}/{file_path}")
            return False, 0

        # Extract symbols with fixed mapping (pass file_path as string, content from git)
        new_symbols = await self.extract_symbols_fixed(file_path, content, language)

        # Count how many were "unknown" before
        old_symbols = doc.get("metadata", {}).get("symbols", [])
        unknown_before = sum(1 for s in old_symbols if s.get("name") == "unknown")
        unknown_after = sum(1 for s in new_symbols if s.name == "unknown")
        symbols_fixed = unknown_before - unknown_after

        # Check underchunked status with new symbols
        chunk_dicts = [{"symbol_name": s.name} for s in new_symbols]
        is_under, under_reason = is_underchunked(file_path, content, chunk_dicts, language)

        # Build updated metadata
        new_metadata = doc.get("metadata", {}).copy()
        new_metadata["symbols"] = [
            {
                "name": s.name,
                "type": s.symbol_type,
                "lines": [s.start_line, s.end_line],
                "docstring": s.docstring,
                "methods": s.methods
            }
            for s in new_symbols
        ]
        new_metadata["is_underchunked"] = is_under
        new_metadata["underchunk_reason"] = under_reason if is_under else ""
        new_metadata["quality_score"] = 0.8 if not is_under else 0.5

        # Update version metadata
        new_version = doc.get("version", {}).copy()
        new_version["schema_version"] = SCHEMA_VERSION
        new_version["updated_at"] = datetime.now().isoformat()
        # Keep protect_from_update and enrichment_level as-is

        # Build updated doc (preserve content and embedding!)
        updated_doc = {
            **doc,
            "metadata": new_metadata,
            "version": new_version
        }

        if not self.dry_run:
            # Upsert the updated doc
            self.db.collection.upsert(chunk_id, updated_doc)

        return True, max(0, symbols_fixed)

    async def regenerate_symbol_indexes(
        self,
        file_doc: Dict,
        content: str,
        symbols: List[SymbolRef]
    ) -> int:
        """
        Regenerate symbol_index docs for a file.

        Returns number of symbol_index docs created.
        """
        repo_id = file_doc.get("repo_id")
        file_path = file_doc.get("file_path")
        commit_hash = file_doc.get("metadata", {}).get("commit_hash", "")
        file_chunk_id = file_doc.get("chunk_id")

        created = 0
        children_ids = []

        for symbol in symbols:
            # Skip tiny symbols
            if symbol.end_line - symbol.start_line < 5:
                continue

            # Skip if name is still unknown
            if symbol.name == "unknown":
                continue

            symbol_chunk_id = hashlib.sha256(
                f"symbol:{repo_id}:{file_path}:{symbol.name}:{commit_hash}".encode()
            ).hexdigest()

            # Get code snippet for embedding (NOT stored in doc)
            lines = content.split('\n')
            start_idx = max(0, symbol.start_line - 1)
            end_idx = min(len(lines), symbol.end_line)
            code_snippet = '\n'.join(lines[start_idx:end_idx])

            # Create summary (basic - no LLM for now)
            summary = f"{symbol.name} ({symbol.symbol_type} in {file_path}, lines {symbol.start_line}-{symbol.end_line})"
            if symbol.docstring:
                # Clean up docstring
                doc_clean = symbol.docstring.strip().strip('"\'').strip()[:200]
                summary += f"\n\n{doc_clean}"

            # Generate embedding from summary + code preview
            embedding_text = f"{summary}\n\nCode:\n{code_snippet[:2000]}"
            embedding = self.embedding_generator.generate_embedding(embedding_text)

            # Build symbol_index doc
            symbol_doc = {
                "chunk_id": symbol_chunk_id,
                "type": "symbol_index",
                "repo_id": repo_id,
                "file_path": file_path,
                "symbol_name": symbol.name,
                "symbol_type": symbol.symbol_type,
                "content": summary,  # Searchable content
                "embedding": embedding,
                "metadata": {
                    "commit_hash": commit_hash,
                    "start_line": symbol.start_line,
                    "end_line": symbol.end_line,
                    "docstring": symbol.docstring,
                    "methods": symbol.methods,
                    "language": file_doc.get("metadata", {}).get("language", "unknown")
                },
                "parent_id": file_chunk_id,
                "version": {
                    "schema_version": SCHEMA_VERSION,
                    "enrichment_level": "basic",  # No LLM enrichment yet
                    "created_at": datetime.now().isoformat(),
                    "pipeline_version": "2025.11.28"
                }
            }

            if not self.dry_run:
                self.db.collection.upsert(symbol_chunk_id, symbol_doc)

            children_ids.append(symbol_chunk_id)
            created += 1

        # Update parent's children_ids
        if children_ids and not self.dry_run:
            file_doc["children_ids"] = children_ids
            self.db.collection.upsert(file_chunk_id, file_doc)

        return created

    async def delete_old_symbol_indexes(self, repo_id: str) -> int:
        """Delete old symbol_index docs for a repo (they have symbol_name='unknown')."""
        query = f'''
        SELECT RAW d.chunk_id
        FROM `{self.bucket}`._default._default d
        WHERE d.type = "symbol_index"
          AND d.repo_id = "{repo_id}"
          AND (d.symbol_name = "unknown" OR d.version.schema_version != "{SCHEMA_VERSION}")
        '''

        chunk_ids = list(self.db.cluster.query(query))

        if not self.dry_run:
            for chunk_id in chunk_ids:
                try:
                    self.db.collection.remove(chunk_id)
                except Exception:
                    pass

        return len(chunk_ids)

    async def patch_repo(self, repo_id: str):
        """Patch all file_index docs for a repo."""
        logger.info(f"Patching repo: {repo_id}")

        # Delete old symbol_indexes first
        deleted = await self.delete_old_symbol_indexes(repo_id)
        self.stats["symbol_indexes_deleted"] += deleted
        logger.info(f"  Deleted {deleted} old symbol_index docs")

        # Fetch all file_index docs for this repo
        query = f'''
        SELECT RAW d
        FROM `{self.bucket}`._default._default d
        WHERE d.type = "file_index"
          AND d.repo_id = "{repo_id}"
        '''

        file_docs = list(self.db.cluster.query(query))
        logger.info(f"  Found {len(file_docs)} file_index docs")

        for doc in file_docs:
            self.stats["files_processed"] += 1

            try:
                # Patch the file_index
                updated, symbols_fixed = await self.patch_file_index(doc)

                if updated:
                    self.stats["files_updated"] += 1
                    self.stats["symbols_fixed"] += symbols_fixed

                    # Read content at original commit and regenerate symbol_indexes
                    content = self.get_file_at_commit(
                        repo_id,
                        doc.get("file_path"),
                        doc.get("metadata", {}).get("commit_hash", "")
                    )

                    if content:
                        # Get the updated symbols (pass file_path as string)
                        language = doc.get("metadata", {}).get("language", "unknown")
                        file_path = doc.get("file_path")
                        new_symbols = await self.extract_symbols_fixed(file_path, content, language)

                        # Regenerate symbol_index docs
                        created = await self.regenerate_symbol_indexes(doc, content, new_symbols)
                        self.stats["symbol_indexes_created"] += created
                else:
                    self.stats["files_skipped"] += 1

            except Exception as e:
                logger.error(f"  Error processing {doc.get('file_path')}: {e}")
                self.stats["errors"] += 1

        logger.info(f"  Completed: {self.stats['files_updated']} updated, {self.stats['symbols_fixed']} symbols fixed")

    async def patch_all_repos(self):
        """Patch all repos in the database."""
        query = f'''
        SELECT DISTINCT d.repo_id
        FROM `{self.bucket}`._default._default d
        WHERE d.type = "file_index"
        '''

        repos = [row["repo_id"] for row in self.db.cluster.query(query)]
        logger.info(f"Found {len(repos)} repos to patch")

        for repo_id in repos:
            await self.patch_repo(repo_id)

    def print_stats(self):
        """Print final statistics."""
        print("\n" + "=" * 60)
        print("V3.5 PATCH COMPLETE")
        print("=" * 60)
        print(f"  Files processed:        {self.stats['files_processed']:,}")
        print(f"  Files updated:          {self.stats['files_updated']:,}")
        print(f"  Files skipped:          {self.stats['files_skipped']:,}")
        print(f"  Symbols fixed:          {self.stats['symbols_fixed']:,}")
        print(f"  Symbol indexes deleted: {self.stats['symbol_indexes_deleted']:,}")
        print(f"  Symbol indexes created: {self.stats['symbol_indexes_created']:,}")
        print(f"  Errors:                 {self.stats['errors']:,}")
        if self.dry_run:
            print("\n  [DRY RUN - no changes made]")
        print("=" * 60)


async def main():
    parser = argparse.ArgumentParser(description="V3.5 Symbol Name Patch")
    parser.add_argument("--repo", type=str, help="Repo to patch (owner/name)")
    parser.add_argument("--all", action="store_true", help="Patch all repos")
    parser.add_argument("--dry-run", action="store_true", help="Don't write changes")
    args = parser.parse_args()

    if not args.repo and not args.all:
        parser.error("Must specify --repo or --all")

    # Initialize
    db = CouchbaseClient()
    embedding_gen = LocalEmbeddingGenerator()

    patcher = V35Patcher(
        db=db,
        embedding_generator=embedding_gen,
        dry_run=args.dry_run
    )

    if args.all:
        await patcher.patch_all_repos()
    else:
        await patcher.patch_repo(args.repo)

    patcher.print_stats()


if __name__ == "__main__":
    asyncio.run(main())
