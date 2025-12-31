"""
CodeSmriti RAG MCP Server (V4)

Provides MCP tools for Claude Code to search and explore codebases.
This is the "direct tool access" mode - Claude does the reasoning.

Tools:
- list_repos: Discover available repositories
- explore_structure: Navigate directory structure
- search_code: Semantic search at any level
- get_file: Retrieve actual source code
"""

import os
from pathlib import Path
from typing import Literal

import httpx
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables - try multiple locations
script_dir = Path(__file__).parent
repo_root = script_dir.parent.parent

# Try script directory first, then repo root
for env_path in [script_dir / ".env", repo_root / ".env"]:
    if env_path.exists():
        load_dotenv(env_path)
        break
else:
    load_dotenv()  # Fallback to default behavior

# Initialize FastMCP server
mcp = FastMCP("code-smriti")

# Configuration
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
        raise ValueError("CODESMRITI_USERNAME and CODESMRITI_PASSWORD must be set")

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            f"{API_BASE_URL}/api/auth/login",
            json={"email": API_USERNAME, "password": API_PASSWORD},
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        _cached_token = data["token"]
        return _cached_token


def clear_token_on_auth_error():
    """Clear cached token on authentication failure."""
    global _cached_token
    _cached_token = None


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
                languages = repo.get("languages", [])
                lang_str = f" ({', '.join(languages[:3])})" if languages else ""
                output.append(f"- **{name}**: {doc_count} documents{lang_str}")

            output.append(f"\n_Total: {data.get('total_repos', 0)} repos, {data.get('total_docs', 0)} documents_")
            return "\n".join(output)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            clear_token_on_auth_error()
            return "Authentication failed. Please check credentials."
        return f"Error listing repositories: {str(e)}"
    except Exception as e:
        return f"Error listing repositories: {str(e)}"


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
        token = await get_auth_token()

        payload = {
            "repo_id": repo_id,
            "path": path,
            "include_summaries": include_summaries
        }
        if pattern:
            payload["pattern"] = pattern

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{API_BASE_URL}/api/rag/structure",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

            output = [f"## {repo_id}/{path or '(root)'}\n"]

            # Key files
            key_files = data.get("key_files", {})
            if key_files:
                output.append("**Key files:**")
                for key_type, key_path in key_files.items():
                    output.append(f"  - {key_type}: `{key_path}`")
                output.append("")

            # Directories
            directories = data.get("directories", [])
            if directories:
                output.append("**Directories:**")
                for d in directories:
                    output.append(f"  - {d}")
                output.append("")

            # Files
            files = data.get("files", [])
            if files:
                output.append("**Files:**")
                for f in files:
                    name = f.get("name", "")
                    lang = f.get("language", "")
                    lines = f.get("line_count", 0)
                    has_summary = "indexed" if f.get("has_summary") else ""
                    lang_str = f" ({lang})" if lang else ""
                    output.append(f"  - `{name}`{lang_str} - {lines} lines {has_summary}")
                output.append("")

            # Summary
            summary = data.get("summary")
            if summary:
                output.append("**Module Summary:**")
                output.append(summary)

            if not directories and not files:
                output.append("_Empty or not found_")

            return "\n".join(output)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            clear_token_on_auth_error()
            return "Authentication failed. Please check credentials."
        return f"Error exploring structure: {str(e)}"
    except Exception as e:
        return f"Error exploring structure: {str(e)}"


