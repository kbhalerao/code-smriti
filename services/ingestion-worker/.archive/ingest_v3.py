#!/usr/bin/env python3
"""
CodeSmriti Ingestion Pipeline V3 - Normalized Schema

Key principles:
1. NO CODE STORAGE - only summaries + line references
2. Commit pinning - chunks tied to specific commit
3. Atomic file updates - delete all chunks for modified file, replace
4. Embedding = LLM summary + code preview (computed at index time, not stored)
5. is_underchunked flag for files needing LLM enrichment

Chunk types:
- repo_summary: LLM-generated repo overview
- module_summary: LLM-generated module overview
- file_index: LLM summary + symbol refs (NO CODE)
- symbol_index: LLM summary + line refs (NO CODE)

Usage:
    python ingest_v3.py --repo kbhalerao/labcore
    python ingest_v3.py --repo kbhalerao/labcore --dry-run
    python ingest_v3.py --repo kbhalerao/labcore --files-only  # Skip summaries
"""

import asyncio
import argparse
import hashlib
import subprocess
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass, field

from loguru import logger

from config import WorkerConfig
from parsers.code_parser import CodeParser, should_skip_file
from embeddings.local_generator import LocalEmbeddingGenerator
from storage.couchbase_client import CouchbaseClient
from llm_enricher import LLMEnricher, LLMConfig, LMSTUDIO_CONFIG, LLMUnavailableError
from llm_chunker import is_underchunked
from chunk_versioning import (
    SchemaVersion, EnrichmentLevel,
    create_version_metadata, estimate_enrichment_cost
)

config = WorkerConfig()


# =============================================================================
# V3 Chunk Dataclasses
# =============================================================================

@dataclass
class SymbolRef:
    """Reference to a symbol (function, class, method) - NO CODE"""
    name: str
    symbol_type: str  # function, class, method, constant
    start_line: int
    end_line: int
    docstring: Optional[str] = None
    methods: List[Dict] = field(default_factory=list)  # For classes: [{name, lines}]


@dataclass
class FileIndexChunk:
    """V3 file_index - summary + refs, NO CODE"""
    chunk_id: str
    repo_id: str
    file_path: str
    commit_hash: str

    # For search (LLM-generated)
    summary: str  # LLM-generated file summary

    # References (no code)
    line_count: int
    language: str
    symbols: List[SymbolRef]
    imports: List[str]

    # Quality flags
    is_underchunked: bool = False
    underchunk_reason: str = ""
    quality_score: float = 0.5

    # Hierarchy
    parent_id: Optional[str] = None
    children_ids: List[str] = field(default_factory=list)

    # Versioning
    enrichment_level: EnrichmentLevel = EnrichmentLevel.BASIC
    enrichment_cost: int = 0

    # Computed at index time, not stored
    _embedding_text: str = ""
    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict:
        version = create_version_metadata(
            enrichment_level=self.enrichment_level,
            enrichment_cost=self.enrichment_cost,
            protect=(self.enrichment_level in [EnrichmentLevel.LLM_SUMMARY, EnrichmentLevel.LLM_FULL])
        )

        return {
            "chunk_id": self.chunk_id,
            "type": "file_index",
            "repo_id": self.repo_id,
            "file_path": self.file_path,
            "content": self.summary,  # Summary is the searchable content
            "embedding": self.embedding,
            "metadata": {
                "commit_hash": self.commit_hash,
                "line_count": self.line_count,
                "language": self.language,
                "symbols": [
                    {
                        "name": s.name,
                        "type": s.symbol_type,
                        "lines": [s.start_line, s.end_line],
                        "docstring": s.docstring,
                        "methods": s.methods
                    }
                    for s in self.symbols
                ],
                "imports": self.imports,
                "is_underchunked": self.is_underchunked,
                "underchunk_reason": self.underchunk_reason,
                "quality_score": self.quality_score
            },
            "parent_id": self.parent_id,
            "children_ids": self.children_ids,
            "version": version
        }


