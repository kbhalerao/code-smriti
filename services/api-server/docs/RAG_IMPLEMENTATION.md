# CodeSmriti RAG API Implementation

Production-quality RAG (Retrieval-Augmented Generation) API for code search and assistance.

## Architecture

### Tool-Calling RAG with PydanticAI

We use a **tool-calling architecture** instead of a rigid two-stage pipeline. This gives the LLM flexibility to:
- Decide when to search
- Perform multiple searches with different parameters
- Call tools strategically based on the query
- Generate responses iteratively

### Key Components

```
┌─────────────────────────────────────────────────────────────┐
│                    User Query + History                      │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│              Intent Classification & Query Routing           │
│  Classify query intent to select search level:              │
│  - CONCEPTUAL → "doc" level (guidelines, design docs)       │
│  - IMPLEMENTATION → "file" level (default)                  │
│  - SPECIFIC → "symbol" level (find exact function/class)    │
│  - ARCHITECTURAL → "module" level (folder structure)        │
│  - OVERVIEW → "repo" level (high-level understanding)       │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│           PydanticAI Agent (Ollama: deepseek-coder)        │
│  System Prompt: Code research assistant with intent routing │
│  Tools:                                                      │
│    - search_code(query, level, limit, repo_filter, preview)│
│    - list_available_repos()                                │
│    - explore_structure(repo_id, path)                      │
│    - get_file(repo_id, file_path, start_line, end_line)   │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│        Vector Search (Couchbase FTS - Hybrid Mode)          │
│  1. Embed query with sentence-transformers                  │
│     Model: nomic-ai/nomic-embed-text-v1.5 (768 dims)       │
│  2. Hybrid search: query (type filter) + kNN               │
│     knn_operator: "and" for proper pre-filtering           │
│  3. Fetch top-k documents from Couchbase                    │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│            Markdown Narrative Generation                     │
│  - High-quality explanations                                │
│  - Code blocks with syntax highlighting                     │
│  - File references (repo/path:line)                        │
│  - Actionable insights                                      │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│              Streaming / Non-Streaming Response             │
└─────────────────────────────────────────────────────────────┘
```

## Features

### ✅ Implemented

1. **Intent Classification & Query Routing**
   - Classifies query intent to select appropriate search level
   - CONCEPTUAL queries → `doc` level (documentation, guidelines)
   - IMPLEMENTATION queries → `file` level (default)
   - SPECIFIC queries → `symbol` level (find exact function/class)
   - ARCHITECTURAL queries → `module` level (folder structure)
   - OVERVIEW queries → `repo` level (high-level understanding)

2. **Multi-Level Vector Search**
   - Couchbase FTS with hybrid search (query + kNN + knn_operator)
   - Five search levels: symbol, file, module, repo, doc
   - Uses sentence-transformers for embedding (nomic-embed-text-v1.5)
   - Supports repo filtering and preview mode
   - Returns top-k most relevant documents

3. **Preview Mode**
   - `preview=True` returns truncated content (~200 chars)
   - Useful for scanning multiple results before fetching full content
   - Reduces token usage in LLM context

4. **Tool-Calling Agent**
   - PydanticAI framework
   - Ollama backend (deepseek-coder:6.7b recommended)
   - Four tools: `search_code()`, `list_available_repos()`, `explore_structure()`, `get_file()`
   - Agent decides when and how to use tools based on query intent

5. **Conversation History**
   - Supports last N messages for context
   - Sent with each request from client
   - Helps with follow-up questions
   - Keeps last 6 messages (3 exchanges) per agent instance

6. **Streaming & Non-Streaming**
   - Streaming: Server-Sent Events (SSE) via FastAPI StreamingResponse
   - Non-streaming: Standard JSON response
   - Client chooses mode with `stream: true/false` flag

7. **Markdown Narrative**
   - Agent generates well-formatted markdown
   - Code blocks with language tags
   - File references with citations
   - Clear structure with headers and lists

## API Endpoints

### `POST /api/chat/` (Authenticated)

Chat with RAG-enriched responses. Requires JWT token.

**Request:**
```json
{
  "query": "How does authentication work in the API?",
  "stream": false,
  "conversation_history": [
    {"role": "user", "content": "What frameworks do we use?"},
    {"role": "assistant", "content": "We use FastAPI and PydanticAI."}
  ]
}
```

**Response (Non-streaming):**
```json
{
  "answer": "# Authentication in the API\n\nThe API uses JWT...",
  "metadata": {
    "tenant_id": "code_kosha",
    "conversation_length": 2
  }
}
```

**Response (Streaming):**
```
text/plain stream
Chunks of markdown text...
```

### `POST /api/chat/test` (Unauthenticated)

Test endpoint for internal use. Same interface as `/api/chat/` but no auth required.

### `GET /api/chat/health`

Health check for chat service.

## Configuration

### Environment Variables

