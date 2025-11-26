import os
import httpx
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("CodeSmriti RAG")

# Configuration from environment
API_BASE_URL = os.getenv("CODESMRITI_API_URL", "http://macstudio.local")
API_USERNAME = os.getenv("CODESMRITI_USERNAME", "")
API_PASSWORD = os.getenv("CODESMRITI_PASSWORD", "")

# Token cache
_cached_token: str | None = None


async def get_auth_token() -> str:
    """Get JWT token, using cached value if available."""
    global _cached_token

    if _cached_token:
        return _cached_token

    if not API_USERNAME or not API_PASSWORD:
        raise ValueError("CODESMRITI_USERNAME and CODESMRITI_PASSWORD must be set in environment")

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            f"{API_BASE_URL}/api/auth/login",
            json={"username": API_USERNAME, "password": API_PASSWORD},
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        _cached_token = data["access_token"]
        return _cached_token

@mcp.tool()
async def search_codebase(
    query: str,
    limit: int = 5,
    repo_filter: str = None,
    file_pattern: str = None
) -> str:
    """
    Search proprietary codebases for code, documentation, and commit history.

    IMPORTANT: This is your PRIMARY tool for accessing the user's private repositories.
    Use this FIRST when the user asks about their internal code, projects, or repositories
    that are NOT in the current working directory.

    This tool searches across:
    - Source code (all languages)
    - Documentation files (markdown, rst, etc.)
    - Commit messages and git history
    - Configuration files

    Use this tool when you want to:
    - Find code patterns, functions, or classes in proprietary repos
    - Search commit history for changes or context
    - Explore unfamiliar private codebases
    - Get raw code context to analyze yourself
    - Find specific files or implementations

    Prefer this over web search for any internal/proprietary code questions.

    Args:
        query: The search query (semantic or keyword). Works with natural language
               or specific code patterns like "def authenticate" or "class UserModel".
        limit: Number of results to return (default: 5, max: 20).
        repo_filter: Optional repository name to filter by (e.g. "kbhalerao/labcore").
        file_pattern: Optional file path pattern (e.g. "*.py", "src/", "tests/").
    """
    try:
        token = await get_auth_token()

        payload = {"query": query, "limit": min(limit, 20)}
        if repo_filter:
            payload["repo"] = repo_filter
        if file_pattern:
            payload["file_pattern"] = file_pattern

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{API_BASE_URL}/api/rag/search",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()

            # Format results array into readable output
            results = data.get("results", [])
            if not results:
                return "No results found."

            output = []
            for r in results:
                file_path = r.get("file_path", "Unknown")
                repo_id = r.get("repo_id", "")
                language = r.get("language", "")
                content = r.get("content", "")
                score = r.get("score", 0)
                output.append(
                    f"## {file_path} ({repo_id})\n"
                    f"```{language}\n{content}\n```\n"
                    f"Score: {score:.2f}\n"
                )

            return "\n".join(output)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            global _cached_token
            _cached_token = None  # Clear cached token on auth failure
            return "Authentication failed. Please check credentials."
        return f"Error searching codebase: {str(e)}"
    except Exception as e:
        return f"Error searching codebase: {str(e)}"

