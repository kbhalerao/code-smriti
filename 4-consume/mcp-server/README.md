# CodeSmriti MCP Server

Model Context Protocol server for AI assistant integration with code search and memory tools. Provides seamless access to your indexed codebase through MCP-compatible clients like Claude Desktop.

## Quick Start

### 1. Start the Server

```bash
cd /home/user/code-smriti/4-consume/mcp-server
docker-compose up -d mcp-server
```

Server will be accessible on:
- **Local**: http://localhost:8080
- **Health check**: http://localhost:8080/health

### 2. Configure Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%/Claude/claude_desktop_config.json` (Windows):

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

### 3. Test MCP Tools

```bash
# List available tools
curl -X POST http://localhost:8080/mcp/tools/list \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN"

# Search code
curl -X POST http://localhost:8080/mcp/tools/call \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -d '{
    "name": "search_code",
    "arguments": {
      "query": "authentication middleware",
      "language": "python",
      "limit": 5
    }
  }'
```

## Architecture

### MCP Protocol Implementation

**MCP Endpoints:**
- `POST /mcp/initialize` - Initialize MCP connection
- `POST /mcp/tools/list` - List available tools
- `POST /mcp/tools/call` - Execute a tool
- `POST /mcp/resources/list` - List available resources
- `POST /mcp/resources/read` - Read a resource

**REST API Endpoints:**
- `GET /health` - Health check
- `GET /api/status` - System statistics
- `POST /api/ingest/trigger` - Trigger re-indexing
- `POST /api/notes` - Create memory note

### Available Tools

#### 1. search_code
Vector similarity search across indexed repositories with filters.

```json
{
  "query": "JWT authentication",
  "repo": "owner/repo",
  "language": "python",
  "hashtags": ["security"],
  "limit": 10
}
```

#### 2. get_code_context
Retrieve specific code file with surrounding context.

```json
{
  "repo": "owner/repo",
  "file_path": "src/auth/middleware.py",
  "function_name": "verify_token"
}
```

#### 3. find_similar
Find code similar to a given snippet.

```json
{
  "code_snippet": "def retry_with_backoff(func, max_retries=3):\n    ...",
  "language": "python",
  "limit": 5
}
```

#### 4. list_repos
List all indexed repositories with statistics.

```json
{}
```

#### 5. add_note
Add a memory note with hashtags for organization.

```json
{
  "content": "Remember to use JWT with 24hr expiration",
  "hashtags": ["security", "authentication"],
  "project": "api-server"
}
```

#### 6. query_by_hashtag
Retrieve content tagged with specific hashtags.

```json
{
  "hashtags": ["security"],
  "content_type": "all"
}
```

### Resources

**Direct File Access:**
Access code files using `repo://` URI scheme:

```
repo://owner/repo/path/to/file.py
```

## Configuration

Create `.env` file in the mcp-server directory:

```bash
# Couchbase
COUCHBASE_HOST=localhost
COUCHBASE_PORT=8091
COUCHBASE_USERNAME=Administrator
COUCHBASE_PASSWORD=password123
COUCHBASE_BUCKET=code_memory

# Ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=codellama:13b

# Embedding Model
EMBEDDING_MODEL=nomic-ai/nomic-embed-text-v1.5
EMBEDDING_MODEL_REVISION=7710840340a098cfb869c4f65e87cf2b1b70caca
EMBEDDING_DIMENSIONS=768

# JWT Authentication
JWT_SECRET=your-secret-key-here-change-in-production
JWT_ALGORITHM=HS256
JWT_EXPIRATION_HOURS=24

# Vector Search
VECTOR_SEARCH_TOP_K=10
SIMILARITY_THRESHOLD=0.7

# Rate Limiting
RATE_LIMIT_PER_MINUTE=60

# Logging
LOG_LEVEL=INFO
```

## Usage with Claude Desktop

Once configured, you can use natural language queries in Claude Desktop:

