# Using CodeSmriti with MCP Clients

*Smriti (स्मृति): Sanskrit for "memory, remembrance"*

Guide for connecting CodeSmriti to various MCP clients (Claude Desktop, VSCode, custom applications).

## Overview

CodeSmriti implements the Model Context Protocol (MCP) specification and can be used with any MCP-compatible client. The server supports both:
- **stdio transport**: For local, single-user connections
- **HTTP/SSE transport**: For remote, multi-user connections (recommended)

## Connecting from Claude Desktop

### Setup for Remote Access (HTTP/SSE)

1. **Start CodeSmriti** on your Mac Studio:
```bash
cd CodeSmriti
./scripts/start.sh
```

2. **Generate an API key** for yourself:
```bash
./scripts/generate-api-key.py
```

3. **Configure Claude Desktop** to connect to CodeSmriti:

Edit Claude Desktop's MCP settings file:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

Add CodeSmriti server configuration:

```json
{
  "mcpServers": {
    "codesmriti": {
      "url": "http://YOUR_MAC_IP:8080/mcp",
      "transport": {
        "type": "http",
        "headers": {
          "Authorization": "Bearer YOUR_API_KEY_HERE"
        }
      }
    }
  }
}
```

4. **Restart Claude Desktop**

5. **Verify Connection**:
   - Open Claude Desktop
   - Type: "List available tools from CodeSmriti"
   - You should see the MCP tools available

### Example Usage in Claude Desktop

Once connected, you can use CodeSmriti tools naturally in conversation:

```
User: "Search our codebase for how we implement rate limiting"

Claude: I'll search the CodeSmriti knowledge base for rate limiting implementations.
[Uses search_code tool]

Found 5 implementations:
1. In api-server/src/middleware/rate_limit.py - Redis-based rate limiting
2. In gateway/src/throttle.ts - Token bucket algorithm
...
```

```
User: "Add a note that we prefer Redis for rate limiting because it's distributed"

Claude: I'll add that architecture decision to CodeSmriti.
[Uses add_note tool with hashtags: rate-limiting, architecture-decision]

Note created and tagged for future reference.
```

## Connecting from VSCode

### Using Continue.dev Extension

1. **Install Continue.dev** extension in VSCode

2. **Configure Continue** to use CodeSmriti:

Edit `~/.continue/config.json`:

```json
{
  "mcpServers": {
    "codesmriti": {
      "url": "http://YOUR_MAC_IP:8080/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  },
  "tools": [
    {
      "name": "search_code",
      "description": "Search CodeSmriti's indexed codebase",
      "server": "codesmriti"
    },
    {
      "name": "add_note",
      "description": "Add a memory note to CodeSmriti",
      "server": "codesmriti"
    }
  ]
}
```

3. **Use in VSCode**:
   - Open Continue chat panel
   - Ask: "@codesmriti search for authentication examples"
   - Continue will use the MCP tools to search CodeSmriti

## Connecting from Custom Applications

### Python Client Example

```python
import httpx
import json

class CodeSmritiClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }

    def search_code(self, query: str, **kwargs):
        """Search for code in CodeSmriti"""
        response = httpx.post(
            f"{self.base_url}/mcp/tools/call",
            headers=self.headers,
            json={
                "name": "search_code",
                "arguments": {
                    "query": query,
                    **kwargs
                }
            }
        )
        response.raise_for_status()
        result = response.json()
        return json.loads(result["content"][0]["text"])

    def add_note(self, content: str, hashtags: list = None, project: str = None):
        """Add a note to CodeSmriti"""
        response = httpx.post(
            f"{self.base_url}/mcp/tools/call",
            headers=self.headers,
            json={
                "name": "add_note",
                "arguments": {
                    "content": content,
                    "hashtags": hashtags or [],
                    "project": project
                }
            }
        )
        response.raise_for_status()
        return response.json()

# Usage
client = CodeSmritiClient(
    base_url="http://YOUR_MAC_IP:8080",
    api_key="YOUR_API_KEY"
)

# Search for code
results = client.search_code(
    query="user authentication",
    language="python",
    limit=5
)

for result in results["results"]:
    print(f"Found in {result['repo_id']}/{result['file_path']}")
    print(result["code_text"][:200])
    print("---")

# Add a note
client.add_note(
    content="Learned that we use bcrypt for password hashing",
    hashtags=["security", "authentication"],
    project="api-server"
)
```

