"""
Chief of Staff MCP Server

Personal productivity tools for capturing tasks, ideas, notes, and context.
Integrates with CodeSmriti's Chief of Staff API.

Tools: cos_create, cos_list, cos_get, cos_update, cos_delete, cos_stats
"""

import os
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
# Core Tools (6 total)
# =============================================================================

@mcp.tool()
async def cos_create(
    content: str,
    doc_type: str = "task",
    status: str = "inbox",
    priority: str = "medium",
    tags: list[str] = None,
    project: str = None,
    due_date: str = None,
) -> str:
    """
    Create a new item (task, idea, note, or context).

    Args:
        content: The content/description of the item
        doc_type: Type - "task", "idea", "note", "context". Default: "task"
        status: Status - "inbox", "todo", "in-progress", "blocked", "done". Default: "inbox"
        priority: Priority - "high", "medium", "low". Default: "medium"
        tags: Optional list of tags (e.g., ["auth", "refactor"])
        project: Optional project name (e.g., "code-smriti")
        due_date: Optional due date in ISO format (e.g., "2025-01-15")
    """
    try:
        payload = {
            "doc_type": doc_type,
            "content": content,
            "status": status,
            "priority": priority,
        }
        if tags:
            payload["tags"] = tags
        if project:
            payload["source"] = {"project": project}
        if due_date:
            payload["due_date"] = due_date

        result = await cos_request("POST", "/api/cos/docs", payload)

        doc_id = result.get("id", "unknown")[:8]
        priority_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "")
        return f"✓ Created {doc_type} {priority_emoji}: \"{content[:50]}{'...' if len(content) > 50 else ''}\" (id: {doc_id})"

    except Exception as e:
        return f"Error creating: {str(e)}"


@mcp.tool()
async def cos_list(
    doc_type: str = None,
    status: str = None,
    priority: str = None,
    tags: list[str] = None,
    project: str = None,
    limit: int = 20,
    include_done: bool = False,
) -> str:
    """
    List and filter items.

    Args:
        doc_type: Filter by type - "task", "idea", "note", "context"
        status: Filter by status - "inbox", "todo", "in-progress", "blocked", "done", "archived"
        priority: Filter by priority - "high", "medium", "low"
        tags: Filter by tags (prefix match, e.g., "cos" matches "cos-api")
        project: Filter by project name
        limit: Max items to return (default: 20)
        include_done: Include done/archived items (default: False)

    Examples:
        - cos_list(status="inbox") - view inbox
        - cos_list(priority="high") - high priority items
        - cos_list(tags=["cos"]) - items with cos* tags
        - cos_list(doc_type="context", project="code-smriti", limit=1) - latest context
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
        if include_done:
            params.append("exclude_done=false")

        query = "&".join(params)
        result = await cos_request("GET", f"/api/cos/docs?{query}")

        items = result.get("items", [])
        if not items:
            return "No items found."

        output = [f"## {len(items)} items\n"]
        for item in items:
            dt = item.get("doc_type", "")
            content = item.get("content", "")[:60]
            doc_id = item.get("id", "")[:8]
            item_status = item.get("status", "")
            item_priority = item.get("priority", "")
            emoji = {"task": "☐", "idea": "💡", "note": "📝", "context": "🎯"}.get(dt, "•")
            p_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(item_priority, "")
            output.append(f"{emoji} {p_emoji} [{item_status}] {content} `{doc_id}`")

        return "\n".join(output)

    except Exception as e:
        return f"Error listing: {str(e)}"


@mcp.tool()
async def cos_get(doc_id: str) -> str:
    """
    Get a single item by ID.

    Args:
        doc_id: The ID of the item (can be partial, e.g., "abc123")
    """
    try:
        result = await cos_request("GET", f"/api/cos/docs/{doc_id}")

        doc_type = result.get("doc_type", "unknown")
        content = result.get("content", "")
        status = result.get("status", "")
        priority = result.get("priority", "")
        tags = result.get("tags", [])
        project = result.get("source", {}).get("project") if result.get("source") else None
        created = result.get("created_at", "")[:10]
        full_id = result.get("id", doc_id)

        emoji = {"task": "☐", "idea": "💡", "note": "📝", "context": "🎯"}.get(doc_type, "•")
        p_emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(priority, "")

        output = [f"## {emoji} {doc_type.title()} `{full_id[:8]}`\n"]
        output.append(f"**Content:** {content}\n")
        output.append(f"**Status:** {status}")
        if priority:
            output.append(f" {p_emoji} | **Priority:** {priority}")
        if tags:
            output.append(f"\n**Tags:** {', '.join(tags)}")
        if project:
            output.append(f"\n**Project:** {project}")
        output.append(f"\n**Created:** {created}")

        return "".join(output)

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Item not found: {doc_id}"
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def cos_update(
    doc_id: str,
    content: str = None,
    status: str = None,
    priority: str = None,
    tags: list[str] = None,
) -> str:
    """
    Update an item.

    Args:
        doc_id: The ID of the item (can be partial)
        content: New content (optional)
        status: New status - "inbox", "todo", "in-progress", "blocked", "done"
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
            return "Nothing to update. Provide at least one field."

        result = await cos_request("PATCH", f"/api/cos/docs/{doc_id}", payload)
        return f"✓ Updated: \"{result.get('content', '')[:50]}\""

    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Item not found: {doc_id}"
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def cos_delete(doc_id: str) -> str:
    """
    Archive an item (soft delete).

    Args:
        doc_id: The ID of the item to archive (can be partial)
    """
    try:
        await cos_request("DELETE", f"/api/cos/docs/{doc_id}")
        return f"✓ Archived: {doc_id}"
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            return f"Item not found: {doc_id}"
        return f"Error: {str(e)}"
    except Exception as e:
        return f"Error: {str(e)}"


@mcp.tool()
async def cos_stats() -> str:
    """
    Get statistics and tag counts.
    """
    try:
        # Get stats
        stats = await cos_request("GET", "/api/cos/stats")
        # Get tags
        tags_result = await cos_request("GET", "/api/cos/tags")

        output = ["## Stats\n"]
        output.append(f"**Total:** {stats.get('total_docs', 0)}")

        by_type = stats.get("by_type", {})
        if by_type:
            output.append("\n**By Type:**")
            for t, count in by_type.items():
                emoji = {"task": "☐", "idea": "💡", "note": "📝", "context": "🎯"}.get(t, "•")
                output.append(f"  {emoji} {t}: {count}")

        by_status = stats.get("by_status", {})
        if by_status:
            output.append("\n**By Status:**")
            for s, count in by_status.items():
                output.append(f"  - {s}: {count}")

        by_priority = stats.get("by_priority", {})
        if by_priority:
            output.append("\n**By Priority:**")
            for p, count in by_priority.items():
                emoji = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p, "")
                output.append(f"  {emoji} {p}: {count}")

        tags_list = tags_result.get("tags", [])
        if tags_list:
            output.append("\n**Tags:**")
            for t in tags_list[:10]:  # Top 10 tags
                output.append(f"  - {t['tag']} ({t['count']})")

        return "\n".join(output)

    except Exception as e:
        return f"Error: {str(e)}"


if __name__ == "__main__":
    mcp.run()
