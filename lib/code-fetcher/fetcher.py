"""
CodeFetcher - Retrieves actual code from repos using V3 chunk references.

V3 chunks store only summaries + line references. This service fetches
the actual code from the repository at retrieval time.

Benefits:
- No redundant code storage (~85% reduction)
- Always fresh code (reads from repo)
- Handles git operations (checkout specific commits if needed)
"""

from pathlib import Path
from typing import Dict, Optional, List
import logging

logger = logging.getLogger(__name__)


class CodeFetcher:
    """
    Fetches actual code from repos using line references.

    Usage:
        fetcher = CodeFetcher("/path/to/repos")

        # Get full file
        code = fetcher.get_file_content("owner/repo", "src/main.py")

        # Get specific lines
        snippet = fetcher.get_lines("owner/repo", "src/main.py", 10, 50)

        # Get code for a symbol_index chunk
        code = fetcher.get_symbol_code(chunk)

        # Get code with context
        code = fetcher.get_context(chunk, context_lines=10)
    """

    def __init__(self, repos_path: str | Path):
        """
        Initialize CodeFetcher.

        Args:
            repos_path: Base directory containing cloned repositories
        """
        self.repos_path = Path(repos_path).resolve()
        self._file_cache: Dict[str, str] = {}
        self._cache_max_size = 100  # Max files to cache

        logger.debug(f"CodeFetcher initialized with repos_path: {self.repos_path}")

    def _get_repo_path(self, repo_id: str) -> Path:
        """Convert repo_id to filesystem path."""
        # repo_id format: "owner/repo" -> "owner_repo" on disk
        return self.repos_path / repo_id.replace("/", "_")

    def _cache_key(self, repo_id: str, file_path: str) -> str:
        return f"{repo_id}:{file_path}"

    def get_file_content(self, repo_id: str, file_path: str) -> Optional[str]:
        """
        Get full file content.

        Args:
            repo_id: Repository identifier (e.g., "owner/repo")
            file_path: Path relative to repo root (e.g., "src/main.py")

        Returns:
            File content or None if not found
        """
        cache_key = self._cache_key(repo_id, file_path)

        # Check cache
        if cache_key in self._file_cache:
            return self._file_cache[cache_key]

        # Read from disk
        repo_path = self._get_repo_path(repo_id)
        full_path = repo_path / file_path

        if not full_path.exists():
            logger.warning(f"File not found: {full_path}")
            return None

        try:
            content = full_path.read_text(encoding='utf-8', errors='ignore')

            # Cache (with size limit)
            if len(self._file_cache) >= self._cache_max_size:
                # Remove oldest entry
                oldest_key = next(iter(self._file_cache))
                del self._file_cache[oldest_key]

            self._file_cache[cache_key] = content
            return content

        except Exception as e:
            logger.error(f"Failed to read {full_path}: {e}")
            return None

    def get_lines(
        self,
        repo_id: str,
        file_path: str,
        start: int,
        end: int,
        include_line_numbers: bool = False
    ) -> Optional[str]:
        """
        Get specific line range from a file.

        Args:
            repo_id: Repository identifier
            file_path: Path relative to repo root
            start: Start line (1-indexed, inclusive)
            end: End line (1-indexed, inclusive)
            include_line_numbers: Whether to prefix each line with its number

        Returns:
            Code snippet or None if not found
        """
        content = self.get_file_content(repo_id, file_path)
        if content is None:
            return None

        lines = content.split('\n')

        # Clamp to valid range
        start = max(1, start)
        end = min(len(lines), end)

        selected_lines = lines[start-1:end]

        if include_line_numbers:
            # Format: "  10 | code here"
            width = len(str(end))
            return '\n'.join(
                f"{i:>{width}} | {line}"
                for i, line in enumerate(selected_lines, start=start)
            )

        return '\n'.join(selected_lines)

    def get_symbol_code(self, chunk: Dict) -> Optional[str]:
        """
        Get code for a symbol_index chunk.

        Args:
            chunk: V3 symbol_index chunk dict with repo_id, file_path, and metadata

        Returns:
            Symbol code or None
        """
        if chunk.get("type") != "symbol_index":
            logger.warning(f"get_symbol_code called with non-symbol chunk type: {chunk.get('type')}")

        repo_id = chunk.get("repo_id")
        file_path = chunk.get("file_path")
        metadata = chunk.get("metadata", {})
        start_line = metadata.get("start_line", 1)
        end_line = metadata.get("end_line", start_line + 10)

        if not repo_id or not file_path:
            logger.warning("Missing repo_id or file_path in chunk")
            return None

        return self.get_lines(repo_id, file_path, start_line, end_line)

    def get_file_preview(
        self,
        chunk: Dict,
        max_lines: int = 100
    ) -> Optional[str]:
        """
        Get preview of a file_index chunk.

        Args:
            chunk: V3 file_index chunk dict
            max_lines: Maximum lines to return

        Returns:
            File preview or None
        """
        repo_id = chunk.get("repo_id")
        file_path = chunk.get("file_path")

        if not repo_id or not file_path:
            return None

        return self.get_lines(repo_id, file_path, 1, max_lines)

    def get_context(
        self,
        chunk: Dict,
        context_lines: int = 10
    ) -> Optional[str]:
        """
        Get symbol code with surrounding context.

        Args:
            chunk: V3 symbol_index chunk dict
            context_lines: Lines before and after symbol

        Returns:
            Code with context or None
        """
        repo_id = chunk.get("repo_id")
        file_path = chunk.get("file_path")
        metadata = chunk.get("metadata", {})
        start_line = metadata.get("start_line", 1)
        end_line = metadata.get("end_line", start_line + 10)

        if not repo_id or not file_path:
            return None

        expanded_start = max(1, start_line - context_lines)
        expanded_end = end_line + context_lines

        return self.get_lines(repo_id, file_path, expanded_start, expanded_end)

    def enrich_search_results(
        self,
        chunks: List[Dict],
        include_code: bool = True,
        max_code_lines: int = 100
    ) -> List[Dict]:
        """
        Enrich search results with actual code.

        Takes V3 chunks (which have summaries but no code) and adds
        the actual code for display/context.

        Args:
            chunks: List of V3 chunk dicts from search
            include_code: Whether to fetch code
            max_code_lines: Max lines for file previews

        Returns:
            Chunks with "code" field added
        """
        enriched = []

        for chunk in chunks:
            chunk_copy = chunk.copy()

            if include_code:
                chunk_type = chunk.get("type")

                if chunk_type == "symbol_index":
                    chunk_copy["code"] = self.get_symbol_code(chunk)
                elif chunk_type == "file_index":
                    chunk_copy["code"] = self.get_file_preview(chunk, max_code_lines)
                else:
                    # repo_summary, module_summary - no code to fetch
                    chunk_copy["code"] = None

            enriched.append(chunk_copy)

        return enriched

    def clear_cache(self):
        """Clear the file cache."""
        self._file_cache.clear()

    def cache_stats(self) -> Dict:
        """Get cache statistics."""
        return {
            "cached_files": len(self._file_cache),
            "max_size": self._cache_max_size
        }


def fetch_code_for_chunks(chunks: List[Dict], repos_path: str | Path) -> List[Dict]:
    """
    Convenience function to enrich chunks with code.

    Usage in RAG pipeline:
        results = vector_search(query_embedding, top_k=5)
        enriched = fetch_code_for_chunks(results, "/path/to/repos")
    """
    fetcher = CodeFetcher(repos_path)
    return fetcher.enrich_search_results(chunks)
