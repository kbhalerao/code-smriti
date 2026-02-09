# CodeSmriti API Server

RAG-enriched LLM chat API with PydanticAI tool-calling architecture for code search and assistance.

## Quick Start

### 1. Start the Server

```bash
cd /path/to/code-smriti/4-consume/api-server
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Server will be accessible on:
- **Local**: http://localhost:8000
- **LAN**: http://<your-ip>:8000

### 2. Get JWT Token

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "demo", "password": "demo"}'
```

Returns:
```json
{
  "access_token": "eyJhbGc...",
  "token_type": "bearer"
}
```

### 3. Chat with RAG (Authenticated)

```bash
TOKEN="<your-token-here>"

curl -X POST http://localhost:8000/api/chat/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "How does authentication work in the API?", "stream": false}'
```

### 4. Chat without Auth (Testing Only)

```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "Show me examples of FastAPI routes", "stream": false}'
```

## Architecture

### PydanticAI Tool-Calling RAG System

**Intent Validation (Heuristic)**
- Lightweight keyword-based filtering to catch obvious off-topic queries
- Fast pre-filter before expensive LLM operations

**PydanticAI Agent (deepseek-coder:6.7b)**
- Tool-calling architecture for flexible code search
- Agent decides when and how to search based on query context
- Supports conversation history for follow-up questions
- Available tools:
  - `search_code()` - Vector search with Couchbase FTS + kNN
  - `list_available_repos()` - Get list of indexed repositories

**Vector Search**
- Couchbase FTS with kNN similarity
- sentence-transformers (nomic-ai/nomic-embed-text-v1.5, 768 dims)
- Matches ingestion embedding model for compatibility

**Response Generation**
- Streaming and non-streaming modes
- High-quality markdown narratives with code blocks
- File references and citations

### Endpoints

| Endpoint | Auth | Streaming | Description |
|----------|------|-----------|-------------|
| `GET /health` | No | - | Health check |
| `POST /api/auth/login` | No | - | Get JWT token |
| `POST /api/chat/` | Yes | Both | RAG chat (set `stream: true/false`) |
| `POST /api/chat/test` | No | Both | RAG chat (test only - no auth) |
| `GET /api/chat/health` | No | - | Chat service health |
| `GET /docs` | No | - | Interactive API docs |

## Configuration

Edit `.env` file:

```bash
# Ollama
OLLAMA_HOST=http://localhost:11434

# Couchbase
COUCHBASE_HOST=localhost
COUCHBASE_USERNAME=Administrator
COUCHBASE_PASSWORD=your-couchbase-password
COUCHBASE_BUCKET_CODE=code_kosha

# JWT Secret (change in production!)
JWT_SECRET=your-secret-key-here-change-in-production

# AES encryption for GitHub PATs
AES_ENCRYPTION_KEY=your-aes-encryption-key-here
```

## Access from LAN

### Find your IP:
```bash
ipconfig getifaddr en0  # macOS WiFi
# or
hostname -I  # Linux
```

### Access from other machines:
```bash
curl http://<your-ip>:8000/health
```

## VSCode/Claude Code Integration

### Using curl from VSCode terminal:

```bash
# 1. Get token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "demo", "password": "demo"}' | jq -r '.access_token')

# 2. Ask questions
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d "{\"query\": \"How does X work in the codebase?\"}" | jq .
```

### Using httpie (cleaner syntax):

```bash
# Install httpie
brew install httpie  # macOS
pip install httpie   # Any platform

# Get token
http POST :8000/api/auth/login username=demo password=demo

# Chat
http POST :8000/api/chat/test query="Show me FastAPI examples"
```

## Response Format

**Non-streaming response:**
```json
{
  "answer": "# Authentication in the API\n\nThe API uses JWT tokens...",
  "metadata": {
    "tenant_id": "code_kosha",
    "conversation_length": 2
  }
}
```

**Streaming response:**
```
text/plain stream
Chunks of markdown text as they're generated...
```

**Request with conversation history:**
```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Can you give me more details?",
    "stream": false,
    "conversation_history": [
      {"role": "user", "content": "How does auth work?"},
      {"role": "assistant", "content": "We use JWT tokens..."}
    ]
  }'
```

## Current Features

- ✅ PydanticAI tool-calling RAG system
- ✅ Vector search with Couchbase FTS + kNN
- ✅ Conversation history support
- ✅ Streaming and non-streaming responses
- ✅ JWT authentication
- ✅ Multi-tenant support (code_kosha bucket)

## Potential Enhancements

1. **User authentication**: Currently accepts any username/password for testing
2. **Re-ranking**: Add re-ranking layer after vector search for better relevance
3. **Hybrid search**: Combine vector search with keyword search
4. **More tools**: Add tools for file-specific queries, language filters, etc.
5. **Rate limiting**: Add rate limits for production use
6. **Evaluation dashboard**: Visualize performance metrics from evals

## Troubleshooting

### Server won't start
```bash
# Check if port 8000 is in use
lsof -ti:8000 | xargs kill -9

# Check Couchbase connection
curl http://localhost:8091
```

### Ollama not responding
```bash
# Check Ollama is running
ollama list

# Pull the model if missing
ollama pull deepseek-coder:6.7b

# Alternative models that work:
# ollama pull qwen2.5-coder:7b
# ollama pull codellama:13b
```

### No code found in database
This is expected if ingestion hasn't run yet. The system will still work but return "No relevant code found" messages.

### ModuleNotFoundError: No module named '_griffe'

**Problem**: Python 3.9 with pydantic-ai==0.0.14 fails with:
```
ModuleNotFoundError: No module named '_griffe'
from _griffe.enumerations import DocstringSectionKind
```

**Root Cause**: pydantic-ai 0.0.14 tries to import from `_griffe` but griffe 1.14.0 moved these modules to `griffe._internal`.

**Solution**: Patch the import in pydantic-ai's _griffe.py:
```bash
# Edit venv/lib/python3.9/site-packages/pydantic_ai/_griffe.py lines 7-8
# Change:
from _griffe.enumerations import DocstringSectionKind
from _griffe.models import Docstring, Object as GriffeObject

# To:
from griffe._internal.enumerations import DocstringSectionKind
from griffe._internal.models import Docstring, Object as GriffeObject
```

Or reinstall with uv for better compatibility:
```bash
/opt/homebrew/bin/uv pip uninstall --python venv/bin/python3 pydantic-ai pydantic-ai-slim griffe
/opt/homebrew/bin/uv pip install --python venv/bin/python3 pydantic-ai==0.0.14
# Then apply the patch above
```
