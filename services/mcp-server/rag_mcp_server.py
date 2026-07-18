"""
CodeSmriti RAG MCP Server (V4)

Provides MCP tools for Claude Code to search and explore codebases.
This is the "direct tool access" mode - Claude does the reasoning.

A thin presenter over ``smriti_client.SmritiClient``: HTTP and authentication
live in the shared ``lib/smriti-client`` package (used by the ``smriti`` CLI
too), and the markdown rendering lives in ``smriti_client.format``. Each tool
here makes one client call and returns the shared formatter's output, so there
is exactly one implementation of both the API contract and its rendering.

Tools:
- list_repos: Discover available repositories
- explore_structure: Navigate directory structure
- search_codebase: Semantic search at any level
- get_file: Retrieve actual source code
- ask_codebase / ask_agsci: RAG-synthesized answers
- affected_tests / get_module_criticality / get_graph_info: dependency graph
"""

from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Load environment before constructing the client (it reads CODESMRITI_TOKEN /
# CODESMRITI_API_URL at init). Try the service .env, then the repo-root .env,
# so the token resolves regardless of the launch working directory.
script_dir = Path(__file__).parent
repo_root = script_dir.parent.parent
for env_path in [script_dir / ".env", repo_root / ".env"]:
    if env_path.exists():
        load_dotenv(env_path)
        break
else:
    load_dotenv()

from smriti_client import (  # noqa: E402  (import after dotenv is intentional)
    SmritiAuthError,
    SmritiClient,
    SmritiConfigError,
    SmritiError,
    SmritiNotFoundError,
)
from smriti_client import format as fmt  # noqa: E402

mcp = FastMCP("code-smriti")
client = SmritiClient()  # reads CODESMRITI_TOKEN / CODESMRITI_API_URL

AUTH_ERROR_MSG = (
    "Authentication failed. Set CODESMRITI_TOKEN to a Personal Access Token "
    "minted in the Chief of Staff web UI (API Tokens panel); if it is "
    "already set, the token may have been revoked."
)


# =============================================================================
# MCP Tools
# =============================================================================

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
        return fmt.format_repos(await client.repos())
    except (SmritiAuthError, SmritiConfigError):
        return AUTH_ERROR_MSG
    except SmritiError as e:
        return f"Error listing repositories: {e}"


@mcp.tool()
async def explore_structure(
    repo_id: str,
    path: str = "",
    pattern: str = None,
    include_summaries: bool = False
) -> str:
    """
    Explore repository directory structure.

    Use this tool to navigate and understand project layout before diving
    into search. Similar to how you'd use 'ls' to orient yourself.

    Args:
        repo_id: Repository identifier (e.g., "kbhalerao/labcore")
        path: Path within repo (empty string for root, e.g., "src/", "tests/")
        pattern: Optional glob pattern to filter files (e.g., "*.py", "test_*")
        include_summaries: Include module summary if available

    Returns:
        Directory listing with:
        - Subdirectories
        - Files with language and line count
        - Key files (config, readme, entry points)
        - Module summary if requested
    """
    try:
        data = await client.structure(repo_id, path, pattern, include_summaries)
        return fmt.format_structure(repo_id, path, data)
    except (SmritiAuthError, SmritiConfigError):
        return AUTH_ERROR_MSG
    except SmritiError as e:
        return f"Error exploring structure: {e}"


@mcp.tool()
async def search_codebase(
    query: str,
    level: Literal["symbol", "file", "module", "repo", "doc", "spec"] = "file",
    limit: int = 5,
    repo_filter: str = None,
    preview: bool = False
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

    Args:
        query: The search query (semantic or keyword). Works with natural language
               or specific code patterns like "def authenticate" or "class UserModel".
        level: Search granularity:
               - "symbol": Find specific functions/classes (most specific)
               - "file": Find relevant files (default, good balance)
               - "module": Find relevant folders/areas of code
               - "repo": High-level repository understanding (most broad)
               - "doc": Find documentation files (RST, MD) - use for conceptual questions,
                        design guidelines, audit docs, principles
               - "spec": Find feature specs - use for intent patterns, state contracts,
                         similar experiences, component composition
        limit: Number of results to return (default: 5, max: 20).
        repo_filter: Optional repository name to filter by (e.g. "kbhalerao/labcore").
        preview: If true, return shortened content for quick scanning before fetching full details.

    Returns:
        Search results with summaries and metadata for navigation.

    Query Routing Strategy:
    - For "how does X work" or implementation questions -> start with "file" level
    - For "find function/class X" -> use "symbol" level
    - For "what principles/guidelines" or conceptual docs -> use "doc" level
    - For "what is in X folder" -> use "module" level
    - For "what repos have X" -> use "repo" level
    - For "similar workflows" or "intent patterns" or feature specs -> use "spec" level
    - If results are poor, try adjacent levels or add preview=true first
    """
    try:
        data = await client.search(query, level, limit, repo_filter, preview)
        return fmt.format_search(level, preview, data)
    except (SmritiAuthError, SmritiConfigError):
        return AUTH_ERROR_MSG
    except SmritiError as e:
        return f"Error searching codebase: {e}"


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
        return fmt.format_ask(await client.ask_code(query))
    except (SmritiAuthError, SmritiConfigError):
        return AUTH_ERROR_MSG
    except SmritiError as e:
        return f"Error querying codebase: {e}"


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
        data = await client.get_file(repo_id, file_path, start_line, end_line)
        return fmt.format_file(repo_id, file_path, data)
    except (SmritiAuthError, SmritiConfigError):
        return AUTH_ERROR_MSG
    except SmritiNotFoundError:
        return f"File not found: {repo_id}/{file_path}"
    except SmritiError as e:
        return f"Error fetching file: {e}"


# =============================================================================
# AgSci Customer-Facing Tool
# =============================================================================

@mcp.tool()
async def ask_agsci(query: str) -> str:
    """
    Ask questions about AgSci capabilities and offerings.

    This is the customer-facing tool for understanding what AgSci can build.
    It searches BDR (Business Development) briefs and documentation to provide
    business-focused answers.

    Use this tool when:
    - A prospect asks what AgSci can build for them
    - You need to match customer needs to capabilities
    - The question is about business value, not code implementation
    - You need to draft proposal sections or summarize experience

    This tool returns business framing, NOT code. For code-level questions,
    use search_codebase instead.

    Args:
        query: Customer question about AgSci capabilities.
               Examples: "Can you build a GIS platform for farm management?",
               "What tools do you have for soil sampling workflows?",
               "Draft the technical approach for a field data collection app"
    """
    try:
        return fmt.format_proposal(await client.ask_proposal(query))
    except (SmritiAuthError, SmritiConfigError):
        return AUTH_ERROR_MSG
    except SmritiError as e:
        return f"Error querying AgSci: {e}"


# =============================================================================
# Graph Tools
# =============================================================================

@mcp.tool()
async def affected_tests(
    changed_files: list[str],
    cluster_id: str = "kbhalerao/labcore"
) -> str:
    """
    Find which tests should run given changed files.

    Uses the dependency graph to trace all modules that transitively depend
    on the changed files, then filters to test modules.

    Args:
        changed_files: List of file paths that changed (e.g., ["common/models/__init__.py"])
        cluster_id: Mother repo ID (e.g., "kbhalerao/labcore")

    Returns:
        List of affected modules and tests to run
    """
    try:
        data = await client.affected_tests(changed_files, cluster_id)
        return fmt.format_affected_tests(cluster_id, data)
    except (SmritiAuthError, SmritiConfigError):
        return AUTH_ERROR_MSG
    except SmritiError as e:
        return f"Error finding affected tests: {e}"


@mcp.tool()
async def get_module_criticality(
    module: str,
    cluster_id: str = "kbhalerao/labcore"
) -> str:
    """
    Get criticality info for a module.

    Returns PageRank-based importance score, percentile ranking,
    and direct dependents.

    Args:
        module: Module name (e.g., "common.models", "clients.models")
        cluster_id: Mother repo ID (e.g., "kbhalerao/labcore")

    Returns:
        Criticality info with score, percentile, and dependents
    """
    try:
        data = await client.criticality(module, cluster_id)
        return fmt.format_criticality(module, data)
    except (SmritiAuthError, SmritiConfigError):
        return AUTH_ERROR_MSG
    except SmritiNotFoundError:
        return f"Module '{module}' not found in graph '{cluster_id}'"
    except SmritiError as e:
        return f"Error getting criticality: {e}"


@mcp.tool()
async def get_graph_info(cluster_id: str = "kbhalerao/labcore") -> str:
    """
    Get summary info about a dependency graph.

    Args:
        cluster_id: Mother repo ID (e.g., "kbhalerao/labcore")

    Returns:
        Graph summary with node/edge counts and repo breakdown
    """
    try:
        return fmt.format_graph_info(cluster_id, await client.graph_info(cluster_id))
    except (SmritiAuthError, SmritiConfigError):
        return AUTH_ERROR_MSG
    except SmritiNotFoundError:
        return f"No dependency graph found for cluster '{cluster_id}'"
    except SmritiError as e:
        return f"Error getting graph info: {e}"


if __name__ == "__main__":
    mcp.run()
