# 4. Consume

Query and use your CodeSmriti knowledge base.

## Interfaces

CodeSmriti provides two ways to access your knowledge:

### MCP Server (Recommended)

Model Context Protocol server for seamless AI assistant integration.

**Features:**
- Natural language code search
- "Find similar code" queries
- Automatic context injection
- Works with Claude Desktop

**Setup:**
```bash
cd mcp-server
# Follow mcp-server/README.md for configuration
```

**Claude Desktop config** (`~/Library/Application Support/Claude/claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "codesmriti": {
      "command": "docker",
      "args": ["exec", "-i", "codesmriti_mcp", "python", "-m", "server"]
    }
  }
}
```

### REST API (Optional)

HTTP API for custom integrations.

**Endpoints:**
- `POST /search/code` - Vector search for code
- `POST /search/similar` - Find similar implementations
- `GET /repos` - List indexed repositories
- `GET /stats` - System statistics

**Setup:**
```bash
cd api-gateway
# Follow api-gateway/README.md
```

## Example Queries

### Natural Language Search

**Via MCP (in Claude):**
```
"Show me how we implemented rate limiting"
"Find authentication middleware examples"
"What's our best practice for error handling?"
```

**Via API:**
```bash
curl -X POST http://localhost:8000/search/code \
  -H "Content-Type: application/json" \
  -d '{
    "query": "authentication middleware",
    "language": "python",
    "limit": 5
  }'
```

### Find Similar Code

**Via MCP:**
```
"Find code similar to this:
def retry_with_backoff(func, max_retries=3):
    ...
"
```

**Via API:**
```bash
curl -X POST http://localhost:8000/search/similar \
  -H "Content-Type: application/json" \
  -d '{
    "code_snippet": "def retry_with_backoff...",
    "language": "python"
  }'
```

## Query Tips

**Be specific:**
- Good: "JWT authentication middleware in Express"
- Bad: "auth"

**Use filters:**
- By repository: `repo:owner/repo-name`
- By language: `language:python`
- By type: `type:function` or `type:class`

**Iterate:**
- Start broad, refine based on results
- Use top results to improve query

## Performance

**Vector search speed:**
- Typical query: 10-50ms
- With filters: 20-100ms
- Large result sets: 100-500ms

**Why so fast?**
- HNSW index: O(log N) instead of O(N)
- Checks ~100-200 candidates instead of all 50K chunks
- Apple Silicon MPS acceleration

## Common Use Cases

### 1. Onboarding

**"How do we handle database migrations?"**
→ Returns all migration-related code with examples

### 2. Architecture Decisions

**"Why did we choose JWT over sessions?"**
→ Finds commit messages, docs, and implementation

### 3. Best Practices

**"Show me our testing patterns for API endpoints"**
→ Returns test examples from multiple repos

### 4. Code Reuse

**"I need a retry mechanism with exponential backoff"**
→ Finds existing implementations you can adapt

### 5. Debugging

**"Find all places where we handle timeout errors"**
→ Shows every timeout error handler across repos

## Next Steps

- **Try some queries** - Test with your actual questions
- **Integrate with AI** - Add MCP to Claude Desktop
- **Automate updates** - Set up cron job for 3-maintain/update-repos
- **Share with team** - Add more repositories to index

## Troubleshooting

**No results:**
- Check data loaded: `cd ../2-initialize && ./verify-data`
- Try broader query
- Check language filter isn't too restrictive

**Slow queries:**
- Verify vector index exists
- Check Couchbase health
- Reduce result limit

**Stale results:**
- Run incremental update: `cd ../3-maintain && ./update-repos`
- Check last update time: `./stats`