```bash
# Ollama
OLLAMA_HOST=http://localhost:11434

# Couchbase
COUCHBASE_HOST=localhost
COUCHBASE_USERNAME=Administrator
COUCHBASE_PASSWORD=your_password
COUCHBASE_BUCKET_CODE=code_kosha

# JWT (for authenticated endpoints)
JWT_SECRET=your_secret_key
```

### Ollama Models

**Required models:**
- `deepseek-coder:6.7b` - Main chat model (or similar code-focused LLM)
- Embedding is handled by sentence-transformers locally (faster, no Ollama needed)

**Check installed models:**
```bash
ollama list
```

**Pull a model:**
```bash
ollama pull deepseek-coder:6.7b
```

**Alternative models:**
- `qwen2.5-coder:7b` - Good alternative
- `codellama:13b` - Larger, more capable
- `mistral:7b` - General purpose

## Usage Examples

### Non-Streaming Chat

```bash
# Get auth token
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "demo", "password": "demo"}' | jq -r '.access_token')

# Ask a question
curl -X POST http://localhost:8000/api/chat/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "How does vector search work in the codebase?",
    "stream": false
  }' | jq .
```

### Streaming Chat

```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Show me examples of FastAPI routes",
    "stream": true
  }'
```

### With Conversation History

```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Can you show me more examples?",
    "stream": false,
    "conversation_history": [
      {"role": "user", "content": "How do we handle authentication?"},
      {"role": "assistant", "content": "We use JWT tokens with bcrypt..."}
    ]
  }' | jq .
```

## Testing

### Quick Test

```bash
cd 4-consume/api-server
./test_rag_api.sh
```

### Manual Tests

1. **Start the server:**
   ```bash
   cd 4-consume/api-server
   source venv/bin/activate
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

2. **Test health:**
   ```bash
   curl http://localhost:8000/health
   ```

3. **Test simple query:**
   ```bash
   curl -X POST http://localhost:8000/api/chat/test \
     -H "Content-Type: application/json" \
     -d '{"query": "How does the RAG system work?", "stream": false}'
   ```

4. **Test streaming:**
   ```bash
   curl -X POST http://localhost:8000/api/chat/test \
     -H "Content-Type: application/json" \
     -d '{"query": "Explain vector search", "stream": true}'
   ```

## Code Structure

```
services/api-server/app/
├── chat/
│   ├── routes.py              # FastAPI endpoints
│   └── pydantic_rag_agent.py  # Main RAG agent with PydanticAI
├── rag/
│   ├── __init__.py
│   ├── models.py              # Pydantic models (SearchLevel, SearchResult, etc.)
│   └── tools.py               # Tool implementations (search_code, list_repos, etc.)
└── database/
    └── couchbase_client.py    # Couchbase connection management
```

**Key files:**
- `app/rag/models.py` - SearchLevel enum with symbol/file/module/repo/doc levels
- `app/rag/tools.py` - Core tool implementations with hybrid FTS search
- `app/chat/pydantic_rag_agent.py` - System prompt with intent detection strategy
- `app/chat/routes.py` - HTTP endpoints for RAG API

## Performance Considerations

1. **Embedding Model**: Loaded once globally (singleton pattern)
2. **HTTP Client**: Reused across requests (pooling)
3. **Couchbase Connection**: Singleton cluster connection
4. **Vector Search**: Native FTS kNN (very fast, ~50-100ms)
5. **Streaming**: Reduces perceived latency for long responses

## Troubleshooting

### "Model not found" error

```bash
# Check models
ollama list

# Pull the model
ollama pull deepseek-coder:6.7b
```

### "Connection refused" to Ollama

```bash
# Check if Ollama is running
curl http://localhost:11434/api/version

# Start Ollama if needed
ollama serve
```

### "FTS search failed"

- Check if Couchbase is running
- Verify `code_vector_index` exists in Couchbase Web UI (port 8091)
- Ensure embeddings exist in documents (field: `embedding`)

### Slow responses

- Use streaming mode for better UX
- Consider using a smaller/faster model (e.g., qwen2.5-coder:1.5b)
- Check if embedding model is loaded (first request is slower)

## Next Steps

- [x] ~~Implement hybrid search (vector + keyword)~~ - Done via query + knn + knn_operator
- [x] ~~Add tool for querying by file/repo directly~~ - Done via get_file and explore_structure
- [ ] Add re-ranking for better relevance
- [ ] Add citation tracking for sources
- [ ] Implement conversation memory persistence
- [ ] Add user feedback collection
- [ ] Create evaluation metrics dashboard
- [ ] Implement multi-hop reasoning for complex queries

## Related Documentation

- [README.md](./README.md) - API server overview
- [STATUS.md](./STATUS.md) - Implementation status
- [../../README.md](../../README.md) - Project overview
- [PydanticAI Docs](https://ai.pydantic.dev/) - Framework documentation
