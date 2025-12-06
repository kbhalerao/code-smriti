"""
V4 File Processor

Processes a single file into file_index + symbol_index documents.

Flow:
1. Parse with tree-sitter (correct symbol name mapping)
2. Check is_underchunked()
3. If underchunked, invoke LLM chunker for additional semantic chunks
4. Merge tree-sitter symbols + LLM chunks
5. Generate symbol summaries (>5 lines only)
6. Generate file summary from ALL symbol summaries
7. Return documents ready for embedding
"""

import subprocess
from pathlib import Path
from typing import List, Tuple, Optional
from datetime import datetime

from loguru import logger

from .schemas import (
    FileIndex, SymbolIndex, SymbolRef, QualityInfo, VersionInfo,
    EnrichmentLevel, SCHEMA_VERSION, SYMBOL_MIN_LINES,
    make_file_id, make_symbol_id,
)
from .quality import QualityTracker

# Import LLM chunker
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))
from llm_chunker import LLMChunker, is_underchunked, SemanticChunk


class FileProcessor:
    """
    Processes a single file into V4 documents.

    Uses tree-sitter for parsing, with correct symbol name mapping.
    Invokes LLM chunker for underchunked files.
    """

    def __init__(
        self,
        code_parser,
        llm_enricher,
        quality_tracker: QualityTracker,
        enable_llm: bool = True,
        llm_chunker: Optional[LLMChunker] = None,
    ):
        """
        Args:
            code_parser: CodeParser instance for tree-sitter parsing
            llm_enricher: LLMEnricher instance for summary generation
            quality_tracker: QualityTracker for metrics
            enable_llm: Whether to use LLM for summaries
            llm_chunker: LLMChunker instance for semantic chunking (created if not provided)
        """
        self.code_parser = code_parser
        self.llm_enricher = llm_enricher
        self.quality_tracker = quality_tracker
        self.enable_llm = enable_llm

        # Initialize LLM chunker for underchunked files
        if enable_llm:
            self.llm_chunker = llm_chunker or LLMChunker()
        else:
            self.llm_chunker = None

    def get_file_at_commit(
        self,
        repo_path: Path,
        file_path: str,
        commit_hash: str
    ) -> Optional[str]:
        """
        Read file content at a specific commit using git show.

        Ensures we're reading the EXACT version being indexed.
        """
        if not commit_hash:
            # Fallback to current file
            full_path = repo_path / file_path
            if full_path.exists():
                try:
                    return full_path.read_text(encoding='utf-8', errors='ignore')
                except Exception:
                    return None
            return None

        try:
            result = subprocess.run(
                ["git", "show", f"{commit_hash}:{file_path}"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )

            if result.returncode == 0:
                return result.stdout

            # Try short hash
            short_hash = commit_hash[:12]
            result = subprocess.run(
                ["git", "show", f"{short_hash}:{file_path}"],
                cwd=repo_path,
                capture_output=True,
                text=True,
                timeout=10
            )
            if result.returncode == 0:
                return result.stdout

            return None

        except subprocess.TimeoutExpired:
            logger.warning(f"git show timeout for {file_path}@{commit_hash}")
            return None
        except Exception as e:
            logger.warning(f"Could not read {file_path}@{commit_hash}: {e}")
            return None

    def extract_symbol_name(self, chunk_metadata: dict, chunk_type: str) -> str:
        """
        Extract symbol name from code_parser metadata.

        FIXED mapping from V3:
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

    async def extract_symbols(
        self,
        file_path: str,
        content: str,
        language: str
    ) -> List[SymbolRef]:
        """
        Extract symbols from file using tree-sitter.

        Uses CORRECT name mapping (fixed from V3).
        """
        symbols = []

        if language not in ("python", "javascript", "typescript", "svelte"):
            return symbols

        # Create dummy Path for parser (it uses for metadata only)
        dummy_path = Path(file_path)

        try:
            if language == "python":
                chunks = await self.code_parser.parse_python_file(
                    dummy_path, content, "", file_path, {}
                )
            elif language in ("javascript", "typescript"):
                chunks = await self.code_parser.parse_javascript_file(
                    dummy_path, content, "", file_path, {},
                    is_typescript=(language == "typescript")
                )
            elif language == "svelte":
                chunks = await self.code_parser.parse_svelte_file(
                    dummy_path, content, "", file_path, {}
                )
            else:
                return symbols

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
            logger.debug(f"Symbol extraction failed for {file_path}: {e}")

        return symbols

    def extract_imports(self, content: str, language: str) -> List[str]:
        """Extract import statements from file."""
        imports = []
        import re

        if language == "python":
            for match in re.finditer(
                r'^(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))',
                content, re.MULTILINE
            ):
                imp = match.group(1) or match.group(2)
                if imp:
                    imports.append(imp)

        elif language in ("javascript", "typescript", "svelte"):
            for match in re.finditer(
                r"(?:import|require)\s*\(?['\"]([^'\"]+)['\"]",
                content
            ):
                imports.append(match.group(1))

        return imports[:30]  # Limit

    def semantic_chunk_to_symbol_ref(self, chunk: SemanticChunk) -> SymbolRef:
        """
        Convert an LLM-identified SemanticChunk to a SymbolRef.

        This allows LLM chunks to be processed uniformly with tree-sitter symbols.
        """
        return SymbolRef(
            name=chunk.name,
            symbol_type=chunk.chunk_type,  # e.g., "embedded_sql", "business_logic"
            start_line=chunk.start_line,
            end_line=chunk.end_line,
            docstring=chunk.purpose,  # Use purpose as docstring
            methods=[],  # LLM chunks don't have methods
        )

    async def get_llm_chunks(
        self,
        file_path: str,
        content: str,
        language: str,
        existing_symbols: List[SymbolRef],
    ) -> List[SemanticChunk]:
        """
        Invoke LLM chunker to find semantic chunks missed by tree-sitter.

        Args:
            file_path: Path to the file
            content: File content
            language: Programming language
            existing_symbols: Already extracted symbols from tree-sitter

        Returns:
            List of SemanticChunk objects identified by LLM
        """
        if not self.llm_chunker:
            return []

        try:
            # Convert existing symbols to the format LLMChunker expects
            existing_chunks = [
                {"symbol_name": s.name, "type": s.symbol_type}
                for s in existing_symbols
            ]

            # Analyze file with LLM
            semantic_chunks = await self.llm_chunker.analyze_file(
                file_path=file_path,
                content=content,
                language=language,
                existing_chunks=existing_chunks,
            )

            logger.info(
                f"LLM chunker found {len(semantic_chunks)} additional chunks in {file_path}"
            )

            return semantic_chunks

        except Exception as e:
            logger.warning(f"LLM chunker failed for {file_path}: {e}")
            self.quality_tracker.record_llm_call(success=False)
            return []

    def get_code_snippet(
        self,
        content: str,
        start_line: int,
        end_line: int
    ) -> str:
        """Extract code snippet for a symbol."""
        lines = content.split('\n')
        start_idx = max(0, start_line - 1)
        end_idx = min(len(lines), end_line)
        return '\n'.join(lines[start_idx:end_idx])

    async def generate_symbol_summary(
        self,
        symbol: SymbolRef,
        code_snippet: str,
        file_path: str,
        language: str
    ) -> Tuple[str, EnrichmentLevel]:
        """
        Generate summary for a symbol.

        Uses LLM if available, falls back to docstring + structure.
        """
        if not self.enable_llm or not self.quality_tracker.llm_available:
            # Fallback: use docstring + structure
            summary = self._fallback_symbol_summary(symbol, file_path)
            return summary, EnrichmentLevel.BASIC

        try:
            # Call LLM for symbol summary
            result = await self.llm_enricher.enrich_symbol(
                symbol_name=symbol.name,
                symbol_type=symbol.symbol_type,
                code=code_snippet[:4000],  # Limit for LLM context
                file_path=file_path,
                language=language
            )

            self.quality_tracker.record_llm_call(
                success=True,
                tokens=result.get("tokens", 0)
            )

            return result.get("summary", ""), EnrichmentLevel.LLM_SUMMARY

        except Exception as e:
            logger.debug(f"LLM symbol summary failed for {symbol.name}: {e}")
            self.quality_tracker.record_llm_call(success=False)

            summary = self._fallback_symbol_summary(symbol, file_path)
            return summary, EnrichmentLevel.BASIC

    def _fallback_symbol_summary(self, symbol: SymbolRef, file_path: str) -> str:
        """Generate fallback summary from docstring + structure."""
        parts = [
            f"{symbol.name} ({symbol.symbol_type} in {file_path}, "
            f"lines {symbol.start_line}-{symbol.end_line})"
        ]

        if symbol.docstring:
            # Clean and truncate docstring
            doc = symbol.docstring.strip().strip('"""').strip("'''").strip()
            if doc:
                parts.append(f"\n\n{doc[:300]}")

        if symbol.methods:
            method_names = [m.get("name", "?") for m in symbol.methods[:5]]
            parts.append(f"\n\nMethods: {', '.join(method_names)}")

        return ''.join(parts)

    async def generate_file_summary(
        self,
        file_path: str,
        content: str,
        language: str,
        symbols: List[SymbolRef],
        symbol_summaries: List[str]
    ) -> Tuple[str, EnrichmentLevel]:
        """
        Generate summary for a file.

        Uses LLM if available, combining symbol summaries with file preview.
        """
        if not self.enable_llm or not self.quality_tracker.llm_available:
            summary = self._fallback_file_summary(file_path, symbols, language)
            return summary, EnrichmentLevel.BASIC

        try:
            # Build context from symbol summaries
            symbols_context = "\n\n".join(symbol_summaries[:10])

            result = await self.llm_enricher.enrich_file(
                file_path=file_path,
                content=content[:6000],  # File preview
                language=language,
                symbols_context=symbols_context
            )

            self.quality_tracker.record_llm_call(
                success=True,
                tokens=result.get("tokens", 0)
            )

            return result.get("summary", ""), EnrichmentLevel.LLM_SUMMARY

        except Exception as e:
            logger.debug(f"LLM file summary failed for {file_path}: {e}")
            self.quality_tracker.record_llm_call(success=False)

            summary = self._fallback_file_summary(file_path, symbols, language)
            return summary, EnrichmentLevel.BASIC

    def _fallback_file_summary(
        self,
        file_path: str,
        symbols: List[SymbolRef],
        language: str
    ) -> str:
        """Generate fallback file summary from structure."""
        parts = [f"File: {file_path} ({language})"]

        if symbols:
            # Group by type
            classes = [s for s in symbols if s.symbol_type == "class"]
            functions = [s for s in symbols if s.symbol_type == "function"]
            methods = [s for s in symbols if s.symbol_type == "method"]

            if classes:
                class_names = [s.name for s in classes[:5]]
                parts.append(f"\nClasses: {', '.join(class_names)}")

            if functions:
                func_names = [s.name for s in functions[:5]]
                parts.append(f"\nFunctions: {', '.join(func_names)}")

            if methods and not classes:
                method_names = [s.name for s in methods[:5]]
                parts.append(f"\nMethods: {', '.join(method_names)}")

        return ''.join(parts)

    async def process(
        self,
        file_path: Path,
        repo_path: Path,
        repo_id: str,
        commit_hash: str,
        parent_module_id: str
    ) -> Tuple[Optional[FileIndex], List[SymbolIndex]]:
        """
        Process a single file into V4 documents.

        Args:
            file_path: Absolute path to file
            repo_path: Path to repo root
            repo_id: Repository identifier (owner/name)
            commit_hash: Git commit hash
            parent_module_id: document_id of parent module

        Returns:
            (file_index, [symbol_indices])
        """
        relative_path = str(file_path.relative_to(repo_path))

        # Read content at specific commit
        content = self.get_file_at_commit(repo_path, relative_path, commit_hash)
        if not content:
            logger.warning(f"[SKIP] {relative_path}: could not read file content")
            self.quality_tracker.record_file_skipped()
            return None, []
        if len(content.strip()) < 50:
            logger.debug(f"[SKIP] {relative_path}: file too small ({len(content.strip())} chars)")
            self.quality_tracker.record_file_skipped()
            return None, []

        # Detect language
        language = self.code_parser.detect_language(file_path)
        line_count = content.count('\n') + 1

        # Extract symbols with CORRECT name mapping
        symbols = await self.extract_symbols(relative_path, content, language)

        # Extract imports
        imports = self.extract_imports(content, language)

        # Check if underchunked
        chunk_dicts = [{"symbol_name": s.name} for s in symbols]
        is_under, under_reason = is_underchunked(
            relative_path, content, chunk_dicts, language
        )

        # If underchunked, invoke LLM chunker for additional semantic chunks
        llm_chunks = []
        if is_under and self.enable_llm and self.llm_chunker:
            logger.info(
                f"[LLM-CHUNK] {relative_path}: underchunked ({under_reason}), "
                f"tree-sitter found {len(symbols)} symbols, invoking LLM chunker..."
            )
            llm_chunks = await self.get_llm_chunks(
                file_path=relative_path,
                content=content,
                language=language or "unknown",
                existing_symbols=symbols,
            )

            # Convert LLM chunks to SymbolRefs and add to symbols list
            for chunk in llm_chunks:
                symbol_ref = self.semantic_chunk_to_symbol_ref(chunk)
                symbols.append(symbol_ref)
                logger.debug(
                    f"[LLM-CHUNK] {relative_path}: added '{chunk.name}' ({chunk.chunk_type})"
                )

            if llm_chunks:
                chunk_types = [c.chunk_type for c in llm_chunks]
                logger.info(
                    f"[LLM-CHUNK] {relative_path}: added {len(llm_chunks)} semantic chunks: {chunk_types}"
                )
            else:
                logger.debug(f"[LLM-CHUNK] {relative_path}: LLM found no additional chunks")

        # Generate symbol summaries and create symbol_index docs
        symbol_docs = []
        symbol_summaries = []

        for symbol in symbols:
            if not symbol.is_significant:
                continue  # Skip symbols < 5 lines

            # Get code snippet for this symbol
            code_snippet = self.get_code_snippet(
                content, symbol.start_line, symbol.end_line
            )

            # Generate summary
            summary, enrichment_level = await self.generate_symbol_summary(
                symbol, code_snippet, relative_path, language
            )
            symbol_summaries.append(summary)

            # Create symbol_index document
            symbol_doc_id = make_symbol_id(
                repo_id, relative_path, symbol.name, commit_hash
            )
            file_doc_id = make_file_id(repo_id, relative_path, commit_hash)

            symbol_doc = SymbolIndex(
                document_id=symbol_doc_id,
                repo_id=repo_id,
                file_path=relative_path,
                commit_hash=commit_hash,
                symbol_name=symbol.name,
                symbol_type=symbol.symbol_type,
                language=language or "",
                content=summary,
                start_line=symbol.start_line,
                end_line=symbol.end_line,
                docstring=symbol.docstring,
                methods=symbol.methods,
                parent_id=file_doc_id,
                quality=QualityInfo(
                    enrichment_level=enrichment_level,
                    llm_available=self.quality_tracker.llm_available,
                    summary_source="llm_from_docstring_and_code" if enrichment_level == EnrichmentLevel.LLM_SUMMARY else "docstring",
                ),
                version=VersionInfo(
                    schema_version=SCHEMA_VERSION,
                    pipeline_version=datetime.now().strftime("%Y.%m.%d"),
                    created_at=datetime.now().isoformat(),
                ),
            )
            # Store code snippet for embedding generation (not persisted)
            symbol_doc._code_for_embedding = code_snippet[:2000]

            symbol_docs.append(symbol_doc)
            self.quality_tracker.record_symbol_processed()

        # Generate file summary from symbol summaries
        file_summary, file_enrichment = await self.generate_file_summary(
            relative_path, content, language, symbols, symbol_summaries
        )

        # Create file_index document
        file_doc_id = make_file_id(repo_id, relative_path, commit_hash)

        file_doc = FileIndex(
            document_id=file_doc_id,
            repo_id=repo_id,
            file_path=relative_path,
            commit_hash=commit_hash,
            content=file_summary,
            line_count=line_count,
            language=language,
            imports=imports,
            symbols=symbols,  # ALL symbols listed
            parent_id=parent_module_id,
            children_ids=[s.document_id for s in symbol_docs],  # Only significant
            quality=QualityInfo(
                enrichment_level=file_enrichment,
                llm_available=self.quality_tracker.llm_available,
                summary_source="llm_from_symbols" if file_enrichment == EnrichmentLevel.LLM_SUMMARY else "fallback",
                is_underchunked=is_under,
                underchunk_reason=under_reason if is_under else "",
                llm_chunks_added=len(llm_chunks),
            ),
            version=VersionInfo(
                schema_version=SCHEMA_VERSION,
                pipeline_version=datetime.now().strftime("%Y.%m.%d"),
                created_at=datetime.now().isoformat(),
            ),
        )
        # Store embedding text (summary + code preview, not persisted)
        file_doc._embedding_text = f"{file_summary}\n\nCode Preview:\n{content[:3000]}"

        self.quality_tracker.record_file_processed()

        # Log file processing summary
        ts_symbols = len(symbols) - len(llm_chunks)
        logger.info(
            f"[FILE] {relative_path}: {line_count} lines, {language or 'unknown'}, "
            f"{ts_symbols} tree-sitter + {len(llm_chunks)} LLM chunks, "
            f"{len(symbol_docs)} symbol docs, "
            f"enrichment={file_enrichment.value}"
        )

        return file_doc, symbol_docs