### JavaScript/TypeScript Client Example

```typescript
import axios from 'axios';

class CodeSmritiClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(baseUrl: string, apiKey: string) {
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
  }

  async searchCode(query: string, options: {
    repo?: string;
    language?: string;
    limit?: number;
  } = {}) {
    const response = await axios.post(
      `${this.baseUrl}/mcp/tools/call`,
      {
        name: 'search_code',
        arguments: {
          query,
          ...options
        }
      },
      {
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json'
        }
      }
    );

    const result = response.data.content[0].text;
    return JSON.parse(result);
  }

  async addNote(content: string, hashtags?: string[], project?: string) {
    const response = await axios.post(
      `${this.baseUrl}/mcp/tools/call`,
      {
        name: 'add_note',
        arguments: {
          content,
          hashtags: hashtags || [],
          project
        }
      },
      {
        headers: {
          'Authorization': `Bearer ${this.apiKey}`,
          'Content-Type': 'application/json'
        }
      }
    );

    return response.data;
  }
}

// Usage
const client = new CodeSmritiClient(
  'http://YOUR_MAC_IP:8080',
  'YOUR_API_KEY'
);

// Search
const results = await client.searchCode('authentication', {
  language: 'typescript',
  limit: 5
});

console.log(results);

// Add note
await client.addNote(
  'Use Zod for runtime validation',
  ['validation', 'best-practices'],
  'frontend'
);
```

## Agentic Workflows

CodeSmriti is designed for agentic loops where an LLM iteratively uses tools.

### Example: Progressive Research

```python
# Anthropic Claude SDK example
from anthropic import Anthropic

client = Anthropic(api_key="...")

# Configure CodeSmriti as MCP server
mcp_tools = [
    {
        "name": "search_code",
        "description": "Search CodeSmriti's indexed code",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "repo": {"type": "string"},
                "language": {"type": "string"},
                "limit": {"type": "integer"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "query_by_hashtag",
        "description": "Retrieve content by hashtags",
        "input_schema": {
            "type": "object",
            "properties": {
                "hashtags": {"type": "array", "items": {"type": "string"}},
                "content_type": {"type": "string"}
            },
            "required": ["hashtags"]
        }
    }
]

messages = [{
    "role": "user",
    "content": "Find all our authentication implementations and summarize the common patterns"
}]

# Agent loop
while True:
    response = client.messages.create(
        model="claude-sonnet-4-5-20250929",
        max_tokens=4096,
        tools=mcp_tools,
        messages=messages
    )

    if response.stop_reason == "end_turn":
        # Agent finished
        print(response.content[0].text)
        break

    # Execute tool calls
    for content_block in response.content:
        if content_block.type == "tool_use":
            # Call CodeSmriti
            tool_result = call_codesmriti_tool(
                content_block.name,
                content_block.input
            )

            # Add result to conversation
            messages.append({
                "role": "assistant",
                "content": response.content
            })
            messages.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": content_block.id,
                    "content": tool_result
                }]
            })
```

## Common Use Cases

### 1. Finding Similar Implementations

```
User: "I need to implement a retry mechanism. Show me similar code in our repos."

Assistant uses:
1. search_code("retry mechanism implementation")
2. find_similar(code_snippet_from_results)
3. Presents 3-5 best examples with pros/cons
```

### 2. Collecting Architecture Decisions

```
User: "Collect all decisions about our API design and create a summary"

Assistant uses:
1. query_by_hashtag(["api-design", "architecture-decision"])
2. Analyzes all tagged notes and code
3. Creates summary document
4. add_note(summary, hashtags=["api-design", "summary"])
```

