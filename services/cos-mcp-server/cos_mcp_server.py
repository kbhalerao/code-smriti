"""
Chief of Staff MCP Server

Personal productivity tools for capturing tasks, ideas, notes, and context.
Integrates with CodeSmriti's Chief of Staff API.
"""

import os
from typing import Optional
import httpx
from mcp.server.fastmcp import FastMCP
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Initialize FastMCP server
mcp = FastMCP("Chief of Staff")

# Configuration
API_BASE_URL = os.getenv("CODESMRITI_API_URL", "http://localhost")
COS_EMAIL = os.getenv("COS_EMAIL", "")
COS_PASSWORD = os.getenv("COS_PASSWORD", "")

# Token cache
_cached_token: str | None = None


async def get_auth_token() -> str:
    """Get JWT token, using cached value if available."""
    global _cached_token

    if _cached_token:
        return _cached_token

    if not COS_EMAIL or not COS_PASSWORD:
        raise ValueError("COS_EMAIL and COS_PASSWORD must be set in environment")

    async with httpx.AsyncClient(verify=False) as client:
        response = await client.post(
            f"{API_BASE_URL}/api/auth/login",
            json={"email": COS_EMAIL, "password": COS_PASSWORD},
            timeout=30.0
        )
        response.raise_for_status()
        data = response.json()
        _cached_token = data["token"]
        return _cached_token


async def cos_request(method: str, endpoint: str, json_data: dict = None) -> dict:
    """Make authenticated request to CoS API."""
    global _cached_token

    token = await get_auth_token()

    async with httpx.AsyncClient(verify=False) as client:
        kwargs = {
            "headers": {"Authorization": f"Bearer {token}"},
            "timeout": 30.0
        }
        if json_data:
            kwargs["json"] = json_data

        if method == "GET":
            response = await client.get(f"{API_BASE_URL}{endpoint}", **kwargs)
        elif method == "POST":
            response = await client.post(f"{API_BASE_URL}{endpoint}", **kwargs)
        elif method == "PATCH":
            response = await client.patch(f"{API_BASE_URL}{endpoint}", **kwargs)
        elif method == "DELETE":
            response = await client.delete(f"{API_BASE_URL}{endpoint}", **kwargs)
        else:
            raise ValueError(f"Unknown method: {method}")

        # Handle auth failure
        if response.status_code == 401:
            _cached_token = None
            raise httpx.HTTPStatusError("Authentication failed", request=response.request, response=response)

        response.raise_for_status()

        if response.status_code == 204:
            return {}
        return response.json()


# =============================================================================
# Capture Tools - Quick capture of tasks, ideas, notes
# =============================================================================

@mcp.tool()
async def cos_capture(
    content: str,
    doc_type: str = "idea",
    priority: str = "medium",
    tags: list[str] = None,
    project: str = None,
) -> str:
    """
    Quickly capture a task, idea, or note to your Chief of Staff inbox.

    Use this as your PRIMARY tool for capturing anything you want to remember,
    do later, or think about. This is your second brain for the current project.

    The captured item goes to your inbox for later processing/prioritization.

    Args:
        content: What you want to capture. Can be a task, idea, note, or any thought.
        doc_type: Type of capture - "task", "idea", "note", or "message". Default: "idea"
        priority: Priority level - "high", "medium", or "low". Default: "medium"
        tags: Optional list of tags for organization (e.g., ["auth", "refactor"])
        project: Optional project name to associate with (e.g., "code-smriti")

    Examples:
        - "Remember to add rate limiting to the API" -> task
        - "What if we used Redis for caching?" -> idea
        - "JWT tokens expire after 24 hours" -> note
    """
    try:
        payload = {
            "doc_type": doc_type,
            "content": content,
            "priority": priority,
            "status": "inbox",
        }
        if tags:
            payload["tags"] = tags
        if project:
            payload["source"] = {"project": project}

        result = await cos_request("POST", "/api/cos/docs", payload)

        doc_id = result.get("id", "unknown")[:8]
        return f"✓ Captured {doc_type}: \"{content[:50]}{'...' if len(content) > 50 else ''}\" (id: {doc_id})"

    except Exception as e:
        return f"Error capturing: {str(e)}"