@mcp.tool()
async def search_codebase(
    query: str,
    level: Literal["symbol", "file", "module", "repo", "doc"] = "file",
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
    - If results are poor, try adjacent levels or add preview=true first
    """
    try:
        token = await get_auth_token()

        payload = {
            "query": query,
            "level": level,
            "limit": min(limit, 20),
            "preview": preview
        }
        if repo_filter:
            payload["repo_filter"] = repo_filter

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{API_BASE_URL}/api/rag/search",
                headers={"Authorization": f"Bearer {token}"},
                json=payload,
                timeout=60.0
            )
            response.raise_for_status()
            data = response.json()

            results = data.get("results", [])
            if not results:
                return f"No results found for '{query}' at {level} level."

            mode_note = " (preview)" if preview else ""
            output = [f"## Search Results ({level} level{mode_note})\n"]

            for r in results:
                doc_type = r.get("doc_type", "")
                repo_id = r.get("repo_id", "")
                file_path = r.get("file_path", "")
                symbol_name = r.get("symbol_name", "")
                content = r.get("content", "")
                score = r.get("score", 0)
                start_line = r.get("start_line")
                end_line = r.get("end_line")

                # Build header based on doc type
                if doc_type == "symbol_index":
                    symbol_type = r.get("symbol_type", "symbol")
                    header = f"### {symbol_name} ({symbol_type}) in {file_path}"
                    if start_line and end_line:
                        header += f" [lines {start_line}-{end_line}]"
                elif doc_type == "file_index":
                    header = f"### {file_path}"
                elif doc_type == "module_summary":
                    module_path = r.get("module_path", file_path or "")
                    header = f"### Module: {module_path}/"
                elif doc_type == "repo_summary":
                    header = f"### Repository: {repo_id}"
                elif doc_type == "document":
                    doc_subtype = r.get("symbol_type", "doc")  # symbol_type holds doc_type for documents
                    header = f"### Doc: {file_path} ({doc_subtype})"
                else:
                    header = f"### {file_path or repo_id}"

                output.append(header)
                output.append(f"_Repo: {repo_id} | Score: {score:.2f}_\n")

                # In preview mode, content is already truncated; otherwise show first 500 chars
                max_len = 200 if preview else 500
                output.append(content[:max_len] + ("..." if len(content) > max_len else ""))
                output.append("")

            return "\n".join(output)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            clear_token_on_auth_error()
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
            clear_token_on_auth_error()
            return "Authentication failed. Please check credentials."
        return f"Error querying codebase: {str(e)}"
    except Exception as e:
        return f"Error querying codebase: {str(e)}"


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
            language = data.get("language", "")
            truncated = data.get("truncated", False)

            header = f"## {repo_id}/{file_path}\n"
            header += f"Lines {start}-{end} of {total}"
            if truncated:
                header += " (truncated)"
            header += "\n\n"

            return header + f"```{language}\n{code}\n```"

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            clear_token_on_auth_error()
            return "Authentication failed. Please check credentials."
        elif e.response.status_code == 404:
            return f"File not found: {repo_id}/{file_path}"
        return f"Error fetching file: {str(e)}"
    except Exception as e:
        return f"Error fetching file: {str(e)}"


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

    This tool returns business framing, NOT code. For code-level questions,
    use search_codebase instead.

    Args:
        query: Customer question about AgSci capabilities.
               Examples: "Can you build a GIS platform for farm management?",
               "What tools do you have for soil sampling workflows?",
               "How do you handle multi-tenant data isolation?"
    """
    try:
        token = await get_auth_token()

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{API_BASE_URL}/api/rag/agsci",
                headers={"Authorization": f"Bearer {token}"},
                json={"query": query},
                timeout=120.0
            )
            response.raise_for_status()
            data = response.json()

            answer = data.get("answer", "No answer received.")
            sources = data.get("sources", [])

            result = answer
            if sources:
                result += "\n\n**Sources:**\n" + "\n".join(f"- {s}" for s in sources)

            return result

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            clear_token_on_auth_error()
            return "Authentication failed. Please check credentials."
        return f"Error querying AgSci: {str(e)}"
    except Exception as e:
        return f"Error querying AgSci: {str(e)}"


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
        token = await get_auth_token()

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{API_BASE_URL}/api/rag/graph/affected-tests",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "changed_files": changed_files,
                    "cluster_id": cluster_id
                },
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()

            if not data.get("graph_found"):
                return f"No dependency graph found for cluster '{cluster_id}'. Run all tests."

            changed = data.get("changed_modules", [])
            affected = data.get("affected_modules", [])
            tests = data.get("tests_to_run", [])

            output = [f"## Affected Tests for {cluster_id}\n"]

            if changed:
                output.append(f"**Changed modules:** {len(changed)}")
                for m in changed[:10]:
                    output.append(f"  - {m}")
                if len(changed) > 10:
                    output.append(f"  - ... and {len(changed) - 10} more")
                output.append("")

            if affected:
                output.append(f"**Affected modules:** {len(affected)}")
                for m in affected[:10]:
                    output.append(f"  - {m}")
                if len(affected) > 10:
                    output.append(f"  - ... and {len(affected) - 10} more")
                output.append("")

            if tests:
                output.append(f"**Tests to run:** {len(tests)}")
                for t in tests:
                    output.append(f"  - {t}")
            else:
                output.append("**No test modules affected**")

            return "\n".join(output)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            clear_token_on_auth_error()
            return "Authentication failed. Please check credentials."
        return f"Error finding affected tests: {str(e)}"
    except Exception as e:
        return f"Error finding affected tests: {str(e)}"


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
        token = await get_auth_token()

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{API_BASE_URL}/api/rag/graph/criticality",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "module": module,
                    "cluster_id": cluster_id
                },
                timeout=30.0
            )

            if response.status_code == 404:
                return f"Module '{module}' not found in graph '{cluster_id}'"

            response.raise_for_status()
            data = response.json()

            score = data.get("score", 0)
            percentile = data.get("percentile", 0)
            in_deg = data.get("in_degree", 0)
            out_deg = data.get("out_degree", 0)
            repo_id = data.get("repo_id", "")
            is_test = data.get("is_test", False)
            dependents = data.get("direct_dependents", [])

            output = [f"## Criticality: {module}\n"]
            output.append(f"**Repo:** {repo_id}")
            output.append(f"**Score:** {score:.6f} (percentile: {percentile})")
            output.append(f"**In-degree:** {in_deg} (modules depend on this)")
            output.append(f"**Out-degree:** {out_deg} (modules this depends on)")
            if is_test:
                output.append("**Type:** Test module")
            output.append("")

            if dependents:
                output.append(f"**Direct dependents ({len(dependents)}):**")
                for d in dependents[:15]:
                    output.append(f"  - {d}")
                if len(dependents) > 15:
                    output.append(f"  - ... and {len(dependents) - 15} more")
            else:
                output.append("_No direct dependents (leaf module)_")

            return "\n".join(output)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            clear_token_on_auth_error()
            return "Authentication failed. Please check credentials."
        return f"Error getting criticality: {str(e)}"
    except Exception as e:
        return f"Error getting criticality: {str(e)}"


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
        token = await get_auth_token()

        async with httpx.AsyncClient(verify=False) as client:
            response = await client.post(
                f"{API_BASE_URL}/api/rag/graph/info",
                headers={"Authorization": f"Bearer {token}"},
                json={"cluster_id": cluster_id},
                timeout=30.0
            )

            if response.status_code == 404:
                return f"No dependency graph found for cluster '{cluster_id}'"

            response.raise_for_status()
            data = response.json()

            output = [f"## Dependency Graph: {cluster_id}\n"]
            output.append(f"**Nodes:** {data.get('total_nodes', 0)}")
            output.append(f"**Edges:** {data.get('total_edges', 0)}")
            output.append(f"**Cross-repo edges:** {data.get('cross_repo_edges', 0)}")
            output.append(f"**Computed at:** {data.get('computed_at', 'unknown')}")
            output.append("")

            repos = data.get("repos", {})
            if repos:
                output.append("**Repos in cluster:**")
                for repo_id, info in repos.items():
                    role = info.get("role", "unknown")
                    count = info.get("module_count", 0)
                    output.append(f"  - {repo_id}: {count} modules ({role})")

            return "\n".join(output)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 401:
            clear_token_on_auth_error()
            return "Authentication failed. Please check credentials."
        return f"Error getting graph info: {str(e)}"
    except Exception as e:
        return f"Error getting graph info: {str(e)}"


if __name__ == "__main__":
    mcp.run()
