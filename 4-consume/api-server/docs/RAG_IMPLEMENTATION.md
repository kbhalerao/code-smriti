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
│              Intent Validation (Heuristic)                   │
│  - Code-related? ✓                                          │
│  - Off-topic? ✗                                             │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│           PydanticAI Agent (Ollama: deepseek-coder)        │
│  System Prompt: Code research assistant                     │
│  Tools:                                                      │
│    - search_code(query, limit, repo_filter)                │
│    - list_available_repos()                                │
└───────────────────────┬─────────────────────────────────────┘
                        │
                        ▼
┌─────────────────────────────────────────────────────────────┐
│              Vector Search (Couchbase FTS)                   │
│  1. Embed query with sentence-transformers                  │
│     Model: nomic-ai/nomic-embed-text-v1.5 (768 dims)       │
│  2. FTS kNN search on "code_vector_index"                   │
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

1. **Intent Classification**
   - Heuristic-based validation (code vs off-topic keywords)
   - Context-aware (considers conversation history)
   - Fast pre-filter before expensive operations

2. **Vector Search**
   - Couchbase FTS with kNN vector similarity
   - Uses sentence-transformers for embedding (matches ingestion model)
   - Supports repo filtering
   - Returns top-k most relevant code chunks

3. **Tool-Calling Agent**
   - PydanticAI framework
   - Ollama backend (deepseek-coder:6.7b recommended)
   - Two tools: `search_code()` and `list_available_repos()`
   - Agent decides when and how to use tools

4. **Conversation History**
   - Supports last N messages for context
   - Sent with each request from client
   - Helps with follow-up questions
   - Keeps last 6 messages (3 exchanges) per agent instance

5. **Streaming & Non-Streaming**
   - Streaming: Server-Sent Events (SSE) via FastAPI StreamingResponse
   - Non-streaming: Standard JSON response
   - Client chooses mode with `stream: true/false` flag

6. **Markdown Narrative**
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
4-consume/api-server/app/chat/
├── routes.py                  # FastAPI endpoints
├── pydantic_rag_agent.py     # Main RAG agent with PydanticAI
├── simple_agent.py           # Legacy agent (kept for reference)
├── tools.py                   # Tool definitions (legacy)
├── agent.py                   # Legacy agent implementation
└── intent.py                  # Legacy intent classification
```

**Key files:**
- `pydantic_rag_agent.py:23` - System prompt for agent
- `pydantic_rag_agent.py:44` - Agent creation with tools
- `pydantic_rag_agent.py:54` - `search_code` tool implementation
- `pydantic_rag_agent.py:256` - Main chat method
- `pydantic_rag_agent.py:305` - Streaming chat method
- `routes.py:54` - Authenticated chat endpoint
- `routes.py:189` - Test chat endpoint

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

- [ ] Add re-ranking for better relevance
- [ ] Implement hybrid search (vector + keyword)
- [ ] Add citation tracking for sources
- [ ] Implement conversation memory persistence
- [ ] Add user feedback collection
- [ ] Create evaluation metrics dashboard
- [ ] Add tool for querying by file/repo directly
- [ ] Implement multi-hop reasoning for complex queries

## Related Documentation

- [README.md](./README.md) - API server overview
- [STATUS.md](./STATUS.md) - Implementation status
- [../../README.md](../../README.md) - Project overview
- [PydanticAI Docs](https://ai.pydantic.dev/) - Framework documentation
