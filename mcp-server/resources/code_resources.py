"""
Code Resources for MCP
Provides direct access to code files via repo:// URIs
"""

from typing import Optional
from loguru import logger


class CodeResources:
    """
    MCP resources for accessing code files
    """

    def __init__(self):
        """Initialize code resources"""
        logger.info("Initializing code resources")
        # TODO: Initialize Couchbase client
        # self.db = CouchbaseClient()

    async def read_file(self, uri: str) -> str:
        """
        Read a code file from the repo:// URI

        Args:
            uri: Resource URI in format repo://owner/repo/path/to/file

        Returns:
            File content as string

        Raises:
            ValueError: If URI format is invalid
            FileNotFoundError: If file doesn't exist
        """
        try:
            # Parse the URI
            if not uri.startswith("repo://"):
                raise ValueError("URI must start with repo://")

            # Remove the scheme
            path = uri[7:]  # Remove "repo://"

            # Split into components
            parts = path.split("/", 2)
            if len(parts) < 3:
                raise ValueError("URI must be in format repo://owner/repo/path/to/file")

            owner = parts[0]
            repo = parts[1]
            file_path = parts[2]

            repo_id = f"{owner}/{repo}"

            logger.info(f"Reading file: {repo_id}/{file_path}")

            # TODO: Query Couchbase for the file content
            # We should have stored full file contents or reconstruct from chunks
            # For now, return stub data
            content = f"""# File: {file_path}
# Repository: {repo_id}

def example_function():
    \"\"\"
    This is an example function from CodeSmriti.
    In production, this would be the actual file content.
    \"\"\"
    return "Hello from {repo_id}"
"""

            return content

        except Exception as e:
            logger.error(f"Error reading file {uri}: {e}")
            raise

    async def list_files(self, repo_id: str, path: Optional[str] = None) -> list:
        """
        List files in a repository or directory

        Args:
            repo_id: Repository in owner/repo format
            path: Optional path within the repository

        Returns:
            List of file paths
        """
        try:
            logger.info(f"Listing files in {repo_id} (path={path})")

            # TODO: Query Couchbase for files in the repository
            # Filter by path prefix if provided
            # For now, return stub data
            files = [
                "src/main.py",
                "src/utils.py",
                "tests/test_main.py",
                "README.md"
            ]

            if path:
                files = [f for f in files if f.startswith(path)]

            return files

        except Exception as e:
            logger.error(f"Error listing files in {repo_id}: {e}")
            return []
