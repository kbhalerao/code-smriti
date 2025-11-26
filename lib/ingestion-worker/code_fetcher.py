#!/usr/bin/env python3
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
from functools import lru_cache

from loguru import logger

from config import WorkerConfig

config = WorkerConfig()


class CodeFetcher:
    """
    Fetches actual code from repos using line references.

    Usage:
        fetcher = CodeFetcher()

        # Get code for a symbol_index chunk
        code = fetcher.get_symbol_code(chunk)

        # Get code with context
        code = fetcher.get_context(chunk, context_lines=10)

        # Get full file
        code = fetcher.get_file_content("kbhalerao/labcore", "associates/models.py")
    """

    def __init__(self, repos_path: Optional[Path] = None):
        self.repos_path = repos_path or Path(config.repos_path).resolve()
        self._file_cache: Dict[str, str] = {}
        self._cache_max_size = 100  # Max files to cache

        logger.debug(f"CodeFetcher initialized with repos_path: {self.repos_path}")

    def _get_repo_path(self, repo_id: str) -> Path:
        """Convert repo_id to filesystem path"""
        return self.repos_path / repo_id.replace("/", "_")

    def _cache_key(self, repo_id: str, file_path: str) -> str:
        return f"{repo_id}:{file_path}"

    def get_file_content(self, repo_id: str, file_path: str) -> Optional[str]:
        """
        Get full file content.

        Args:
            repo_id: Repository identifier (e.g., "kbhalerao/labcore")
            file_path: Path relative to repo root (e.g., "associates/models.py")

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
        end: int
    ) -> Optional[str]:
        """
        Get specific line range from a file.

        Args:
            repo_id: Repository identifier
            file_path: Path relative to repo root
            start: Start line (1-indexed, inclusive)
            end: End line (1-indexed, inclusive)

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

        return '\n'.join(lines[start-1:end])

    def get_symbol_code(self, chunk: Dict) -> Optional[str]:
        """
        Get code for a symbol_index chunk.

        Args:
            chunk: V3 symbol_index chunk dict

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
        """Clear the file cache"""
        self._file_cache.clear()

    def cache_stats(self) -> Dict:
        """Get cache statistics"""
        return {
            "cached_files": len(self._file_cache),
            "max_size": self._cache_max_size
        }


# Convenience function for RAG pipeline
def fetch_code_for_chunks(chunks: List[Dict], repos_path: Optional[Path] = None) -> List[Dict]:
    """
    Convenience function to enrich chunks with code.

    Usage in RAG pipeline:
        results = vector_search(query_embedding, top_k=5)
        enriched = fetch_code_for_chunks(results)
    """
    fetcher = CodeFetcher(repos_path)
    return fetcher.enrich_search_results(chunks)


if __name__ == "__main__":
    # Test the code fetcher
    import json

    fetcher = CodeFetcher()

    # Test file read
    print("=== CodeFetcher Test ===\n")

    test_repo = "kbhalerao/claudegram"
    test_file = "src/telegram_io_mcp/server.py"

    content = fetcher.get_file_content(test_repo, test_file)
    if content:
        print(f"✓ Read {test_repo}/{test_file}: {len(content)} chars")
        print(f"  First 3 lines:\n{chr(10).join(content.split(chr(10))[:3])}\n")
    else:
        print(f"✗ Could not read {test_repo}/{test_file}")

    # Test line range
    lines = fetcher.get_lines(test_repo, test_file, 1, 10)
    if lines:
        print(f"✓ Lines 1-10:\n{lines}\n")

    # Test with mock symbol_index chunk
    mock_chunk = {
        "type": "symbol_index",
        "repo_id": test_repo,
        "file_path": test_file,
        "metadata": {
            "start_line": 1,
            "end_line": 20
        }
    }

    code = fetcher.get_symbol_code(mock_chunk)
    if code:
        print(f"✓ Symbol code (lines 1-20): {len(code)} chars")

    print(f"\nCache stats: {fetcher.cache_stats()}")