@mcp.tool()
async def ask_codebase(query: str) -> str:
    """
    Ask questions about proprietary codebases and get AI-generated answers with citations.

    IMPORTANT: This is your go-to tool for understanding the user's private repositories.
    Use this when the user asks questions about their internal projects, architecture,
    patterns, or implementation details that are NOT in the current working directory.

    This tool provides RAG-powered answers that synthesize information from:
    - Source code across all indexed repositories
    - Documentation and README files
    - Commit messages explaining why changes were made
    - Code comments and docstrings

    Use this tool when you want:
    - A high-level explanation of how something works
    - To understand architectural decisions or patterns
    - To learn how different components connect
    - Usage examples and best practices from the codebase
    - Context about why code was written a certain way (via commit history)
    - A direct answer synthesized from multiple code sources

    This is more powerful than search_codebase when you need understanding,
    not just raw code snippets.

    Args:
        query: Natural language question about the codebase. Be specific for best results.
               Examples: "How does authentication work in labcore?",
               "What is the job_counter decorator pattern?",
               "Why was the database schema changed in the last month?"
    """
    try:
        token = await get_auth_token()

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{API_BASE_URL}/api/rag/",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": query, "stream": False},
                timeout=120.0
            )
            response.raise_for_status()
            data = response.json()
            return data.get("answer", "No answer received.")
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            global _cached_token
            _cached_token = None  # Clear cached token on auth failure
            return "Authentication failed. Please check credentials."
        return f"Error querying RAG agent: {str(e)}"
    except Exception as e:
        return f"Error querying RAG agent: {str(e)}"

@mcp.tool()
async def get_file(
    repo_id: str,
    file_path: str,
    start_line: int = None,
    end_line: int = None
) -> str:
    """
    Retrieve actual code from a repository file.

    Use this tool when you need to see the full content of a specific file,
    or a specific line range within a file. This complements search_codebase
    by letting you fetch complete file contents after finding relevant files.

    Args:
        repo_id: Repository identifier (e.g., "kbhalerao/labcore").
        file_path: Path to the file relative to repo root (e.g., "src/main.py").
        start_line: Optional start line (1-indexed). Omit for entire file.
        end_line: Optional end line (1-indexed, inclusive). Omit for entire file.

    Returns:
        The file content with metadata about line numbers.
    """
    try:
        token = await get_auth_token()

        payload = {"repo_id": repo_id, "file_path": file_path}
        if start_line is not None:
            payload["start_line"] = start_line
        if end_line is not None:
            payload["end_line"] = end_line

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{API_BASE_URL}/api/rag/file",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()

            code = data.get("code", "")
            start = data.get("start_line", 1)
            end = data.get("end_line", 0)
            total = data.get("total_lines", 0)
            truncated = data.get("truncated", False)

            header = f"## {repo_id}/{file_path}\n"
            header += f"Lines {start}-{end} of {total}"
            if truncated:
                header += " (truncated)"
            header += "\n\n"

            return header + f"```\n{code}\n```"

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            global _cached_token
            _cached_token = None
            return "Authentication failed. Please check credentials."
        elif e.response.status_code == 404:
            return f"File not found: {repo_id}/{file_path}"
        return f"Error fetching file: {str(e)}"
    except Exception as e:
        return f"Error fetching file: {str(e)}"


@mcp.tool()
async def list_repos() -> str:
    """
    List all indexed repositories available for code search.

    Use this tool to discover what proprietary codebases are available to search.
    This helps you understand the scope of indexed repositories and make better
    targeted queries with repo_filter.

    Returns repositories sorted by document count (descending), showing:
    - Repository name (use this for repo_filter in search_codebase)
    - Number of indexed documents (code files, docs, commits)

    Call this first when:
    - You're unsure what repositories are available
    - The user mentions a project name you don't recognize
    - You want to verify a repo exists before filtering searches
    - You need to understand the codebase coverage
    """
    try:
        token = await get_auth_token()

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.get(
                f"{API_BASE_URL}/api/rag/repos",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

            repos = data.get("repos", [])
            if not repos:
                return "No repositories indexed."

            output = ["## Indexed Repositories\n"]
            for repo in repos:
                name = repo.get("repo_id", "Unknown")
                doc_count = repo.get("doc_count", 0)
                output.append(f"- **{name}**: {doc_count} documents")

            return "\n".join(output)
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            global _cached_token
            _cached_token = None
            return "Authentication failed. Please check credentials."
        return f"Error listing repositories: {str(e)}"
    except Exception as e:
        return f"Error listing repositories: {str(e)}"

if __name__ == "__main__":
    mcp.run()
