# CodeSmriti API Reference

*Smriti (स्मृति): Sanskrit for "memory, remembrance"*

Complete API reference for the CodeSmriti MCP Server and REST endpoints.

## Base URL

```
http://localhost:8080  # Direct MCP server
http://localhost       # Via Nginx gateway (recommended)
```

## Authentication

All API requests require JWT authentication via Bearer token.

### Obtaining an API Key

```bash
# Generate an API key
./scripts/generate-api-key.py

# Use in requests
curl -H "Authorization: Bearer YOUR_API_KEY" \
  http://localhost/api/status
```

### Token Format

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

## MCP Protocol Endpoints

### Initialize Connection

Establish an MCP connection and get server capabilities.

**Request:**
```http
POST /mcp/initialize
Content-Type: application/json
```

**Response:**
```json
{
  "protocolVersion": "2024-11-05",
  "capabilities": {
    "tools": {},
    "resources": {
      "subscribe": true,
      "listChanged": true
    },
    "prompts": {}
  },
  "serverInfo": {
    "name": "codesmriti",
    "version": "1.0.0"
  }
}
```

### List Tools

Get all available MCP tools.

**Request:**
```http
POST /mcp/tools/list
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json
```

**Response:**
```json
{
  "tools": [
    {
      "name": "search_code",
      "description": "Search for code across indexed repositories",
      "inputSchema": {
        "type": "object",
        "properties": {
          "query": {
            "type": "string",
            "description": "Search query"
          },
          "repo": {
            "type": "string",
            "description": "Filter by repository"
          },
          "language": {
            "type": "string",
            "description": "Filter by language"
          },
          "limit": {
            "type": "integer",
            "default": 10
          }
        },
        "required": ["query"]
      }
    }
    // ... other tools
  ]
}
```

### Call Tool

Execute an MCP tool.

**Request:**
```http
POST /mcp/tools/call
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "name": "search_code",
  "arguments": {
    "query": "authentication middleware",
    "language": "python",
    "limit": 5
  }
}
```

**Response:**
```json
{
  "content": [
    {
      "type": "text",
      "text": "{\"query\": \"authentication middleware\", \"total_results\": 5, \"results\": [...]}"
    }
  ]
}
```

### List Resources

Get available MCP resources.

**Request:**
```http
POST /mcp/resources/list
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json
```

**Response:**
```json
{
  "resources": [
    {
      "uri": "repo://{owner}/{repo}/{file_path}",
      "name": "Code File",
      "description": "Direct access to code files",
      "mimeType": "text/plain"
    }
  ]
}
```

### Read Resource

Read a specific resource.

**Request:**
```http
POST /mcp/resources/read
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "uri": "repo://myorg/api-server/src/auth/jwt.py"
}
```

**Response:**
```json
{
  "contents": [
    {
      "uri": "repo://myorg/api-server/src/auth/jwt.py",
      "mimeType": "text/plain",
      "text": "import jwt\nfrom datetime import datetime...\n"
    }
  ]
}
```

## REST API Endpoints

### Health Check

Check if the server is running.

**Request:**
```http
GET /health
```

**Response:**
```json
{
  "status": "healthy",
  "service": "codesmriti-mcp-server",
  "timestamp": "2025-01-15T12:00:00.000Z"
}
```

### System Status

Get system statistics and status.

**Request:**
```http
GET /api/status
Authorization: Bearer YOUR_API_KEY
```

**Response:**
```json
{
  "total_repositories": 100,
  "total_code_chunks": 125000,
  "total_notes": 350,
  "total_documents": 5000,
  "indexed_languages": ["python", "javascript", "typescript"],
  "total_storage_mb": 2048.5,
  "last_index_time": "2025-01-15T10:30:00Z",
  "status": "healthy"
}
```

### Trigger Ingestion

Manually trigger repository re-indexing.

**Request:**
```http
POST /api/ingest/trigger
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "repo": "myorg/api-server"  # Optional, omit to index all repos
}
```

**Response:**
```json
{
  "status": "triggered",
  "repo": "myorg/api-server",
  "timestamp": "2025-01-15T12:00:00Z"
}
```

### Create Note

Add a memory note with hashtags.

**Request:**
```http
POST /api/notes
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json

{
  "content": "We use JWT for authentication because it's stateless and scales well. See implementation in api-server/src/auth/jwt.py",
  "hashtags": ["authentication", "architecture-decision", "jwt"],
  "project": "api-server"
}
```

**Response:**
```json
{
  "status": "created",
  "result": "{\"note_id\": \"123e4567-e89b-12d3-a456-426614174000\", ...}"
}
```

## Tool Specifications

### search_code

Search for code across all indexed repositories using vector similarity and filters.

**Parameters:**
- `query` (string, required): Natural language query or code snippet
- `repo` (string, optional): Filter by repository (owner/repo format)
- `language` (string, optional): Filter by language (python, javascript, typescript)
- `hashtags` (array, optional): Filter by hashtags
- `limit` (integer, optional, default: 10): Maximum results

**Example:**
```json
{
  "name": "search_code",
  "arguments": {
    "query": "How do we handle rate limiting?",
    "repo": "myorg/api-server",
    "language": "python",
    "limit": 5
  }
}
```

