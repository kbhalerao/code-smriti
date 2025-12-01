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
[Uses search_codebase(query="rate limiting implementation", level="file")]

Found 5 results at file level:
1. api-server/src/middleware/rate_limit.py - Redis-based rate limiting
2. gateway/src/throttle.ts - Token bucket algorithm
...
```

```
User: "Find the RateLimiter class"

Claude: I'll search at symbol level for the specific class.
[Uses search_codebase(query="RateLimiter class", level="symbol")]

Found RateLimiter class in middleware/rate_limit.py lines 45-120
```

```
User: "What are our rate limiting guidelines?"

Claude: I'll search documentation for rate limiting guidelines.
[Uses search_codebase(query="rate limiting guidelines", level="doc")]

Found design doc explaining our rate limiting strategy...
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

    def search_codebase(self, query: str, level: str = "file",
                        repo_filter: str = None, limit: int = 5, preview: bool = False):
        """
        Search for code in CodeSmriti at specified granularity.

        Args:
            query: Search query (semantic or keyword)
            level: Search level - symbol, file, module, repo, or doc
            repo_filter: Optional repository filter (e.g., "owner/repo")
            limit: Max results (default 5, max 20)
            preview: If True, return truncated content for quick scanning
        """
        payload = {
            "query": query,
            "level": level,
            "limit": limit,
            "preview": preview
        }
        if repo_filter:
            payload["repo_filter"] = repo_filter

        response = httpx.post(
            f"{self.base_url}/api/rag/search",
            headers=self.headers,
            json=payload,
            timeout=60.0
        )
        response.raise_for_status()
        return response.json()

    def get_file(self, repo_id: str, file_path: str,
                 start_line: int = None, end_line: int = None):
        """Retrieve actual source code from a file."""
        payload = {"repo_id": repo_id, "file_path": file_path}
        if start_line:
            payload["start_line"] = start_line
        if end_line:
            payload["end_line"] = end_line

        response = httpx.post(
            f"{self.base_url}/api/rag/file",
            headers=self.headers,
            json=payload,
            timeout=60.0
        )
        response.raise_for_status()
        return response.json()

    def list_repos(self):
        """List all indexed repositories."""
        response = httpx.get(
            f"{self.base_url}/api/rag/repos",
            headers=self.headers,
            timeout=30.0
        )
        response.raise_for_status()
        return response.json()

    def ask_codebase(self, query: str):
        """Ask a question and get an LLM-synthesized answer."""
        response = httpx.post(
            f"{self.base_url}/api/rag/",
            headers=self.headers,
            json={"query": query, "stream": False},
            timeout=120.0
        )
        response.raise_for_status()
        return response.json()

# Usage
client = CodeSmritiClient(
    base_url="http://YOUR_MAC_IP:8080",
    api_key="YOUR_API_KEY"
)

# Search for code at file level (default)
results = client.search_codebase(
    query="user authentication",
    level="file",
    limit=5
)
for result in results["results"]:
    print(f"Found in {result['repo_id']}/{result['file_path']}")
    print(result["content"][:200])
    print("---")

# Search for specific function at symbol level
results = client.search_codebase(
    query="authenticate_user function",
    level="symbol"
)

# Search documentation for guidelines
results = client.search_codebase(
    query="authentication guidelines",
    level="doc"
)

# Get full file content
file_content = client.get_file(
    repo_id="owner/repo",
    file_path="src/auth/middleware.py"
)
print(file_content["code"])
```

### JavaScript/TypeScript Client Example

```typescript
import axios from 'axios';

interface SearchOptions {
  level?: 'symbol' | 'file' | 'module' | 'repo' | 'doc';
  repoFilter?: string;
  limit?: number;
  preview?: boolean;
}

class CodeSmritiClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(baseUrl: string, apiKey: string) {
    this.baseUrl = baseUrl;
    this.apiKey = apiKey;
  }

  private get headers() {
    return {
      'Authorization': `Bearer ${this.apiKey}`,
      'Content-Type': 'application/json'
    };
  }

  async searchCodebase(query: string, options: SearchOptions = {}) {
    const response = await axios.post(
      `${this.baseUrl}/api/rag/search`,
      {
        query,
        level: options.level || 'file',
        limit: options.limit || 5,
        repo_filter: options.repoFilter,
        preview: options.preview || false
      },
      { headers: this.headers, timeout: 60000 }
    );
    return response.data;
  }

  async getFile(repoId: string, filePath: string, startLine?: number, endLine?: number) {
    const payload: any = { repo_id: repoId, file_path: filePath };
    if (startLine) payload.start_line = startLine;
    if (endLine) payload.end_line = endLine;

    const response = await axios.post(
      `${this.baseUrl}/api/rag/file`,
      payload,
      { headers: this.headers, timeout: 60000 }
    );
    return response.data;
  }

  async listRepos() {
    const response = await axios.get(
      `${this.baseUrl}/api/rag/repos`,
      { headers: this.headers, timeout: 30000 }
    );
    return response.data;
  }

  async askCodebase(query: string) {
    const response = await axios.post(
      `${this.baseUrl}/api/rag/`,
      { query, stream: false },
      { headers: this.headers, timeout: 120000 }
    );
    return response.data;
  }
}

// Usage
const client = new CodeSmritiClient(
  'http://YOUR_MAC_IP:8080',
  'YOUR_API_KEY'
);

// Search at file level (default)
const fileResults = await client.searchCodebase('authentication', {
  level: 'file',
  limit: 5
});
console.log(fileResults);

// Search at symbol level for specific function
const symbolResults = await client.searchCodebase('UserModel class', {
  level: 'symbol'
});

// Search documentation
const docResults = await client.searchCodebase('API guidelines', {
  level: 'doc'
});

// Get file content
const fileContent = await client.getFile('owner/repo', 'src/auth/models.py');
console.log(fileContent.code);
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
1. search_codebase("retry mechanism implementation", level="file")
2. get_file(repo_id, file_path) for the most relevant files
3. Presents 3-5 best examples with analysis
```

### 2. Understanding Architecture

```
User: "What's the structure of the auth module?"

Assistant uses:
1. search_codebase("authentication architecture", level="module")
2. explore_structure(repo_id, "auth/")
3. Provides overview of the module with key files
```

### 3. Onboarding New Engineers

```
User: "I'm new to the auth system. Explain how it works."

Assistant uses:
1. search_codebase("authentication flow", level="file")
2. search_codebase("authentication guidelines", level="doc")
3. get_file(repo_id, file_path) for key implementation files
4. Provides guided tour with code and documentation links
```

### 4. Finding Specific Code

```
User: "Find where we define the UserModel class"

Assistant uses:
1. search_codebase("UserModel class definition", level="symbol")
2. get_file(repo_id, file_path, start_line, end_line) for exact code
3. Returns precise location and implementation
```

### 5. Research Documentation

```
User: "What are our API design guidelines?"

Assistant uses:
1. search_codebase("API design guidelines", level="doc")
2. Returns relevant documentation files (RST, MD)
3. Synthesizes guidelines from documentation
```

## Best Practices

### For Users

1. **Choose the Right Level**:
   - `symbol`: Find specific functions/classes by name
   - `file`: Default for implementation questions ("how does X work")
   - `module`: Understand folder structure and organization
   - `repo`: High-level repository overview
   - `doc`: Find documentation, guidelines, design decisions

2. **Use Preview Mode**: Start with `preview=true` to scan results before fetching full content

3. **Be Specific in Queries**: "user authentication with JWT" > "auth"

4. **Filter by Repository**: Use `repo_filter` when you know which repo to search

5. **Combine Tools**: Use `search_codebase` to find, then `get_file` for full content

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
