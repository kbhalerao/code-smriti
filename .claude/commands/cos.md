# Chief of Staff - Personal Productivity Assistant

You have access to the **Chief of Staff (CoS)** MCP tools for personal productivity.
Use these tools to capture and manage tasks, ideas, notes, and context.

## Tools (11 total)

| Tool | Purpose |
|------|---------|
| `cos_capture` | Quick capture ideas, notes, thoughts to inbox |
| `cos_task` | Create actionable tasks |
| `cos_next` | Get prioritized next actions |
| `cos_inbox` | View inbox items to process |
| `cos_list` | List/filter items by type, status, priority, tags |
| `cos_search_tags` | Get all tags or search by tags |
| `cos_done` | Mark item as done |
| `cos_update` | Update item fields |
| `cos_save_context` | Save work session context |
| `cos_get_context` | Retrieve last context |
| `cos_stats` | Stats + counts |

## User Request

$ARGUMENTS

## Instructions

Based on the user's request above, use the appropriate CoS tool(s).

**Patterns:**
- "remind me..." / "todo:" / "task:" → `cos_task(content)`
- "idea:" / "what if..." → `cos_capture(content, doc_type="idea")`
- "note:" → `cos_capture(content, doc_type="note")`
- "list" / "show" → `cos_list()`
- "inbox" → `cos_inbox()`
- "next" / "priority" / "what's next" → `cos_next()`
- "#tag" / "tagged" → `cos_search_tags(tags=["tag"])`
- "done X" / "complete X" → `cos_done(doc_id)`
- "update X" → `cos_update(doc_id, ...)`
- "stats" → `cos_stats()`
- "save context" / "end session" → `cos_save_context(summary, ...)`
- "where was I" / "resume" → `cos_get_context()`

If ambiguous, use `cos_capture` with best guess at doc_type.
Tag items with relevant project context when possible.