@mcp.tool()
async def cos_task(
    content: str,
    priority: str = "medium",
    due_date: str = None,
    tags: list[str] = None,
    project: str = None,
) -> str:
    """
    Create a task - something actionable that needs to be done.

    Use this when you identify something that needs action. Tasks are
    actionable items with clear outcomes.

    Args:
        content: What needs to be done. Be specific and actionable.
        priority: "high", "medium", or "low". Default: "medium"
        due_date: Optional due date in ISO format (e.g., "2025-01-15")
        tags: Optional tags for organization
        project: Optional project name

    Examples:
        - "Add input validation to the login endpoint"
        - "Write tests for the auth module"
        - "Review PR #123"
    """
    try:
        payload = {
            "doc_type": "task",
            "content": content,
            "priority": priority,
            "status": "todo",
        }
        if due_date:
            payload["due_date"] = due_date
        if tags:
            payload["tags"] = tags
        if project:
            payload["source"] = {"project": project}

        result = await cos_request("POST", "/api/cos/docs", payload)

        doc_id = result.get("id", "unknown")[:8]
        priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "")
        return f"✓ Task created {priority_emoji}: \"{content[:60]}{'...' if len(content) > 60 else ''}\" (id: {doc_id})"

    except Exception as e:
        return f"Error creating task: {str(e)}"


# =============================================================================
# Query Tools - Retrieve and search captured items
# =============================================================================

@mcp.tool()
async def cos_next(limit: int = 5) -> str:
    """
    Get your next actions - what should you work on now?

    Returns a prioritized list of tasks: high priority first, then by due date.
    Use this when you need to decide what to work on next.

    Args:
        limit: Number of items to return (default: 5)
    """
    try:
        result = await cos_request("GET", f"/api/cos/docs/next?limit={limit}")

        items = result.get("items", [])
        if not items:
            return "No pending tasks. Your inbox is clear! 🎉"

        output = ["## Next Actions\n"]
        for i, item in enumerate(items, 1):
            priority = item.get("priority", "medium")
            emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "")
            content = item.get("content", "")[:80]
            doc_id = item.get("id", "")[:8]
            tags = item.get("tags", [])
            tag_str = f" [{', '.join(tags)}]" if tags else ""
            output.append(f"{i}. {emoji} {content}{tag_str} `{doc_id}`")

        return "\n".join(output)

    except Exception as e:
        return f"Error getting next actions: {str(e)}"


@mcp.tool()
async def cos_inbox(limit: int = 10) -> str:
    """
    View your inbox - items captured but not yet processed.

    These are items you've quickly captured that need to be reviewed,
    prioritized, or converted to actionable tasks.

    Args:
        limit: Number of items to return (default: 10)
    """
    try:
        result = await cos_request("GET", f"/api/cos/docs/inbox?limit={limit}")

        items = result.get("items", [])
        if not items:
            return "Inbox is empty. Nothing to process! ✓"

        output = [f"## Inbox ({len(items)} items)\n"]
        for item in items:
            doc_type = item.get("doc_type", "idea")
            content = item.get("content", "")[:70]
            doc_id = item.get("id", "")[:8]
            emoji = {"task": "☐", "idea": "💡", "note": "📝", "message": "💬"}.get(doc_type, "•")
            output.append(f"{emoji} {content} `{doc_id}`")

        return "\n".join(output)

    except Exception as e:
        return f"Error getting inbox: {str(e)}"


