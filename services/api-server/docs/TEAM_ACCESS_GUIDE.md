# CodeSmriti API - Team Access Guide

## Quick Start

CodeSmriti is now running on the office LAN and ready for use!

**API Base URL:** `http://192.168.1.29`

---

## Table of Contents

- [Getting Started](#getting-started)
- [Authentication](#authentication)
- [Using the Chat API](#using-the-chat-api)
- [Examples](#examples)
- [Troubleshooting](#troubleshooting)

---

## Getting Started

### Requirements

- Connected to the same network as the server (office LAN)
- Any HTTP client (curl, Postman, Python requests, etc.)
- No special software needed!

### Available Endpoints

| Endpoint | Description | Auth Required |
|----------|-------------|---------------|
| `GET /health` | Health check | No |
| `POST /api/auth/login` | Get authentication token | No |
| `POST /api/chat/` | RAG-powered chat (production) | Yes |
| `POST /api/chat/test` | RAG-powered chat (testing) | No |
| `POST /api/chat/search` | Raw code search | No |

---

## Authentication

For **production endpoints** (`/api/chat/`), you need a JWT token.

### Step 1: Get Your Token

**Request:**
```bash
curl -X POST http://192.168.1.29/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{
    "username": "your_name",
    "password": "any_password"
  }'
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer"
}
```

**Note:** For internal use, any username/password combination works. The system generates a valid token for you.

### Step 2: Use Your Token

Include the token in the `Authorization` header:

```bash
curl -X POST http://192.168.1.29/api/chat/ \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{"query": "your question"}'
```

### Token Expiration

- Tokens are valid for **24 hours**
- After expiration, simply request a new token
- No need to log out or invalidate old tokens

---

## Using the Chat API

### Test Endpoint (No Auth Required)

Perfect for quick tests and development:

```bash
curl -X POST http://192.168.1.29/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Show me examples of Django background task processing"
  }'
```

### Production Endpoint (Auth Required)

For production use with authentication:

```bash
# 1. Get token
TOKEN=$(curl -s -X POST http://192.168.1.29/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "your_name", "password": "demo"}' \
  | jq -r '.access_token')

# 2. Use token
curl -X POST http://192.168.1.29/api/chat/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How do I implement retry logic for async functions?"
  }'
```

---

## Examples

### Example 1: Simple Code Question

**Request:**
```bash
curl -X POST http://192.168.1.29/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Show me authentication middleware examples"
  }'
```

**Response:**
```json
{
  "answer": "Based on the search results, here's how authentication middleware is implemented...\n\n```python\nclass JWTAuthMiddleware:\n    ...\n```\n\n**Sources:**\n- [kbhalerao/labcore/auth/middleware.py]\n- [code-smriti/api-server/app/auth/utils.py]\n",
  "metadata": {
    "tenant_id": "code_kosha",
    "conversation_length": 2
  }
}
```

### Example 2: Python Script

```python
import requests

# API base URL
BASE_URL = "http://192.168.1.29"

def get_token(username="your_name"):
    """Get authentication token"""
    response = requests.post(
        f"{BASE_URL}/api/auth/login",
        json={"username": username, "password": "demo"}
    )
    return response.json()["access_token"]

def ask_question(query, use_auth=False):
    """Ask a code question"""
    if use_auth:
        token = get_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }
        endpoint = f"{BASE_URL}/api/chat/"
    else:
        headers = {"Content-Type": "application/json"}
        endpoint = f"{BASE_URL}/api/chat/test"

    response = requests.post(
        endpoint,
        headers=headers,
        json={"query": query},
        timeout=120
    )

    return response.json()

# Usage
result = ask_question("How do I implement access control in Django models?")
print(result["answer"])
```

### Example 3: JavaScript/Fetch

```javascript
const BASE_URL = 'http://192.168.1.29';

async function getToken(username = 'your_name') {
  const response = await fetch(`${BASE_URL}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password: 'demo' })
  });
  const data = await response.json();
  return data.access_token;
}

async function askQuestion(query, useAuth = false) {
  const headers = { 'Content-Type': 'application/json' };
  let endpoint = `${BASE_URL}/api/chat/test`;

  if (useAuth) {
    const token = await getToken();
    headers['Authorization'] = `Bearer ${token}`;
    endpoint = `${BASE_URL}/api/chat/`;
  }

  const response = await fetch(endpoint, {
    method: 'POST',
    headers,
    body: JSON.stringify({ query })
  });

  return response.json();
}

// Usage
askQuestion('How do I implement retry logic?')
  .then(result => console.log(result.answer));
```

### Example 4: With Conversation History

```bash
curl -X POST http://192.168.1.29/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Can you show me an example?",
    "conversation_history": [
      {
        "role": "user",
        "content": "Tell me about the requeue_task decorator"
      },
      {
        "role": "assistant",
        "content": "The requeue_task decorator provides retry logic..."
      }
    ]
  }'
