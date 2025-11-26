#!/usr/bin/env python3
"""
CodeFetcher wrapper for ingestion-worker.

Re-exports the shared CodeFetcher from lib/code-fetcher with
ingestion-worker specific defaults (repos_path from config).
"""

import sys
from pathlib import Path

# Add the shared lib to path
lib_path = Path(__file__).parent.parent / "code-fetcher"
if str(lib_path) not in sys.path:
    sys.path.insert(0, str(lib_path))

from fetcher import CodeFetcher as _CodeFetcher, fetch_code_for_chunks as _fetch_code_for_chunks
from typing import Dict, List, Optional

from config import WorkerConfig

config = WorkerConfig()


class CodeFetcher(_CodeFetcher):
    """
    CodeFetcher with ingestion-worker defaults.

    If repos_path is not provided, uses the path from WorkerConfig.
    """

    def __init__(self, repos_path: Optional[str | Path] = None):
        super().__init__(repos_path or config.repos_path)


def fetch_code_for_chunks(chunks: List[Dict], repos_path: Optional[str | Path] = None) -> List[Dict]:
    """
    Convenience function to enrich chunks with code.

    Uses WorkerConfig.repos_path as default.
    """
    return _fetch_code_for_chunks(chunks, repos_path or config.repos_path)


# Re-export for backwards compatibility
__all__ = ["CodeFetcher", "fetch_code_for_chunks"]


if __name__ == "__main__":
    # Test the code fetcher
    fetcher = CodeFetcher()

    print("=== CodeFetcher Test ===\n")
    print(f"Repos path: {fetcher.repos_path}")

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

    print(f"Cache stats: {fetcher.cache_stats()}")
