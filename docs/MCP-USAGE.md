# Using CodeSmriti with MCP Clients

*Smriti (स्मृति): Sanskrit for "memory, remembrance"*

Guide for connecting CodeSmriti to MCP clients (Claude Code, Claude Desktop, custom applications).

## Overview

CodeSmriti provides a codebase intelligence platform with:
- **Hierarchical document indexing**: repo → module → file → symbol
- **Two-tier intelligence**: Local LLM (Qwen3) for routine queries, SOTA models for complex reasoning
- **MCP protocol support**: Works with Claude Code, Claude Desktop, and custom clients

**External URL**: `https://smriti.agsci.com`

## Connecting from Claude Code (Recommended)

Claude Code runs the MCP server locally, which calls the CodeSmriti API.

### 1. Prerequisites

- Python 3.11+
- CodeSmriti credentials (email/password)
- Clone the code-smriti repository

### 2. Configure Claude Code

Create or edit `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "code-smriti": {
      "command": "uv",
      "args": ["run", "python", "-m", "services.mcp-server.rag_mcp_server"],
      "cwd": "/path/to/code-smriti",
      "env": {
        "CODESMRITI_API_URL": "https://smriti.agsci.com",
        "CODESMRITI_USERNAME": "your-email@example.com",
        "CODESMRITI_PASSWORD": "your-password"
      }
    }
  }
}
```

Alternatively, create a `.env` file in the code-smriti directory:

```bash
CODESMRITI_API_URL=https://smriti.agsci.com
CODESMRITI_USERNAME=your-email@example.com
CODESMRITI_PASSWORD=your-password
```

### 3. Available MCP Tools

Once connected, Claude Code has access to these tools:

| Tool | Purpose | Example |
|------|---------|---------|
| `list_repos` | Discover indexed repositories | "What repos are indexed?" |
| `explore_structure` | Navigate directory structure | "Show me the structure of labcore" |
| `search_codebase` | Semantic search at any level | "Find authentication code" |
| `get_file` | Retrieve source code | "Show me auth/__init__.py" |
| `ask_codebase` | Developer Q&A (uses local LLM) | "How does the job queue work?" |
| `ask_agsci` | Capability/proposal questions | "Can we build a GIS app?" |
| `affected_tests` | Find tests for changed files | "What tests cover this module?" |
| `get_module_criticality` | Module importance analysis | "How critical is common.models?" |
| `get_graph_info` | Dependency graph summary | "Show me the graph for labcore" |

### 4. Verify Connection

In Claude Code, run:

```
/mcp
```

You should see `code-smriti` listed as an available server.

Then try:

```
What repositories are indexed in code-smriti?
```

Claude will use the `list_repos` tool to query the index.

## Connecting from Claude Desktop

Claude Desktop requires HTTP/SSE transport for MCP servers. CodeSmriti currently only provides a **stdio-based MCP server** (runs locally as a subprocess).

### Current Status

**Not directly supported.** The MCP server runs locally via stdio transport and makes HTTP calls to `https://smriti.agsci.com`. There is no HTTP MCP endpoint.

### Workaround: Run Locally

If Claude Desktop supports stdio transport (check Claude Desktop docs), configure it the same way as Claude Code:

Edit the MCP settings file:
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "code-smriti": {
      "command": "uv",
      "args": ["run", "python", "-m", "services.mcp-server.rag_mcp_server"],
      "cwd": "/path/to/code-smriti",
      "env": {
        "CODESMRITI_API_URL": "https://smriti.agsci.com",
        "CODESMRITI_USERNAME": "your-email@example.com",
        "CODESMRITI_PASSWORD": "your-password"
      }
    }
  }
}
```

### Future: HTTP Transport

HTTP/SSE MCP transport could be added in the future. This would require implementing an MCP HTTP endpoint on the API server.

## Connecting from VSCode

### Using Continue.dev Extension

Continue.dev supports MCP servers via stdio transport (same as Claude Code).

1. **Install Continue.dev** extension in VSCode

2. **Configure Continue** to use CodeSmriti:

Edit `~/.continue/config.json`:

```json
{
  "mcpServers": [
    {
      "name": "code-smriti",
      "command": "uv",
      "args": ["run", "python", "-m", "services.mcp-server.rag_mcp_server"],
      "cwd": "/path/to/code-smriti",
      "env": {
        "CODESMRITI_API_URL": "https://smriti.agsci.com",
        "CODESMRITI_USERNAME": "your-email@example.com",
        "CODESMRITI_PASSWORD": "your-password"
      }
    }
  ]
}
```

3. **Use in VSCode**:
   - Open Continue chat panel
   - The MCP tools will be available automatically
   - Ask questions about your indexed codebases

## Connecting from Custom Applications

### Python Client Example

```python
import httpx