```

---

## Search Without LLM

If you just want to search code without the LLM narrative:

```bash
curl -X POST http://192.168.1.29/api/chat/search \
  -H "Content-Type: application/json" \
  -d '{
    "query": "authentication middleware",
    "limit": 5,
    "doc_type": "code_chunk"
  }'
```

**Parameters:**
- `query`: Semantic vector search
- `text_query`: Keyword/BM25 search
- `limit`: Number of results (1-20)
- `repo_filter`: Filter by repository (e.g., "kbhalerao/labcore")
- `file_path_pattern`: Filter by file pattern (e.g., "*.py", "test_*")
- `doc_type`: "code_chunk", "document", or "commit"

---

## Troubleshooting

### Connection Issues

**Problem:** Can't connect to the API

**Solutions:**
1. Check you're on the same network
2. Verify the IP: `ping 192.168.1.29`
3. Check server status: `curl http://192.168.1.29/health`

### Timeout Errors

**Problem:** Request times out

**Solutions:**
1. Increase timeout in your HTTP client (use 120s for chat endpoints)
2. Try a simpler query first
3. Use the search endpoint to verify data is indexed

### Authentication Errors

**Problem:** `401 Unauthorized` or `403 Forbidden`

**Solutions:**
1. Verify you're using the correct endpoint:
   - `/api/chat/test` - No auth needed
   - `/api/chat/` - Auth required
2. Check token format: `Authorization: Bearer YOUR_TOKEN`
3. Get a fresh token (they expire after 24 hours)

### Empty Results

**Problem:** Search returns no results

**Solutions:**
1. Try broader search terms
2. Use the `/api/chat/search` endpoint to test search quality
3. Try different doc_type: "code_chunk", "document", or "commit"
4. Remove filters (repo_filter, file_path_pattern)

---

## Best Practices

### 1. Use Test Endpoint for Development

During development, use `/api/chat/test` to avoid auth overhead:

```bash
# Quick testing - no auth
curl -X POST http://192.168.1.29/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "test query"}'
```

### 2. Set Appropriate Timeouts

LLM requests can take 30-120 seconds. Configure your client:

```python
# Python requests
response = requests.post(url, json=data, timeout=120)

# JavaScript fetch (use AbortController)
const controller = new AbortController();
setTimeout(() => controller.abort(), 120000);
fetch(url, { signal: controller.signal });
```

### 3. Save Tokens

Don't request a new token for every request:

```bash
# Good: Save token and reuse
export API_TOKEN=$(curl -s -X POST http://192.168.1.29/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "your_name", "password": "demo"}' \
  | jq -r '.access_token')

# Then reuse
curl -X POST http://192.168.1.29/api/chat/ \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "query 1"}'

curl -X POST http://192.168.1.29/api/chat/ \
  -H "Authorization: Bearer $API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "query 2"}'
```

### 4. Use the Right Search Mode

- **Semantic search**: Use for conceptual queries
  ```json
  {"query": "authentication middleware implementation"}
  ```

- **Keyword search**: Use for exact terms/function names
  ```json
  {"text_query": "JWTAuthMiddleware"}
  ```

- **Hybrid search**: Use both for best results
  ```json
  {
    "query": "authentication middleware",
    "text_query": "JWT"
  }
  ```

---

## Quick Reference Card

### Get Token
```bash
curl -X POST http://192.168.1.29/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "NAME", "password": "demo"}'
```

### Test Endpoint (No Auth)
```bash
curl -X POST http://192.168.1.29/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "YOUR_QUESTION"}'
```

### Production Endpoint (With Auth)
```bash
curl -X POST http://192.168.1.29/api/chat/ \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "YOUR_QUESTION"}'
```

### Search Only
```bash
curl -X POST http://192.168.1.29/api/chat/search \
  -H "Content-Type: application/json" \
  -d '{"query": "search_term", "limit": 5}'
```

### Health Check
```bash
curl http://192.168.1.29/health
```

---

## API Limits and Performance

- **Concurrent requests**: Up to 100 (nginx connection limit)
- **Workers**: 2 uvicorn workers for load balancing
- **Timeout**: 120 seconds for chat endpoints
- **Max query length**: 2000 characters
- **Max search results**: 20 per query

---

## Support

If you encounter issues:

1. **Check health**: `curl http://192.168.1.29/health`
2. **View logs**: Ask admin to check Docker logs
3. **Test search directly**: Use `/api/chat/search` endpoint
4. **Try simpler query**: Start with a basic question

For detailed API documentation, see: `docs/API_USAGE.md`

---

## Summary

**Server URL:** `http://192.168.1.29`

**Simplest Usage (No Auth):**
```bash
curl -X POST http://192.168.1.29/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "your code question here"}'
```

**With Authentication:**
```bash
# 1. Get token (any username/password works)
TOKEN=$(curl -s -X POST http://192.168.1.29/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "your_name", "password": "demo"}' | jq -r '.access_token')

# 2. Use token
curl -X POST http://192.168.1.29/api/chat/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "your question"}'
```

Happy coding! 🚀
