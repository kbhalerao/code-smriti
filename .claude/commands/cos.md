# Chief of Staff - Personal Productivity Assistant

You have access to the **Chief of Staff (CoS)** MCP tools for personal productivity.
Use these tools to capture and manage tasks, ideas, notes, and context.

## Available Tools

### Quick Capture
- `cos_capture` - Quickly capture anything (task, idea, note) to inbox
- `cos_task` - Create a specific task with priority and due date

### Retrieve & Search
- `cos_next` - Get your prioritized next actions (what to work on now)
- `cos_inbox` - View items waiting to be processed
- `cos_list` - List/filter items by type, status, priority, tags, project
- `cos_search_tags` - Browse tags or find items by tags

### Update
- `cos_done` - Mark an item as complete
- `cos_update` - Modify an existing item

### Context Management
- `cos_save_context` - Save a snapshot of current work state (end of session)
- `cos_get_context` - Retrieve last context (start of session)
- `cos_stats` - Get statistics about your captured items

## User Request

$ARGUMENTS

## Instructions

Based on the user's request above, use the appropriate CoS tool(s).

**Quick patterns:**
- "remind me to..." / "don't forget..." / "todo:" → `cos_task` or `cos_capture`
- "idea:" / "what if..." / "maybe we should..." → `cos_capture` with doc_type="idea"
- "note:" / "remember that..." → `cos_capture` with doc_type="note"
- "what should I work on" / "next" → `cos_next`
- "show inbox" / "what's pending" → `cos_inbox`
- "mark X done" / "complete X" → `cos_done`
- "save context" / "end session" → `cos_save_context`
- "where was I" / "resume" / "start session" → `cos_get_context`

If the request is ambiguous, use `cos_capture` with your best guess at doc_type.
Always tag items with relevant context from the current project when possible.