### 3. Onboarding New Engineers

```
User: "I'm new to the auth system. Explain how it works."

Assistant uses:
1. search_code("authentication main flow")
2. get_code_context(repo, file_path) for key files
3. query_by_hashtag(["authentication", "onboarding"])
4. Provides guided tour with links to code
```

### 4. Generating PRDs from Ideas

```
User: "Collect all ideas tagged #mobile-app and create a PRD using patterns from our backend services"

Assistant uses:
1. query_by_hashtag(["mobile-app"])
2. search_code("service architecture", repo="backend")
3. Generates PRD leveraging existing patterns
4. add_note(PRD, hashtags=["mobile-app", "prd"])
```

## Best Practices

### For Users

1. **Be Specific in Queries**: "user authentication with JWT" > "auth"
2. **Use Hashtags Consistently**: Establish team conventions
3. **Add Context to Notes**: Include "why" not just "what"
4. **Tag Projects**: Makes filtering easier
5. **Regular Reviews**: Query by hashtag monthly to surface insights

### For Administrators

1. **API Key Management**:
   - Create separate keys per user
   - Set appropriate scopes (read/write/admin)
   - Rotate keys quarterly

2. **Monitor Usage**:
   ```bash
   # Check MCP server logs
   docker logs codesmriti_mcp_server --tail=100

   # Check system status
   curl -H "Authorization: Bearer $API_KEY" \
     http://localhost/api/status
   ```

3. **Regular Re-indexing**:
   ```bash
   # Trigger weekly re-indexing via cron
   0 2 * * 0 curl -X POST -H "Authorization: Bearer $ADMIN_KEY" \
     http://localhost/api/ingest/trigger
   ```

4. **Backup Strategy**:
   ```bash
   # Backup Couchbase data
   docker exec codesmriti_couchbase cbbackup \
     http://localhost:8091 /backup/$(date +%Y%m%d)
   ```

## Troubleshooting

### Connection Issues

**Problem**: Client can't connect to CodeSmriti

**Solutions**:
1. Verify CodeSmriti is running: `curl http://localhost:8080/health`
2. Check firewall allows port 8080
3. Ensure correct IP address (not localhost if remote)
4. Verify API key is valid

### Authentication Failures

**Problem**: "401 Unauthorized" errors

**Solutions**:
1. Check API key is correctly copied (no extra spaces)
2. Verify key hasn't expired: `docker logs codesmriti_mcp_server | grep "Token expired"`
3. Generate new key if needed: `./scripts/generate-api-key.py`

### Slow Responses

**Problem**: Tool calls timeout or take too long

**Solutions**:
1. Check Couchbase performance: `curl http://localhost:8091/pools/default`
2. Reduce search limit: Use `limit: 5` instead of `limit: 50`
3. Add more specific filters (repo, language)
4. Check if re-indexing is running: `docker logs codesmriti_ingestion_worker`

## Security Considerations

1. **Never Commit API Keys**: Add to .gitignore
2. **Use HTTPS in Production**: Configure SSL certificates
3. **Scope Keys Appropriately**: Read-only for most users
4. **Monitor Access Logs**: Review for suspicious activity
5. **Network Isolation**: Use VPN for remote access

## Advanced: Custom MCP Server Integration

If you're building your own MCP server that needs to query CodeSmriti:

```python
# Your MCP server
@app.tool()
async def research_topic(topic: str) -> str:
    """Research a topic using CodeSmriti + other sources"""

    # Query CodeSmriti
    codesmriti_results = await query_codesmriti(topic)

    # Combine with other sources
    # ...

    return combined_research
```

This allows chaining MCP servers for complex workflows.

## Support

For MCP integration help:
1. Review [MCP specification](https://spec.modelcontextprotocol.io)
2. Check CodeSmriti logs: `docker-compose logs -f mcp-server`
3. Test with curl first before integrating
4. Contact the team for custom integrations
