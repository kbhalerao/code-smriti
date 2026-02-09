# CodeSmriti Chat API - Usage Guide

## Overview

The CodeSmriti Chat API provides RAG-powered (Retrieval-Augmented Generation) code search and question-answering capabilities. It combines semantic vector search with LLM-generated narratives and automatic source citations.

## Table of Contents

- [Endpoints](#endpoints)
- [Authentication](#authentication)
- [Chat Endpoint](#chat-endpoint)
- [Search Endpoint](#search-endpoint)
- [Citation System](#citation-system)
- [Examples](#examples)
- [Error Handling](#error-handling)
- [Configuration](#configuration)

---

## Endpoints

### Base URL
```
http://localhost:8000/api
```

### Available Endpoints

| Endpoint | Method | Auth Required | Description |
|----------|--------|---------------|-------------|
| `/api/chat/` | POST | Yes | RAG-powered chat with authentication |
| `/api/chat/test` | POST | No | Test endpoint without auth (development only) |
| `/api/chat/search` | POST | No | Raw search without LLM processing |
| `/api/chat/health` | GET | No | Health check for chat service |

---

## Authentication

The production chat endpoint (`/api/chat/`) requires JWT authentication:

```bash
curl -X POST http://localhost:8000/api/chat/ \
  -H "Authorization: Bearer YOUR_JWT_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "your question here"}'
```

For development/testing, use `/api/chat/test` which doesn't require authentication.

---

## Chat Endpoint

### Request Format

**Endpoint:** `POST /api/chat/test`

**Request Body:**
```json
{
  "query": "your question about code",
  "stream": false,
  "conversation_history": [
    {
      "role": "user",
      "content": "previous question"
    },
    {
      "role": "assistant",
      "content": "previous answer"
    }
  ]
}
```

**Parameters:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | Yes | - | Your question or code search query |
| `stream` | boolean | No | false | Enable streaming response |
| `conversation_history` | array | No | [] | Previous conversation context (last 2 exchanges) |

### Response Format

**Success Response (200):**
```json
{
  "answer": "Generated answer with inline citations and code examples...\n\n**Sources:**\n- [repo_id/file_path]\n- [repo_id/file_path]\n",
  "metadata": {
    "tenant_id": "code_kosha",
    "conversation_length": 2
  }
}
```

### How It Works

1. **Tool Calling**: The LLM automatically calls the `search_code` tool to find relevant code
2. **Search Execution**: Hybrid vector + text search across your codebase
3. **Answer Generation**: LLM generates a narrative answer based on search results
4. **Automatic Citations**: System extracts and appends source files used

---

## Search Endpoint

Use the raw search endpoint to test search quality before LLM processing.

### Request Format

**Endpoint:** `POST /api/chat/search`

```json
{
  "query": "semantic search query",
  "text_query": "exact keyword search",
  "limit": 5,
  "repo_filter": "owner/repo",
  "file_path_pattern": "*.py",
  "doc_type": "code_chunk"
}
```

**Parameters:**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `query` | string | No | null | Semantic vector search query |
| `text_query` | string | No | null | BM25 keyword search query |
| `limit` | integer | No | 5 | Number of results (1-20) |
| `repo_filter` | string | No | null | Filter by repository (e.g., "kbhalerao/labcore") |
| `file_path_pattern` | string | No | null | SQL LIKE pattern (e.g., "*.py", "test_*") |
| `doc_type` | string | No | "code_chunk" | Type: "code_chunk", "document", or "commit" |

**Search Modes:**

1. **Vector-only**: Provide only `query` for semantic search
2. **Text-only**: Provide only `text_query` for keyword search
3. **Hybrid**: Provide both for combined semantic + keyword matching

### Response Format

```json
{
  "query": "semantic query",
  "text_query": "keyword query",
  "search_mode": "hybrid",
  "filters": {
    "repo": "kbhalerao/labcore",
    "file_path": "*.py",
    "doc_type": "code_chunk"
  },
  "results": [
    {
      "content": "code content...",
      "repo_id": "kbhalerao/labcore",
      "file_path": "common/consumer_decorators.py",
      "language": "python",
      "score": 0.802,
      "start_line": null,
      "end_line": null,
      "type": "code_chunk"
    }
  ],
  "count": 5
}
```

---

## Citation System

### Automatic Source Extraction

The chat endpoint **automatically extracts and cites all sources** used to generate the answer. You don't need to rely on the LLM to format citations.

**How it works:**
1. System tracks all `search_code` tool calls during execution
2. Extracts `repo_id` and `file_path` from each search result
3. Appends a "Sources:" section to the answer
4. Format: `[repo_id/file_path]`

**Example output:**
```markdown
The `requeue_task` decorator provides retry logic for async functions...

[Code example here]

**Sources:**
- [your-org/core-library/common/consumer_decorators.py]
- [your-org/utils/workers/consumer_decorators.py]
- [your-org/platform-backend/network_helpers/consumer_helpers.py]
```

### Benefits

- ✅ 100% reliable (programmatic extraction, not LLM-generated)
- ✅ No hallucinated citations
- ✅ Works with any LLM model
- ✅ Consistent format
- ✅ Easy to parse programmatically

---

## Examples

### Example 1: Simple Code Question

**Request:**
```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Show me a requeue task decorator with retry logic for async functions"
  }'
```

**Response:**
```json
{
  "answer": "Based on the search results, the `requeue_task` decorator is a tool for async functions with retry logic...\n\n```python\n@requeue_task(max_retries=5, delay=10)\nasync def my_async_function():\n    # Your code here\n```\n\n**Sources:**\n- [your-org/core-library/common/consumer_decorators.py]\n- [your-org/utils/workers/consumer_decorators.py]\n",
  "metadata": {
    "tenant_id": "code_kosha",
    "conversation_length": 2
  }
}
```

### Example 2: Filtered Search

**Request:**
```bash
curl -X POST http://localhost:8000/api/chat/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "authentication middleware",
    "repo_filter": "kbhalerao/labcore",
    "file_path_pattern": "*.py",
    "limit": 3
  }'
```

**Response:**
```json
{
  "search_mode": "vector",
  "results": [
    {
      "content": "class JWTAuthMiddleware...",
      "repo_id": "kbhalerao/labcore",
      "file_path": "auth/middleware.py",
      "score": 0.856
    }
  ],
  "count": 3
}
```

### Example 3: Hybrid Search

**Request:**
```bash
curl -X POST http://localhost:8000/api/chat/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "background task processing",
    "text_query": "Celery async_task",
    "doc_type": "code_chunk",
    "limit": 10
  }'
```

This combines semantic understanding (background task processing) with exact keyword matching (Celery, async_task).

### Example 4: Conversation with History

**Request:**
```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How do I use it?",
    "conversation_history": [
      {
        "role": "user",
        "content": "Show me the requeue_task decorator"
      },
      {
        "role": "assistant",
        "content": "The requeue_task decorator is defined in..."
      }
    ]
  }'
```

The API maintains context from the last 2 conversation exchanges (4 messages).

### Example 5: Python Script

```python
import httpx
import asyncio

async def ask_code_question(query: str):
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            'http://localhost:8000/api/chat/test',
            json={"query": query, "stream": False}
        )

        if response.status_code == 200:
            data = response.json()
            print(data['answer'])
            return data
        else:
            print(f"Error: {response.status_code}")
            return None

# Usage
asyncio.run(ask_code_question(
    "How do I implement access control in Django models?"
))
```

---

## Error Handling

### Common Errors

**400 Bad Request:**
```json
{
  "detail": "Query is required and must be between 1-2000 characters"
}
```

**500 Internal Server Error:**
```json
{
  "detail": "Failed to process chat request: [error message]"
}
```

**Tool Execution Errors:**

If search fails, the error is returned in the tool response:
```json
{
  "error": "Vector search failed: [reason]"
}
```

The LLM will see this error and may retry or inform the user.

### Best Practices

1. **Timeout**: Set appropriate timeouts (60-120 seconds for complex queries)
2. **Query Length**: Keep queries under 2000 characters
3. **Conversation History**: Limit to last 2 exchanges (system enforces this)
4. **Retry Logic**: Implement exponential backoff for network errors

---

## Configuration

### Environment Variables

Set these in your `.env` file:

```bash
# LLM Model (must support tool calling)
LLM_MODEL_NAME=llama3.1:latest

# Embedding Model
EMBEDDING_MODEL_NAME=nomic-ai/nomic-embed-text-v1.5

# Ollama Host
OLLAMA_HOST=http://localhost:11434

# Couchbase Connection
COUCHBASE_HOST=localhost
COUCHBASE_PORT=8091
COUCHBASE_USER=Administrator
COUCHBASE_PASSWORD=your-couchbase-password
COUCHBASE_BUCKET_CODE=code_kosha
```

### Supported LLM Models

Models that support tool/function calling:
- ✅ `llama3.1:latest` - **Recommended** (best instruction-following)
- ✅ `llama3:latest` - Good performance
- ⚠️ `codellama:13b` - Code-focused but poor citation following
- ❌ `deepseek-coder:6.7b` - Doesn't support tool calling

### Logging

Logs are configured in `app/main.py`:

- **File**: `logs/api-server.log` (all levels, DEBUG+)
- **Console**: stderr only, WARNING+ (errors and warnings only)

View logs:
```bash
# Recent logs
tail -f logs/api-server.log

# Errors only
tail -f logs/api-server.log | grep ERROR

# Search specific query
grep "search_code" logs/api-server.log
```

---

## Advanced Features

### Search Tool Parameters

The `search_code` tool supports these parameters:

```json
{
  "query": "semantic search query",
  "text_query": "keyword search",
  "limit": 5,
  "repo_filter": "owner/repo",
  "file_path_pattern": "*.py",
  "doc_type": "code_chunk"
}
```

The LLM decides which parameters to use based on your question.

**Example LLM tool calls:**

- "Find authentication code" → `{"query": "authentication", "limit": 5}`
- "Show me all Python test files" → `{"file_path_pattern": "test_*.py", "limit": 10}`
- "Find JWT code in labcore" → `{"query": "JWT", "repo_filter": "kbhalerao/labcore"}`

### Document Types

- **code_chunk**: Source code files (default)
- **document**: README, documentation, markdown files
- **commit**: Git commit messages and metadata

**Example:**
```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How is the authentication flow documented?",
    "doc_type": "document"
  }'
```

---

## Performance Tips

1. **Use specific queries**: "authentication middleware in Django" vs "auth"
2. **Limit results**: Start with `limit: 5`, increase only if needed
3. **Use filters**: Narrow by repo or file pattern when possible
4. **Raw search first**: Test with `/api/chat/search` before using chat
5. **Monitor logs**: Check `logs/api-server.log` for slow queries

---

## Troubleshooting

### No Results Returned

1. Check search directly: Use `/api/chat/search` endpoint
2. Verify data is indexed: Check Couchbase FTS index `code_vector_index`
3. Try different query: Use synonyms or related terms
4. Check filters: Remove `repo_filter` or `file_path_pattern` to broaden search

### Poor Quality Answers

1. Verify search results are relevant (use `/api/chat/search`)
2. Try different LLM model (llama3.1 recommended)
3. Rephrase query to be more specific
4. Check if sources are correct (automatic citation system is reliable)

### Tool Calling Issues

If the LLM returns tool calls as text instead of executing them:

1. Ensure model supports tool calling (llama3.1, llama3)
2. Check Ollama is running: `curl http://localhost:11434/api/tags`
3. Review logs for Ollama API errors
4. Try different model

---

## API Client Libraries

### Python (httpx)

```python
import httpx
import asyncio

async def chat(query: str, **kwargs):
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(
            'http://localhost:8000/api/chat/test',
            json={"query": query, **kwargs}
        )
        return response.json()
```

### JavaScript (fetch)

```javascript
async function chat(query, options = {}) {
  const response = await fetch('http://localhost:8000/api/chat/test', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, ...options })
  });
  return response.json();
}
```

### cURL

```bash
chat() {
  curl -X POST http://localhost:8000/api/chat/test \
    -H "Content-Type: application/json" \
    -d "{\"query\": \"$1\"}"
}

# Usage
chat "How do I implement retry logic?"
```

---

## Support

For issues, questions, or feature requests:
- Check logs: `logs/api-server.log`
- Review search quality: `/api/chat/search` endpoint
- Test different models: Update `LLM_MODEL_NAME` in `.env`
- Report bugs: [GitHub Issues]

---

## Summary

The CodeSmriti Chat API provides:
- ✅ RAG-powered code search with LLM narratives
- ✅ Automatic source citations (100% reliable)
- ✅ Hybrid vector + text search
- ✅ Conversation history support
- ✅ Flexible filtering (repo, file pattern, doc type)
- ✅ Multiple search modes (vector, text, hybrid)
- ✅ Clean logging (errors to console, all to file)

**Quick Start:**
```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "your code question here"}'
```