@mcp.tool()
async def cos_list(
    doc_type: str = None,
    status: str = None,
    priority: str = None,
    tags: list[str] = None,
    project: str = None,
    limit: int = 20,
) -> str:
    """
    List and filter your captured items.

    Use this to find specific items or browse your captured knowledge.

    Args:
        doc_type: Filter by type - "task", "idea", "note", "context", "message"
        status: Filter by status - "inbox", "todo", "in-progress", "blocked", "done", "archived"
        priority: Filter by priority - "high", "medium", "low"
        tags: Filter by tags (items must have ALL specified tags)
        project: Filter by project name
        limit: Max items to return (default: 20)
    """
    try:
        params = [f"limit={limit}"]
        if doc_type:
            params.append(f"doc_type={doc_type}")
        if status:
            params.append(f"status={status}")
        if priority:
            params.append(f"priority={priority}")
        if project:
            params.append(f"project={project}")
        if tags:
            for tag in tags:
                params.append(f"tags={tag}")

        query = "&".join(params)
        result = await cos_request("GET", f"/api/cos/docs?{query}")

        items = result.get("items", [])
        total = result.get("total", 0)

        if not items:
            return "No items found matching your filters."

        output = [f"## Found {total} items (showing {len(items)})\n"]
        for item in items:
            dt = item.get("doc_type", "")
            content = item.get("content", "")[:60]
            doc_id = item.get("id", "")[:8]
            status = item.get("status", "")
            emoji = {"task": "☐", "idea": "💡", "note": "📝", "context": "🎯", "message": "💬"}.get(dt, "•")
            output.append(f"{emoji} [{status}] {content} `{doc_id}`")

        return "\n".join(output)

    except Exception as e:
        return f"Error listing items: {str(e)}"


@mcp.tool()
async def cos_search_tags(tags: list[str] = None) -> str:
    """
    Get all your tags with counts, or search for items by tags.

    Use this to explore how your knowledge is organized and find
    related items across different captures.

    Args:
        tags: If provided, search for items with these tags.
              If omitted, returns all tags with counts.
    """
    try:
        if tags:
            # Search by tags
            tag_params = "&".join(f"tags={t}" for t in tags)
            result = await cos_request("GET", f"/api/cos/docs?{tag_params}&limit=20")

            items = result.get("items", [])
            if not items:
                return f"No items found with tags: {', '.join(tags)}"

            output = [f"## Items tagged [{', '.join(tags)}]\n"]
            for item in items:
                content = item.get("content", "")[:60]
                doc_id = item.get("id", "")[:8]
                output.append(f"- {content} `{doc_id}`")
            return "\n".join(output)
        else:
            # List all tags
            result = await cos_request("GET", "/api/cos/tags")

            tags_list = result.get("tags", [])
            if not tags_list:
                return "No tags found. Start tagging your captures!"

            output = ["## Your Tags\n"]
            for t in tags_list:
                output.append(f"- **{t['tag']}** ({t['count']} items)")
            return "\n".join(output)

    except Exception as e:
        return f"Error with tags: {str(e)}"


# =============================================================================
# Update Tools - Modify captured items
# =============================================================================

@mcp.tool()
async def cos_done(doc_id: str) -> str:
    """
    Mark a task or item as done.

    Args:
        doc_id: The ID of the item to mark done (can be partial, e.g., "abc123")
    """
    try:
        result = await cos_request("PATCH", f"/api/cos/docs/{doc_id}", {"status": "done"})
        content = result.get("content", "")[:50]
        return f"✓ Marked as done: \"{content}\""
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Item not found: {doc_id}"
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error marking done: {str(e)}"


@mcp.tool()
async def cos_update(
    doc_id: str,
    content: str = None,
    status: str = None,
    priority: str = None,
    tags: list[str] = None,
) -> str:
    """
    Update an existing item.

    Args:
        doc_id: The ID of the item to update
        content: New content (optional)
        status: New status - "inbox", "todo", "in-progress", "blocked", "done", "archived"
        priority: New priority - "high", "medium", "low"
        tags: Replace tags with this list
    """
    try:
        payload = {}
        if content:
            payload["content"] = content
        if status:
            payload["status"] = status
        if priority:
            payload["priority"] = priority
        if tags is not None:
            payload["tags"] = tags

        if not payload:
            return "Nothing to update. Provide at least one field to change."

        result = await cos_request("PATCH", f"/api/cos/docs/{doc_id}", payload)
        return f"✓ Updated: \"{result.get('content', '')[:50]}\""

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Item not found: {doc_id}"
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error updating: {str(e)}"