```
"Show me how we implemented JWT authentication"
"Find similar code to this retry mechanism: [paste code]"
"What repositories do we have indexed?"
"Add a note: Remember to update dependencies quarterly #maintenance"
"Show me everything tagged with #security"
```

Claude will automatically use the appropriate MCP tools to search your codebase and provide context-aware answers.

## Development

### Run Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run server
python server.py
```

Server runs on port 8080 with auto-reload enabled.

### Project Structure

```
mcp-server/
├── server.py              # Main FastAPI application
├── config.py              # Configuration management
├── tools/
│   ├── search.py         # Vector search tools
│   ├── retrieval.py      # Direct code retrieval
│   └── notes.py          # Memory notes management
├── resources/
│   └── code_resources.py # MCP resources (repo:// URIs)
├── auth/
│   └── jwt_middleware.py # JWT authentication
├── storage/
│   └── couchbase_client.py # Couchbase integration
└── Dockerfile
```

## Integration Examples

### Python Client

```python
import requests

# Call MCP tool
response = requests.post(
    "http://localhost:8080/mcp/tools/call",
    headers={"Authorization": f"Bearer {token}"},
    json={
        "name": "search_code",
        "arguments": {
            "query": "error handling patterns",
            "language": "python",
            "limit": 10
        }
    }
)

results = response.json()
```

### JavaScript/Node.js Client

```javascript
const response = await fetch('http://localhost:8080/mcp/tools/call', {
  method: 'POST',
  headers: {
    'Authorization': `Bearer ${token}`,
    'Content-Type': 'application/json'
  },
  body: JSON.stringify({
    name: 'search_code',
    arguments: {
      query: 'async database operations',
      language: 'javascript',
      limit: 5
    }
  })
});

const results = await response.json();
```

## Troubleshooting

### Server won't start

```bash
# Check if port 8080 is in use
lsof -ti:8080 | xargs kill -9

# Check Couchbase connection
curl http://localhost:8091

# Check logs
docker logs codesmriti_mcp
```

### MCP tools not showing in Claude Desktop

1. Verify Claude Desktop config file location and syntax
2. Restart Claude Desktop completely
3. Check MCP server is running: `curl http://localhost:8080/health`
4. Check Docker container is accessible: `docker ps | grep codesmriti_mcp`

### No search results

```bash
# Verify data is indexed
curl http://localhost:8080/api/status

# Check Couchbase bucket exists
# Open http://localhost:8091 in browser

# Verify embeddings model is available
curl http://localhost:11434/api/tags
```

### Authentication errors

```bash
# Generate new JWT token (if using REST API directly)
# MCP protocol handles authentication automatically

# Verify JWT_SECRET is set in .env
grep JWT_SECRET .env
```

## Performance Tuning

**Vector Search:**
- Adjust `VECTOR_SEARCH_TOP_K` to control result count
- Set `SIMILARITY_THRESHOLD` to filter low-quality matches
- Default: top 10 results with 0.7 threshold

**Rate Limiting:**
- Configure `RATE_LIMIT_PER_MINUTE` for production use
- Default: 60 requests/minute per client

**Timeouts:**
- Long-running queries have 600s timeout
- Adjust in nginx configuration if needed

## Security Notes

**Production Checklist:**
- Change default `JWT_SECRET` to a strong random value
- Use HTTPS (see api-gateway/README.md for SSL setup)
- Enable rate limiting in nginx
- Restrict CORS origins (currently set to `*` for development)
- Review and restrict `/couchbase/` endpoint access
- Use environment variables, never commit secrets to git

## Next Steps

1. **Index your code**: Run ingestion pipeline (see `2-initialize/`)
2. **Configure Claude Desktop**: Add MCP server configuration
3. **Try queries**: Test with real questions about your codebase
4. **Add notes**: Build your memory system with hashtags
5. **Deploy**: Use api-gateway for production SSL/reverse proxy