@dataclass
class SymbolIndexChunk:
    """V3 symbol_index - summary + line refs, NO CODE"""
    chunk_id: str
    repo_id: str
    file_path: str
    commit_hash: str

    # Symbol identity
    symbol_name: str
    symbol_type: str  # function, class, method

    # For search (LLM-generated or docstring)
    summary: str

    # Line references (NO CODE)
    start_line: int
    end_line: int

    # Additional metadata
    methods: List[Dict] = field(default_factory=list)  # For classes
    inherits: List[str] = field(default_factory=list)
    docstring: Optional[str] = None

    # Hierarchy
    parent_id: Optional[str] = None  # Points to file_index

    # Versioning
    enrichment_level: EnrichmentLevel = EnrichmentLevel.BASIC
    enrichment_cost: int = 0

    embedding: Optional[List[float]] = None

    def to_dict(self) -> Dict:
        version = create_version_metadata(
            enrichment_level=self.enrichment_level,
            enrichment_cost=self.enrichment_cost,
            protect=(self.enrichment_level in [EnrichmentLevel.LLM_SUMMARY, EnrichmentLevel.LLM_FULL])
        )

        return {
            "chunk_id": self.chunk_id,
            "type": "symbol_index",
            "repo_id": self.repo_id,
            "file_path": self.file_path,
            "symbol_name": self.symbol_name,
            "symbol_type": self.symbol_type,
            "content": self.summary,  # Summary is the searchable content
            "embedding": self.embedding,
            "metadata": {
                "commit_hash": self.commit_hash,
                "start_line": self.start_line,
                "end_line": self.end_line,
                "methods": self.methods,
                "inherits": self.inherits,
                "docstring": self.docstring
            },
            "parent_id": self.parent_id,
            "version": version
        }


# =============================================================================
# V3 Ingestion Pipeline
# =============================================================================

