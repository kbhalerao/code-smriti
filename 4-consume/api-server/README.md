# CodeSmriti API Server

RAG-enriched LLM chat API with two-phase architecture for code search and assistance.

## Quick Start

### 1. Start the Server

```bash
cd /Users/kaustubh/Documents/code/code-smriti/4-consume/api-server
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
  -d '{"query": "How does authentication work in the API?"}'
```

### 4. Chat without Auth (Testing Only)

```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "Show me examples of FastAPI routes"}'
```

## Architecture

### Two-Phase RAG System

**Phase 1: Intent Classification**
- Uses Ollama (qwen2.5-coder:7b) to analyze if query can be answered from code
- Returns structured decision with confidence score
- Acts as guardrail to filter off-topic questions

**Phase 2: RAG Research**
- Searches Couchbase for relevant code chunks
- Generates contextual answer using Ollama with retrieved code
- Returns answer with source attribution

### Endpoints

| Endpoint | Auth | Description |
|----------|------|-------------|
| `GET /health` | No | Health check |
| `POST /api/auth/login` | No | Get JWT token |
| `POST /api/chat/` | Yes | RAG chat (authenticated) |
| `POST /api/chat/test` | No | RAG chat (test only - no auth) |
| `GET /api/chat/health` | No | Chat service health |
| `GET /docs` | No | Interactive API docs |

## Configuration

Edit `.env` file:

```bash
# Ollama
OLLAMA_HOST=http://localhost:11434

# Couchbase
COUCHBASE_HOST=localhost
COUCHBASE_PASSWORD=password123

# JWT Secret (change in production!)
JWT_SECRET=your-secret-key-here-change-in-production
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

```json
{
  "answer": "Generated answer with code context...",
  "intent": {
    "can_answer": true,
    "confidence": 0.95,
    "reasoning": "Query is about code implementation",
    "max_results": 5
  },
  "sources": [
    {
      "repo": "owner/repo",
      "file": "path/to/file.py",
      "language": "python",
      "content": "code snippet..."
    }
  ],
  "metadata": {
    "num_sources": 3,
    "tenant_id": "code_kosha"
  }
}
```

## Next Steps

1. **Add proper authentication**: Currently accepts any username/password for testing
2. **Upgrade to Python 3.10+**: Enable Pydantic AI for better tool calling rigor
3. **Implement vector search**: Add semantic search using embeddings
4. **Rate limiting**: Add rate limits for production use
5. **Multi-tenancy**: Implement proper user buckets and permissions

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
ollama pull qwen2.5-coder:7b
```

### No code found in database
This is expected if ingestion hasn't run yet. The system will still work but return "No relevant code found" messages.