**Response:**
```json
{
  "query": "How do we handle rate limiting?",
  "total_results": 3,
  "results": [
    {
      "repo_id": "myorg/api-server",
      "file_path": "src/middleware/rate_limit.py",
      "chunk_type": "function",
      "code_text": "@app.middleware(\"http\")\nasync def rate_limit_middleware(request: Request, call_next):\n    ...",
      "metadata": {
        "language": "python",
        "function_name": "rate_limit_middleware",
        "start_line": 15,
        "end_line": 35,
        "commit_hash": "abc123",
        "author": "dev@example.com"
      },
      "similarity_score": 0.92
    }
  ]
}
```

### get_code_context

Retrieve a specific code file with full context and metadata.

**Parameters:**
- `repo` (string, required): Repository in owner/repo format
- `file_path` (string, required): Path to file within repository
- `function_name` (string, optional): Specific function or class name

**Example:**
```json
{
  "name": "get_code_context",
  "arguments": {
    "repo": "myorg/api-server",
    "file_path": "src/auth/jwt.py",
    "function_name": "verify_token"
  }
}
```

### find_similar

Find code similar to a given snippet using vector similarity.

**Parameters:**
- `code_snippet` (string, required): Code to find similar implementations
- `language` (string, optional): Filter by programming language
- `limit` (integer, optional, default: 5): Maximum results

**Example:**
```json
{
  "name": "find_similar",
  "arguments": {
    "code_snippet": "async def fetch_user(user_id: int):\n    user = await db.users.find_one({\"_id\": user_id})\n    return user",
    "language": "python",
    "limit": 5
  }
}
```

### add_note

Add a memory note with hashtags for organization.

**Parameters:**
- `content` (string, required): Note content in markdown format
- `hashtags` (array, optional): Tags for categorization
- `project` (string, optional): Associated project name

**Example:**
```json
{
  "name": "add_note",
  "arguments": {
    "content": "## Authentication Decision\n\nWe chose JWT over session-based auth because:\n1. Stateless scaling\n2. Microservices compatibility\n3. Mobile app support",
    "hashtags": ["authentication", "architecture", "jwt"],
    "project": "api-server"
  }
}
```

### query_by_hashtag

Retrieve all content (code and notes) tagged with specific hashtags.

**Parameters:**
- `hashtags` (array, required): List of hashtags to search
- `content_type` (string, optional, default: "all"): Filter by type (code, note, all)

**Example:**
```json
{
  "name": "query_by_hashtag",
  "arguments": {
    "hashtags": ["authentication", "best-practices"],
    "content_type": "all"
  }
}
```

### list_repos

List all indexed repositories with statistics.

**Parameters:** None

**Example:**
```json
{
  "name": "list_repos",
  "arguments": {}
}
```

**Response:**
```json
{
  "total_repos": 100,
  "repositories": [
    {
      "repo_id": "myorg/api-server",
      "total_chunks": 2500,
      "languages": ["python", "yaml"],
      "last_indexed": "2025-01-15T10:00:00Z"
    }
  ]
}
```

## Error Responses

All errors follow this format:

```json
{
  "error": "Error message describing what went wrong"
}
```

**Common HTTP Status Codes:**
- `200 OK`: Success
- `400 Bad Request`: Invalid parameters
- `401 Unauthorized`: Missing or invalid API key
- `403 Forbidden`: Insufficient permissions
- `404 Not Found`: Resource not found
- `500 Internal Server Error`: Server error

## Rate Limiting

Default rate limits:
- 60 requests per minute per API key
- Configurable via `RATE_LIMIT_PER_MINUTE` environment variable

When rate limited, you'll receive:
```json
{
  "error": "Rate limit exceeded. Try again in 30 seconds."
}
```

## Pagination

For endpoints returning large result sets, use limit/offset:

```json
{
  "query": "authentication",
  "limit": 20,
  "offset": 0
}
```

## Best Practices

1. **Use specific queries**: More specific queries return better results
2. **Filter by repo/language**: Reduces search space and improves speed
3. **Leverage hashtags**: Tag important notes for easy retrieval
4. **Cache responses**: API responses can be cached for 5-10 minutes
5. **Batch operations**: Group related operations in agentic loops

## Examples

### Complete Workflow: Find Best Practices

```bash
# 1. Search for authentication implementations
curl -X POST http://localhost/mcp/tools/call \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "search_code",
    "arguments": {
      "query": "authentication implementation",
      "limit": 10
    }
  }'

# 2. Get notes tagged with best practices
curl -X POST http://localhost/mcp/tools/call \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "query_by_hashtag",
    "arguments": {
      "hashtags": ["authentication", "best-practices"]
    }
  }'

# 3. Add a new note summarizing findings
curl -X POST http://localhost/api/notes \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "content": "Based on our codebase, authentication best practices include...",
    "hashtags": ["authentication", "best-practices"],
    "project": "platform"
  }'
```

## Support

For API issues or questions:
1. Check server logs: `docker-compose logs -f mcp-server`
2. Verify authentication: Test with `/health` endpoint
3. Review this documentation
4. Contact the development team
