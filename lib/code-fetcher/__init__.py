"""
CodeFetcher - Shared library for retrieving code from repositories.

V3 chunks store only summaries + line references. This service fetches
the actual code from the repository at retrieval time.

Usage:
    from code_fetcher import CodeFetcher

    fetcher = CodeFetcher("/path/to/repos")
    code = fetcher.get_file_content("owner/repo", "src/main.py")
    snippet = fetcher.get_lines("owner/repo", "src/main.py", 10, 50)
"""

from .fetcher import CodeFetcher, fetch_code_for_chunks

__all__ = ["CodeFetcher", "fetch_code_for_chunks"]
