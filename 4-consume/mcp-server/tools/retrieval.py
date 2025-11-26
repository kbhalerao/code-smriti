"""
Retrieval Tools for MCP
Handles direct retrieval of code files and metadata using shared CodeFetcher
"""

import os
import sys
import json
from pathlib import Path
from typing import Optional
from loguru import logger

# Add shared lib to path
_lib_path = Path(__file__).parent.parent.parent.parent / "lib" / "code-fetcher"
if str(_lib_path) not in sys.path:
    sys.path.insert(0, str(_lib_path))

from fetcher import CodeFetcher

from config import settings


class RetrievalTools:
    """
    MCP tools for retrieving specific code files and metadata.
    Uses shared CodeFetcher from lib/code-fetcher.
    """

    def __init__(self):
        """Initialize retrieval tools with CodeFetcher."""
        logger.info("Initializing retrieval tools")
        # Get repos path from environment or config
        repos_path = os.getenv("REPOS_PATH", "/repos")
        self.fetcher = CodeFetcher(repos_path)
        logger.info(f"CodeFetcher initialized with repos_path: {self.fetcher.repos_path}")

    async def get_code_context(
        self,
        repo: str,
        file_path: str,
        function_name: Optional[str] = None,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None
    ) -> str:
        """
        Retrieve specific code file with surrounding context.

        Args:
            repo: Repository in owner/repo format
            file_path: Path to the file in the repository
            function_name: Specific function or class name (optional, for future symbol lookup)
            start_line: Start line (1-indexed, optional)
            end_line: End line (1-indexed, optional)

        Returns:
            JSON string with code content and metadata
        """
        try:
            logger.info(f"Retrieving code: {repo}/{file_path} (lines={start_line}-{end_line}, function={function_name})")

            # Fetch content
            if start_line and end_line:
                code = self.fetcher.get_lines(repo, file_path, start_line, end_line, include_line_numbers=True)
            else:
                code = self.fetcher.get_file_content(repo, file_path)

            if code is None:
                return json.dumps({
                    "error": f"File not found: {repo}/{file_path}",
                    "repo_id": repo,
                    "file_path": file_path
                }, indent=2)

            # Get total lines for metadata
            full_content = self.fetcher.get_file_content(repo, file_path)
            total_lines = len(full_content.split('\n')) if full_content else 0

            # Determine actual line range
            actual_start = start_line or 1
            actual_end = end_line or total_lines

            result = {
                "repo_id": repo,
                "file_path": file_path,
                "code": code,
                "start_line": actual_start,
                "end_line": actual_end,
                "total_lines": total_lines,
                "metadata": {
                    "language": self._detect_language(file_path),
                    "fetched_at": self._timestamp()
                }
            }

            if function_name:
                result["function_name"] = function_name

            return json.dumps(result, indent=2)

        except Exception as e:
            logger.error(f"Error in get_code_context: {e}")
            return json.dumps({"error": str(e)})

    async def get_file(
        self,
        repo: str,
        file_path: str
    ) -> str:
        """
        Get entire file content.

        Args:
            repo: Repository in owner/repo format
            file_path: Path to the file in the repository

        Returns:
            JSON string with file content
        """
        return await self.get_code_context(repo, file_path)

    async def get_lines(
        self,
        repo: str,
        file_path: str,
        start_line: int,
        end_line: int
    ) -> str:
        """
        Get specific line range from a file.

        Args:
            repo: Repository in owner/repo format
            file_path: Path to the file in the repository
            start_line: Start line (1-indexed)
            end_line: End line (1-indexed)

        Returns:
            JSON string with code snippet
        """
        return await self.get_code_context(repo, file_path, start_line=start_line, end_line=end_line)

    async def list_repos(self) -> str:
        """
        List all indexed repositories.

        Returns:
            JSON string with repository information
        """
        try:
            logger.info("Listing indexed repositories")

            # List directories in repos_path
            repos_path = self.fetcher.repos_path
            if not repos_path.exists():
                return json.dumps({
                    "error": f"Repos path does not exist: {repos_path}",
                    "total_repos": 0,
                    "repositories": []
                }, indent=2)

            repos = []
            for item in repos_path.iterdir():
                if item.is_dir() and not item.name.startswith('.'):
                    # Convert folder name back to repo_id format
                    # e.g., "owner_repo" -> "owner/repo"
                    parts = item.name.split('_', 1)
                    if len(parts) == 2:
                        repo_id = f"{parts[0]}/{parts[1]}"
                    else:
                        repo_id = item.name

                    # Count files
                    file_count = sum(1 for _ in item.rglob('*') if _.is_file())

                    repos.append({
                        "repo_id": repo_id,
                        "folder": item.name,
                        "file_count": file_count
                    })

            # Sort by file count descending
            repos.sort(key=lambda x: x['file_count'], reverse=True)

            return json.dumps({
                "total_repos": len(repos),
                "repositories": repos
            }, indent=2)

        except Exception as e:
            logger.error(f"Error in list_repos: {e}")
            return json.dumps({"error": str(e)})

    async def get_system_stats(self) -> dict:
        """
        Get system statistics.

        Returns:
            Dictionary with system statistics
        """
        try:
            # Get cache stats from fetcher
            cache_stats = self.fetcher.cache_stats()

            # Count repos and files
            repos_path = self.fetcher.repos_path
            total_repos = 0
            total_files = 0

            if repos_path.exists():
                for item in repos_path.iterdir():
                    if item.is_dir() and not item.name.startswith('.'):
                        total_repos += 1
                        total_files += sum(1 for _ in item.rglob('*') if _.is_file())

            stats = {
                "total_repositories": total_repos,
                "total_files": total_files,
                "repos_path": str(repos_path),
                "cache": cache_stats,
                "status": "healthy"
            }

            return stats

        except Exception as e:
            logger.error(f"Error in get_system_stats: {e}")
            return {"error": str(e), "status": "error"}

    def _detect_language(self, file_path: str) -> str:
        """Detect language from file extension."""
        ext_map = {
            '.py': 'python',
            '.js': 'javascript',
            '.ts': 'typescript',
            '.tsx': 'typescript',
            '.jsx': 'javascript',
            '.svelte': 'svelte',
            '.vue': 'vue',
            '.html': 'html',
            '.css': 'css',
            '.scss': 'scss',
            '.sql': 'sql',
            '.md': 'markdown',
            '.json': 'json',
            '.yaml': 'yaml',
            '.yml': 'yaml',
            '.toml': 'toml',
            '.sh': 'bash',
            '.rs': 'rust',
            '.go': 'go',
            '.java': 'java',
            '.rb': 'ruby',
        }
        ext = Path(file_path).suffix.lower()
        return ext_map.get(ext, 'text')

    def _timestamp(self) -> str:
        """Get current timestamp."""
        from datetime import datetime
        return datetime.utcnow().isoformat() + 'Z'
