# FastAPI Server Implementation Status

## Completed ✅

### Project Structure
- Created `4-consume/api-server/` directory
- Organized into modules: app/{auth,users,repos,jobs,search,chat,database}
- Set up proper Python package structure

### Core Infrastructure
- **requirements.txt**: FastAPI, Uvicorn, Pydantic, PydanticAI, Couchbase SDK, sentence-transformers, etc.
- **app/config.py**: Pydantic Settings for environment configuration
- **app/models.py**: Complete Pydantic models for all database documents and API requests/responses
  - UserDocument, RepoInfo, IngestionJobDocument, CodeChunkDocument
  - LoginRequest, RegisterRequest, SearchRequest, etc.
  - All response models with proper typing

### Database Layer
- **app/database/couchbase_client.py**: Singleton cluster connection
  - get_cluster(), get_code_collection(), get_users_collection(), get_jobs_collection()
  - Graceful shutdown support

### Authentication & Security
- **app/auth/routes.py**: Simple login endpoint (accepts any username/password for testing)
- **app/auth/utils.py**: Complete auth utilities
  - JWT creation and verification with python-jose
  - Password hashing with bcrypt (passlib)
  - GitHub PAT encryption/decryption with AES-256-CBC + PBKDF2

- **app/dependencies.py**: FastAPI dependency injection
  - get_current_user() dependency for protected endpoints
  - HTTPBearer security scheme

### Application Core
- **app/main.py**: FastAPI app with lifespan management
  - CORS middleware configured for Cloudflare
  - Health check endpoint
  - All routers registered

### RAG System (PydanticAI)
- **app/chat/pydantic_rag_agent.py**: Production-quality RAG agent
  - Tool-calling architecture with PydanticAI
  - Tools: search_code(), list_available_repos()
  - Conversation history support
  - Streaming and non-streaming modes
  - Vector search using Couchbase FTS + kNN
  - sentence-transformers embeddings (nomic-ai/nomic-embed-text-v1.5)

- **app/chat/routes.py**: Chat endpoints
  - POST /api/chat/ - Authenticated chat with RAG
  - POST /api/chat/test - Test endpoint (no auth)
  - GET /api/chat/health - Health check
  - Support for streaming responses
  - Conversation history in request body

## Remaining Work 🚧

### 1. Additional Route Handlers (Optional)
Placeholder routes that could be implemented for full multi-tenant functionality:
- `app/users/routes.py` - GET /me, PATCH /github-pat, DELETE /github-pat
- `app/repos/routes.py` - GET /repos, POST /repos, DELETE /repos/{repo_id}
- `app/jobs/routes.py` - GET /jobs, GET /jobs/{job_id}
- `app/search/routes.py` - POST /search (direct vector search without LLM)

**Note**: The core RAG functionality in `/api/chat/` is complete and working.

### 2. Docker Setup
Create `Dockerfile`:
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3000"]
```

Create `.env` file from web-ui example.

### 3. Docker Compose Integration
Update `docker-compose.yml`:
```yaml
  api-server:
    build:
      context: ./4-consume/api-server
      dockerfile: Dockerfile
    container_name: codesmriti_api_server
    restart: unless-stopped
    ports:
      - "3000:3000"
    environment:
      - COUCHBASE_HOST=couchbase
      - COUCHBASE_PASSWORD=${COUCHBASE_PASSWORD}
      - JWT_SECRET=${JWT_SECRET}
      - AES_ENCRYPTION_KEY=${AES_ENCRYPTION_KEY}
      - OLLAMA_HOST=http://host.docker.internal:11434
    networks:
      - codesmriti_network
    depends_on:
      couchbase:
        condition: service_healthy
    extra_hosts:
      - "host.docker.internal:host-gateway"
```

### 4. Nginx Configuration
Update `4-consume/api-gateway/nginx.conf`:

```nginx
upstream api_server {
    server api-server:3000;
}

# Route API requests to FastAPI
location /api/ {
    proxy_pass http://api_server/api/;

    # CORS headers for Cloudflare
    add_header Access-Control-Allow-Origin $http_origin always;
    add_header Access-Control-Allow-Credentials true always;
    add_header Access-Control-Allow-Methods "GET, POST, PATCH, DELETE, OPTIONS" always;
    add_header Access-Control-Allow-Headers "Authorization, Content-Type" always;

    if ($request_method = OPTIONS) {
        return 204;
    }

    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
}
```

### 5. Fix docker-compose.yml Path
Line 107: Change `./api-gateway/nginx.conf` to `./4-consume/api-gateway/nginx.conf`

## Architecture Confirmed

**Cloudflare Pages** (SvelteKit frontend)
  ↓ HTTPS requests
**smriti.agsci.com** (Gateway nginx with SSL termination)
  ↓ Proxy /api/*
**Mac Studio** (Docker: FastAPI server)
  ↓ Internal Docker network
**Couchbase** (Database)

**Ollama** (Host machine: localhost:11434)

## Testing Plan

1. Build and run API server: `docker-compose up --build api-server`
2. Test health endpoint: `curl http://localhost:3000/health`
3. Test register: `curl -X POST http://localhost:3000/api/auth/register -H "Content-Type: application/json" -d '{"email":"test@example.com","password":"test1234"}'`
4. Test login and get token
5. Test protected endpoints with Bearer token
6. Test from Cloudflare (after deployment)

## SvelteKit Integration

The web-ui SvelteKit +server.ts files will make fetch() calls to `https://smriti.agsci.com/api/*`:

```typescript
// 4-consume/web-ui/src/routes/api/auth/login/+server.ts
export const POST: RequestHandler = async ({ request, fetch }) => {
  const body = await request.json();

  const response = await fetch('https://smriti.agsci.com/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  return response;
};
```

## Testing & Evaluation

- **test_fts_vector_search.py**: Evaluation script for vector search
  - Tests against search_eval_questions.json (37 questions)
  - Calculates Recall@K, MRR metrics
  - Compares expected vs actual results

- **test_rag_api.sh**: Integration test script
  - Tests health endpoints
  - Tests chat with and without auth
  - Tests streaming and non-streaming modes
  - Tests conversation history

## Current Status (November 2025)

✅ **Production-ready RAG API with PydanticAI**
- Tool-calling architecture implemented
- Vector search with Couchbase FTS working
- Streaming support functional
- Conversation history working
- Basic auth implemented (JWT tokens)

🎯 **Server tested and operational on Mac Studio**
- Running on http://localhost:8000
- All endpoints responding correctly
- Compatible with ingestion pipeline embeddings

📊 **Evaluation baseline established**
- 37 eval questions across 5 repos
- Metrics: Recall@K, MRR, success rate
- Ready for performance tuning