class IngestionPipelineV3:
    """
    V3 Ingestion Pipeline - Normalized Schema

    Key differences from V2:
    - NO code stored in chunks
    - LLM summaries for searchable content
    - Line references for code retrieval
    - is_underchunked flag for quality tracking
    """

    def __init__(
        self,
        llm_config: LLMConfig = LMSTUDIO_CONFIG,
        enable_llm_summaries: bool = True,
        dry_run: bool = False
    ):
        logger.info("Initializing V3 Ingestion Pipeline (Normalized Schema)")

        # Core components
        self.code_parser = CodeParser()
        self.embedding_generator = LocalEmbeddingGenerator()
        self.db = CouchbaseClient() if not dry_run else None

        # LLM for summaries
        self.enable_llm = enable_llm_summaries
        self.llm_enricher = LLMEnricher(llm_config) if enable_llm_summaries else None
        self._llm_disabled = False  # Circuit breaker state
        self._llm_failures = 0  # Track failures for reporting

        self.dry_run = dry_run

        # Repository path
        self.repos_path = Path(config.repos_path).resolve()

        logger.info(f"V3 Pipeline initialized (LLM={'enabled' if enable_llm_summaries else 'disabled'}, dry_run={dry_run})")

    def get_commit_hash(self, repo_path: Path) -> str:
        """Get current HEAD commit hash"""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=repo_path,
                capture_output=True,
                text=True
            )
            return result.stdout.strip()[:12]  # Short hash
        except Exception:
            return "unknown"

    def get_file_commit(self, repo_path: Path, file_path: str) -> str:
        """Get last commit that modified this file"""
        try:
            result = subprocess.run(
                ["git", "log", "-1", "--format=%H", "--", file_path],
                cwd=repo_path,
                capture_output=True,
                text=True
            )
            return result.stdout.strip()[:12] if result.stdout.strip() else self.get_commit_hash(repo_path)
        except Exception:
            return self.get_commit_hash(repo_path)

    async def generate_file_summary(
        self,
        file_path: str,
        content: str,
        language: str,
        symbols: List[SymbolRef]
    ) -> Tuple[str, int, bool]:
        """
        Generate LLM summary for a file with graceful fallback.

        Returns:
            (summary: str, token_cost: int, llm_succeeded: bool)
        """
        # Fallback summary builder
        def make_fallback_summary():
            symbol_list = ", ".join(s.name for s in symbols[:10])
            return f"File: {file_path} ({language}). Contains: {symbol_list or 'code'}"

        if not self.enable_llm or not self.llm_enricher:
            return make_fallback_summary(), 0, False

        # Check if LLM is disabled due to circuit breaker
        if self._llm_disabled:
            return make_fallback_summary(), 0, False

        try:
            # Truncate content for LLM
            content_preview = content[:8000]

            enrichment = await self.llm_enricher.enrich_file(
                file_path, content_preview, language
            )

            # Build summary from enrichment
            key_symbols_text = ""
            if enrichment.key_symbols:
                if isinstance(enrichment.key_symbols[0], dict):
                    key_symbols_text = chr(10).join(f"- {s.get('name', s)}" for s in enrichment.key_symbols[:10])
                else:
                    key_symbols_text = chr(10).join(f"- {s}" for s in enrichment.key_symbols[:10])

            summary = f"""File: {file_path}

Purpose: {enrichment.purpose or 'Code file'}

{enrichment.summary or ''}

Key Components:
{key_symbols_text if key_symbols_text else '- (see symbols)'}
"""
            cost = estimate_enrichment_cost(len(content_preview), EnrichmentLevel.LLM_SUMMARY)
            return summary.strip(), cost, True

        except LLMUnavailableError:
            # Circuit breaker tripped - disable LLM for rest of run
            logger.warning(f"LLM circuit breaker open - disabling LLM for remaining files")
            self._llm_disabled = True
            return make_fallback_summary(), 0, False

        except Exception as e:
            logger.warning(f"LLM summary failed for {file_path}: {e}")
            self._llm_failures += 1
            return make_fallback_summary(), 0, False

    async def generate_symbol_summary(
        self,
        symbol: SymbolRef,
        file_path: str,
        code_snippet: str
    ) -> str:
        """Generate summary for a symbol (uses docstring if available, else basic)"""
        if symbol.docstring:
            return f"{symbol.name} ({symbol.symbol_type} in {file_path})\n\n{symbol.docstring}"

        # Basic summary without LLM for symbols (cost optimization)
        return f"{symbol.name} ({symbol.symbol_type} in {file_path}, lines {symbol.start_line}-{symbol.end_line})"

    def build_embedding_text(
        self,
        summary: str,
        code_preview: str,
        max_preview_lines: int = 50
    ) -> str:
        """
        Build text for embedding: summary + code preview

        The embedding captures both semantic meaning (summary) and code patterns (preview).
        Code is NOT stored, only used for embedding computation.
        """
        # Limit code preview
        preview_lines = code_preview.split('\n')[:max_preview_lines]
        limited_preview = '\n'.join(preview_lines)

        return f"{summary}\n\nCode Preview:\n{limited_preview}"

    async def extract_symbols(
        self,
        file_path: Path,
        content: str,
        language: str
    ) -> List[SymbolRef]:
        """Extract symbols from file using tree-sitter"""
        symbols = []

        if language not in ("python", "javascript", "typescript"):
            return symbols

        try:
            # Use code parser's tree-sitter parsing
            if language == "python":
                chunks = await self.code_parser.parse_python_file(
                    file_path, content, "", str(file_path), {}
                )
                for chunk in chunks:
                    symbols.append(SymbolRef(
                        name=chunk.metadata.get("symbol_name", "unknown"),
                        symbol_type=chunk.chunk_type,
                        start_line=chunk.metadata.get("start_line", 0),
                        end_line=chunk.metadata.get("end_line", 0),
                        docstring=chunk.metadata.get("docstring"),
                        methods=chunk.metadata.get("methods", [])
                    ))
        except Exception as e:
            logger.debug(f"Symbol extraction failed for {file_path}: {e}")

        return symbols

    def extract_imports(self, content: str, language: str) -> List[str]:
        """Extract import statements"""
        imports = []

        if language == "python":
            import re
            # Match import and from...import
            for match in re.finditer(r'^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))', content, re.MULTILINE):
                imp = match.group(1) or match.group(2)
                if imp:
                    imports.append(imp)

        elif language in ("javascript", "typescript"):
            import re
            for match in re.finditer(r"(?:import|require)\s*\(?['\"]([^'\"]+)['\"]", content):
                imports.append(match.group(1))

        return imports[:20]  # Limit

    async def process_file_v3(
        self,
        file_path: Path,
        repo_path: Path,
        repo_id: str,
        parent_module_id: Optional[str]
    ) -> Tuple[Optional[FileIndexChunk], List[SymbolIndexChunk]]:
        """
        Process a single file with V3 normalized chunking.

        Returns:
            (file_index, [symbol_indices])
        """
        try:
            content = file_path.read_text(encoding='utf-8', errors='ignore')
        except Exception as e:
            logger.warning(f"Could not read {file_path}: {e}")
            return None, []

        if len(content.strip()) < 50:
            return None, []

        relative_path = str(file_path.relative_to(repo_path))
        language = self.code_parser.detect_language(file_path)
        commit_hash = self.get_file_commit(repo_path, relative_path)
        line_count = content.count('\n') + 1

        # Extract symbols (for refs, not code)
        symbols = await self.extract_symbols(file_path, content, language)

        # Extract imports
        imports = self.extract_imports(content, language)

        # Check if underchunked
        chunk_dicts = [{"symbol_name": s.name} for s in symbols]
        underchunked, underchunk_reason = is_underchunked(
            relative_path, content, chunk_dicts, language
        )

        # Generate LLM summary (with graceful fallback)
        summary, summary_cost, llm_succeeded = await self.generate_file_summary(
            relative_path, content, language, symbols
        )

        # Create file_index chunk
        file_chunk_id = hashlib.sha256(
            f"file:{repo_id}:{relative_path}:{commit_hash}".encode()
        ).hexdigest()

        file_chunk = FileIndexChunk(
            chunk_id=file_chunk_id,
            repo_id=repo_id,
            file_path=relative_path,
            commit_hash=commit_hash,
            summary=summary,
            line_count=line_count,
            language=language,
            symbols=symbols,
            imports=imports,
            is_underchunked=underchunked,
            underchunk_reason=underchunk_reason if underchunked else "",
            quality_score=0.8 if not underchunked else 0.5,
            parent_id=parent_module_id,
            enrichment_level=EnrichmentLevel.LLM_SUMMARY if llm_succeeded else EnrichmentLevel.BASIC,
            enrichment_cost=summary_cost
        )

        # Build embedding text (summary + code preview, but DON'T STORE code)
        file_chunk._embedding_text = self.build_embedding_text(
            summary,
            content[:3000],  # First ~50 lines for embedding
            max_preview_lines=50
        )

        # Create symbol_index chunks for significant symbols
        symbol_chunks = []
        for symbol in symbols:
            if symbol.end_line - symbol.start_line < 5:
                continue  # Skip tiny symbols

            symbol_chunk_id = hashlib.sha256(
                f"symbol:{repo_id}:{relative_path}:{symbol.name}:{commit_hash}".encode()
            ).hexdigest()

            # Get code snippet for embedding (NOT stored)
            lines = content.split('\n')
            code_snippet = '\n'.join(lines[symbol.start_line-1:symbol.end_line])

            symbol_summary = await self.generate_symbol_summary(
                symbol, relative_path, code_snippet
            )

            symbol_chunk = SymbolIndexChunk(
                chunk_id=symbol_chunk_id,
                repo_id=repo_id,
                file_path=relative_path,
                commit_hash=commit_hash,
                symbol_name=symbol.name,
                symbol_type=symbol.symbol_type,
                summary=symbol_summary,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                methods=symbol.methods,
                docstring=symbol.docstring,
                parent_id=file_chunk_id,
                enrichment_level=EnrichmentLevel.BASIC
            )

            # Embedding text for symbol (summary + code, code NOT stored)
            symbol_chunk._embedding_text = self.build_embedding_text(
                symbol_summary,
                code_snippet,
                max_preview_lines=100
            )

            symbol_chunks.append(symbol_chunk)
            file_chunk.children_ids.append(symbol_chunk_id)

        return file_chunk, symbol_chunks

    async def generate_embeddings_v3(
        self,
        file_chunks: List[FileIndexChunk],
        symbol_chunks: List[SymbolIndexChunk]
    ):
        """Generate embeddings for all V3 chunks"""
        all_items = []
        all_texts = []

        for fc in file_chunks:
            all_items.append(fc)
            all_texts.append(fc._embedding_text[:8000])

        for sc in symbol_chunks:
            all_items.append(sc)
            all_texts.append(sc._embedding_text[:8000])

        if not all_texts:
            return

        logger.info(f"Generating embeddings for {len(all_texts)} chunks...")

        embeddings = self.embedding_generator.model.encode(
            all_texts,
            batch_size=config.embedding_batch_size
        )

        for item, emb in zip(all_items, embeddings):
            item.embedding = emb.tolist()

    async def delete_file_chunks(self, repo_id: str, file_path: str):
        """Delete all chunks for a file (atomic update)"""
        if self.dry_run or not self.db:
            logger.info(f"[DRY RUN] Would delete chunks for {repo_id}:{file_path}")
            return

        # Query for all chunks with this file_path
        query = f"""
            SELECT META().id
            FROM `{config.couchbase_bucket}`._default._default
            WHERE repo_id = $repo_id
              AND file_path = $file_path
              AND type IN ['file_index', 'symbol_index', 'file', 'symbol_function', 'symbol_class', 'symbol_method']
        """
        try:
            result = self.db.cluster.query(
                query,
                repo_id=repo_id,
                file_path=file_path
            )

            deleted = 0
            for row in result:
                chunk_id = row['id']
                try:
                    self.db.collection.remove(chunk_id)
                    deleted += 1
                except Exception:
                    pass

            if deleted > 0:
                logger.debug(f"Deleted {deleted} existing chunks for {file_path}")

        except Exception as e:
            logger.warning(f"Failed to delete old chunks for {file_path}: {e}")

    async def process_repository(self, repo_id: str, files_only: bool = False):
        """
        Process a repository with V3 normalized pipeline.

        Args:
            repo_id: Repository identifier (owner/repo)
            files_only: Skip repo/module summaries, just process files
        """
        logger.info(f"=== V3 Processing: {repo_id} ===")
        start_time = datetime.now()

        # Get repo path
        repo_path = self.repos_path / repo_id.replace("/", "_")
        if not repo_path.exists():
            logger.error(f"Repository not found: {repo_path}")
            return

        commit_hash = self.get_commit_hash(repo_path)
        logger.info(f"Commit: {commit_hash}")

        all_file_chunks: List[FileIndexChunk] = []
        all_symbol_chunks: List[SymbolIndexChunk] = []

        # Process all code files
        logger.info("Processing files...")
        code_files = []
        for ext in config.supported_code_extensions:
            code_files.extend(repo_path.rglob(f"*{ext}"))

        processed = 0
        skipped = 0
        underchunked_count = 0

        for file_path in code_files:
            if should_skip_file(file_path):
                skipped += 1
                continue

            # Delete existing chunks for this file (atomic update)
            relative_path = str(file_path.relative_to(repo_path))
            await self.delete_file_chunks(repo_id, relative_path)

            # Process file
            file_chunk, symbol_chunks = await self.process_file_v3(
                file_path, repo_path, repo_id, None
            )

            if file_chunk:
                all_file_chunks.append(file_chunk)
                all_symbol_chunks.extend(symbol_chunks)

                if file_chunk.is_underchunked:
                    underchunked_count += 1
                    logger.debug(f"Under-chunked: {relative_path} ({file_chunk.underchunk_reason})")

            processed += 1

            if processed % 20 == 0:
                logger.info(f"Processed {processed} files...")

        logger.info(f"Files: {processed} processed, {skipped} skipped")
        logger.info(f"File chunks: {len(all_file_chunks)}, Symbol chunks: {len(all_symbol_chunks)}")
        logger.info(f"Under-chunked files: {underchunked_count}")

        # Generate embeddings
        await self.generate_embeddings_v3(all_file_chunks, all_symbol_chunks)

        # Store chunks
        if not self.dry_run and self.db:
            logger.info("Storing chunks...")
            success = 0
            failed = 0

            for fc in all_file_chunks:
                try:
                    self.db.collection.upsert(fc.chunk_id, fc.to_dict())
                    success += 1
                except Exception as e:
                    logger.error(f"Failed to store {fc.chunk_id}: {e}")
                    failed += 1

            for sc in all_symbol_chunks:
                try:
                    self.db.collection.upsert(sc.chunk_id, sc.to_dict())
                    success += 1
                except Exception as e:
                    logger.error(f"Failed to store {sc.chunk_id}: {e}")
                    failed += 1

            logger.info(f"Stored: {success}, Failed: {failed}")
        else:
            logger.info(f"[DRY RUN] Would store {len(all_file_chunks) + len(all_symbol_chunks)} chunks")

        elapsed = (datetime.now() - start_time).total_seconds()
        logger.info(f"=== Completed {repo_id} in {elapsed:.1f}s ===")

        # Summary
        total_symbols = sum(len(fc.symbols) for fc in all_file_chunks)
        llm_enriched = sum(1 for fc in all_file_chunks if fc.enrichment_level == EnrichmentLevel.LLM_SUMMARY)

        logger.info(f"""
V3 Ingestion Summary:
---------------------
Repository: {repo_id}
Commit: {commit_hash}
File index chunks: {len(all_file_chunks)}
Symbol index chunks: {len(all_symbol_chunks)}
Total symbols referenced: {total_symbols}
Under-chunked files: {underchunked_count}
LLM-enriched files: {llm_enriched}
LLM failures: {self._llm_failures}
LLM disabled (circuit breaker): {self._llm_disabled}
        """)


async def main():
    parser = argparse.ArgumentParser(description="CodeSmriti V3 Ingestion (Normalized Schema)")
    parser.add_argument("--repo", type=str, required=True, help="Repository ID (owner/repo)")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to database")
    parser.add_argument("--files-only", action="store_true", help="Skip repo/module summaries")
    parser.add_argument("--no-llm", action="store_true", help="Disable LLM summaries")
    parser.add_argument("--llm-host", type=str, default="macstudio.local", help="LLM host")
    parser.add_argument("--llm-port", type=int, default=1234, help="LLM port")
    parser.add_argument("--model", type=str, default="qwen/qwen3-30b-a3b-2507", help="LLM model")
    args = parser.parse_args()

    llm_config = LLMConfig(
        provider="lmstudio",
        model=args.model,
        base_url=f"http://{args.llm_host}:{args.llm_port}",
        temperature=0.3
    )

    pipeline = IngestionPipelineV3(
        llm_config=llm_config,
        enable_llm_summaries=not args.no_llm,
        dry_run=args.dry_run
    )

    await pipeline.process_repository(args.repo, files_only=args.files_only)


if __name__ == "__main__":
    asyncio.run(main())