class CodeSmritiClient:
    def __init__(self, base_url: str, token: str):
        """
        Args:
            base_url: API URL (e.g., "https://smriti.agsci.com")
            token: JWT access token from /api/auth/login
        """
        self.base_url = base_url.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def search_codebase(self, query: str, level: str = "file",
                        repo_filter: str = None, limit: int = 5, preview: bool = False):
        """
        Search for code at specified granularity.

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

    def ask_code(self, query: str, conversation_history: list = None):
        """
        Ask a developer question about the codebase.
        Uses unified RAG pipeline with intent classification.
        """
        payload = {"query": query}
        if conversation_history:
            payload["conversation_history"] = conversation_history

        response = httpx.post(
            f"{self.base_url}/api/rag/ask/code",
            headers=self.headers,
            json=payload,
            timeout=180.0  # Extended for multi-step pipeline
        )
        response.raise_for_status()
        return response.json()

    def ask_proposal(self, query: str, conversation_history: list = None):
        """
        Ask about capabilities for proposals/sales.
        Uses unified RAG pipeline with sales persona.
        """
        payload = {"query": query}
        if conversation_history:
            payload["conversation_history"] = conversation_history

        response = httpx.post(
            f"{self.base_url}/api/rag/ask/proposal",
            headers=self.headers,
            json=payload,
            timeout=180.0
        )
        response.raise_for_status()
        return response.json()


def get_token(base_url: str, username: str, password: str) -> str:
    """Authenticate and get JWT token."""
    response = httpx.post(
        f"{base_url}/api/auth/login",
        json={"email": username, "password": password},
        timeout=30.0
    )
    response.raise_for_status()
    return response.json()["token"]  # Returns "token", not "access_token"


# Usage
BASE_URL = "https://smriti.agsci.com"
token = get_token(BASE_URL, "your-email@example.com", "your-password")
client = CodeSmritiClient(BASE_URL, token)

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

# Ask a developer question (uses unified RAG pipeline)
answer = client.ask_code("How does the job queue system work?")
print(f"Intent: {answer['intent']}")
print(f"Answer: {answer['answer']}")
print(f"Sources: {answer['sources']}")

# Ask about capabilities (sales persona)
capabilities = client.ask_proposal("Can we build a GIS platform for farm management?")
print(capabilities["answer"])
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

interface ConversationMessage {
  role: 'user' | 'assistant';
  content: string;
}

class CodeSmritiClient {
  private baseUrl: string;
  private token: string;

  constructor(baseUrl: string, token: string) {
    this.baseUrl = baseUrl.replace(/\/$/, '');
    this.token = token;
  }

  private get headers() {
    return {
      'Authorization': `Bearer ${this.token}`,
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
    const payload: Record<string, any> = { repo_id: repoId, file_path: filePath };
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

  async askCode(query: string, conversationHistory?: ConversationMessage[]) {
    const response = await axios.post(
      `${this.baseUrl}/api/rag/ask/code`,
      { query, conversation_history: conversationHistory },
      { headers: this.headers, timeout: 180000 }
    );
    return response.data;
  }

  async askProposal(query: string, conversationHistory?: ConversationMessage[]) {
    const response = await axios.post(
      `${this.baseUrl}/api/rag/ask/proposal`,
      { query, conversation_history: conversationHistory },
      { headers: this.headers, timeout: 180000 }
    );
    return response.data;
  }
}

async function getToken(baseUrl: string, email: string, password: string): Promise<string> {
  const response = await axios.post(
    `${baseUrl}/api/auth/login`,
    { email, password },
    { timeout: 30000 }
  );
  return response.data.token;  // Returns "token", not "access_token"
}

// Usage
const BASE_URL = 'https://smriti.agsci.com';
const token = await getToken(BASE_URL, 'your-email@example.com', 'your-password');
const client = new CodeSmritiClient(BASE_URL, token);

// Search at file level (default)
const fileResults = await client.searchCodebase('authentication', {
  level: 'file',
  limit: 5
});
console.log(fileResults);

// Ask a developer question (uses unified RAG pipeline)
const answer = await client.askCode('How does the job queue system work?');
console.log('Intent:', answer.intent);
console.log('Answer:', answer.answer);
console.log('Sources:', answer.sources);

// Ask about capabilities (sales persona)
const capabilities = await client.askProposal('Can we build a GIS platform?');
console.log(capabilities.answer);
```

## Two-Tier Intelligence

CodeSmriti provides a two-tier intelligence architecture:

### Local LLM Tier (Qwen3)
- Fast, private, constrained queries
- Handles routine code questions and specific lookups
- Tools: `ask_codebase`, `ask_agsci`
- Uses intent classification + progressive retrieval + synthesis

### SOTA Tier (Claude via Claude Code)
- Complex reasoning and cross-cutting analysis
- Multi-step debugging and architectural decisions
- Claude decides when to delegate to local tools vs. reason directly
- Uses MCP tools to access the indexed knowledge base

### How It Works

When using Claude Code with CodeSmriti MCP:

1. **Claude decides** whether to use local tools or reason directly
2. **For routine queries**: Claude calls `ask_codebase` (developer) or `ask_agsci` (sales)
3. **For exploration**: Claude uses `search_codebase`, `explore_structure`, `get_file`
4. **For complex analysis**: Claude reasons over the retrieved context itself

The local LLM handles:
- Intent classification (code_explanation, architecture, capability_check, etc.)
- Query expansion (adding synonyms and related terms)
- Synthesis with citations

Claude Code handles:
- Deciding which tools to use
- Complex multi-step reasoning
- Cross-repository analysis
- Architectural recommendations

## Common Use Cases

### Developer Persona

**Understanding Code** (intent: `code_explanation`)
```
User: "How does the job queue work?"

Claude uses: ask_codebase("How does the job queue work?")
→ Intent classification identifies code_explanation
→ Progressive retrieval at file/symbol level
→ Local LLM synthesizes answer with citations
```

**Architecture Overview** (intent: `architecture`)
```
User: "What's the structure of the API server?"

Claude uses:
1. explore_structure(repo_id, "api-server/")
2. search_codebase("API architecture", level="module")
→ Returns directory tree + module summaries
```

**Finding Specific Code** (intent: `specific_lookup`)
```
User: "Find the UserModel class"

Claude uses: search_codebase("UserModel class", level="symbol")
→ Returns precise location with line numbers
→ Can follow up with get_file() for full context
```

**Impact Analysis** (intent: `impact_analysis`)
```
User: "What depends on common.models?"

Claude uses:
1. get_module_criticality("common.models")
2. affected_tests(["common/models/__init__.py"])
→ Returns PageRank score + dependents + affected tests
```

### Sales Persona

**Capability Check** (intent: `capability_check`)
```
User: "Can we build a mobile app with offline sync?"

Claude uses: ask_agsci("mobile app offline sync capabilities")
→ Searches BDR briefs and repo summaries
→ Returns business-focused capability assessment
```

**Proposal Drafting** (intent: `proposal_draft`)
```
User: "Draft the technical approach for a GIS platform"

Claude uses: ask_agsci("technical approach GIS platform")
→ Gathers relevant experience from indexed projects
→ Synthesizes proposal section with citations
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

1. **User Management**:
   - Users authenticate via `/api/auth/login` with email/password
   - JWT tokens are used for all API requests
   - Create accounts via the admin interface or API

2. **Monitor Usage**:
   ```bash
   # Check API server logs
   docker-compose logs -f api-server

   # Check system health
   curl https://smriti.agsci.com/health
   ```

3. **Ingestion Pipeline**:
   - Repositories are indexed via the ingestion-worker service
   - V4 hierarchy: repo_summary → module_summary → file_index → symbol_index
   - BDR briefs (repo_bdr) are generated for sales/proposal capabilities

4. **Service Architecture**:
   ```
   api-gateway (nginx)     → Routes requests, handles SSL
   api-server (FastAPI)    → Main API, RAG pipeline
   ingestion-worker        → Repository indexing
   couchbase               → Vector store + FTS
   LM Studio (local)       → Local LLM inference
   ```

## Troubleshooting

### Connection Issues

**Problem**: Client can't connect to CodeSmriti

**Solutions**:
1. Verify CodeSmriti is running: `curl https://smriti.agsci.com/health`
2. Check your network can reach the server
3. If running locally, ensure Docker containers are up: `docker-compose ps`
4. Verify credentials are correct

### Authentication Failures

**Problem**: "401 Unauthorized" errors

**Solutions**:
1. Check credentials are correctly configured (no extra spaces)
2. Verify token hasn't expired - re-authenticate if needed
3. Ensure the MCP server has correct environment variables set
4. Check MCP server logs: `~/.claude/logs/` (for Claude Code)

### Slow Responses

**Problem**: Tool calls timeout or take too long

**Solutions**:
1. The unified RAG pipeline has extended timeouts (180s) for multi-step processing
2. `ask_codebase` and `ask_agsci` involve intent classification + retrieval + synthesis
3. For faster results, use `search_codebase` directly (Claude does its own synthesis)
4. Reduce search limit: Use `limit: 5` instead of `limit: 20`
5. Add repo_filter when you know which repository to search

## Security Considerations

1. **Never Commit API Keys**: Add to .gitignore
2. **Use HTTPS in Production**: Configure SSL certificates
3. **Scope Keys Appropriately**: Read-only for most users
4. **Monitor Access Logs**: Review for suspicious activity
5. **Network Isolation**: Use VPN for remote access

## API Endpoints Reference

### Unified RAG Pipeline

| Endpoint | Persona | Description |
|----------|---------|-------------|
| `POST /api/rag/ask` | Configurable | Unified pipeline with intent classification |
| `POST /api/rag/ask/code` | Developer | Code questions (code_explanation, architecture, etc.) |
| `POST /api/rag/ask/proposal` | Sales | Capability/proposal questions |

**Request body**:
```json
{
  "query": "How does authentication work?",
  "conversation_history": [
    {"role": "user", "content": "previous question"},
    {"role": "assistant", "content": "previous answer"}
  ]
}
```

**Response**:
```json
{
  "answer": "Authentication in this codebase...",
  "intent": "code_explanation",
  "direction": "narrow",
  "sources": ["repo/auth/middleware.py", "repo/auth/models.py"],
  "levels_searched": ["file", "symbol"],
  "adequate_context": true,
  "gaps": []
}
```

### Search & Retrieval

| Endpoint | Description |
|----------|-------------|
| `POST /api/rag/search` | Semantic search at any level |
| `POST /api/rag/file` | Retrieve file content |
| `GET /api/rag/repos` | List indexed repositories |

## Architecture Documentation

For detailed architecture documentation, see:
- [RAG_PIPELINE_ARCHITECTURE.md](RAG_PIPELINE_ARCHITECTURE.md) - Pipeline internals
- [FTS_VECTOR_SEARCH.md](FTS_VECTOR_SEARCH.md) - Vector search configuration

## Support

For MCP integration help:
1. Review [MCP specification](https://spec.modelcontextprotocol.io)
2. Check MCP server logs in `~/.claude/logs/`
3. Test endpoints with curl before integrating
4. See architecture docs for pipeline behavior