# =============================================================================
# Context Tools - Save and retrieve project context
# =============================================================================

@mcp.tool()
async def cos_save_context(
    summary: str,
    project: str = None,
    key_topics: list[str] = None,
    files_modified: list[str] = None,
    open_questions: list[str] = None,
) -> str:
    """
    Save a context snapshot - capture the current state of your work.

    Use this at the end of a work session to remember:
    - What you were working on
    - Key decisions made
    - Files changed
    - Open questions for next time

    This is invaluable for picking up where you left off.

    Args:
        summary: Summary of current work state and progress
        project: Project name (e.g., "code-smriti")
        key_topics: Main topics/areas being worked on
        files_modified: List of files changed in this session
        open_questions: Questions or uncertainties to resolve
    """
    try:
        payload = {
            "summary": summary,
        }
        if project:
            payload["project"] = project
        if key_topics:
            payload["key_topics"] = key_topics
        if files_modified:
            payload["files_modified"] = files_modified
        if open_questions:
            payload["open_questions"] = open_questions

        result = await cos_request("POST", "/api/cos/context", payload)

        ctx_id = result.get("id", "")[:8]
        return f"✓ Context saved for {project or 'general'} (id: {ctx_id})"

    except Exception as e:
        return f"Error saving context: {str(e)}"


@mcp.tool()
async def cos_get_context(project: str = None) -> str:
    """
    Retrieve the latest context snapshot.

    Use this at the START of a work session to remember where you left off.

    Args:
        project: Optional project name. If omitted, returns most recent context.
    """
    try:
        endpoint = f"/api/cos/context/{project}" if project else "/api/cos/context"
        result = await cos_request("GET", endpoint)

        if not result:
            return f"No context saved{f' for {project}' if project else ''}. Start fresh!"

        output = [f"## Last Context{f' - {project}' if project else ''}\n"]
        output.append(f"**Summary:** {result.get('summary', 'N/A')}\n")

        topics = result.get("key_topics", [])
        if topics:
            output.append(f"**Key Topics:** {', '.join(topics)}\n")

        files = result.get("files_modified", [])
        if files:
            output.append(f"**Files Modified:** {', '.join(files)}\n")

        questions = result.get("open_questions", [])
        if questions:
            output.append("**Open Questions:**")
            for q in questions:
                output.append(f"  - {q}")

        output.append(f"\n_Saved: {result.get('created_at', 'unknown')}_")

        return "\n".join(output)

    except Exception as e:
        return f"Error getting context: {str(e)}"


@mcp.tool()
async def cos_stats() -> str:
    """
    Get statistics about your captured items.

    Shows counts by type, status, and recent activity.
    """
    try:
        result = await cos_request("GET", "/api/cos/stats")

        output = ["## Chief of Staff Stats\n"]
        output.append(f"**Total Items:** {result.get('total_docs', 0)}\n")

        by_type = result.get("by_type", {})
        if by_type:
            output.append("**By Type:**")
            for t, count in by_type.items():
                emoji = {"task": "☐", "idea": "💡", "note": "📝", "context": "🎯"}.get(t, "•")
                output.append(f"  {emoji} {t}: {count}")
            output.append("")

        by_status = result.get("by_status", {})
        if by_status:
            output.append("**By Status:**")
            for s, count in by_status.items():
                output.append(f"  - {s}: {count}")

        return "\n".join(output)

    except Exception as e:
        return f"Error getting stats: {str(e)}"


if __name__ == "__main__":
    mcp.run()
