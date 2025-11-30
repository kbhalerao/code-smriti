# CodeSmriti - Forever Memory MCP System

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Docker](https://img.shields.io/badge/docker-required-blue.svg)](https://www.docker.com/)

*Smriti (स्मृति): Sanskrit for "memory, remembrance, that which is remembered"*

A persistent knowledge base system that intelligently indexes, organizes, and retrieves code and documentation from ~100 GitHub repositories. Built on the Model Context Protocol (MCP) for seamless integration with AI assistants.

## The Problem

Your team has built incredible solutions across dozens of repositories over the years, but:

- 🔍 **"Didn't we solve this before?"** - Engineers waste days reimplementing features that already exist somewhere
- 📚 **Knowledge Silos** - Best practices and architecture decisions are trapped in old PRs, Slack threads, or departed engineers' heads
- 🔄 **Reinventing the Wheel** - Each new project starts from scratch instead of leveraging proven patterns
- 🤔 **Onboarding Pain** - New engineers spend weeks understanding "how we do things here"
- 💭 **Lost Context** - "Why did we choose JWT over sessions?" - Nobody remembers, and the reasoning is buried somewhere

**The cost**: Wasted engineering time, inconsistent implementations, and knowledge that disappears when people leave.

## The Solution

CodeSmriti is your team's "Pensieve" - a forever memory system that:
- 📚 **Ingests** all your code repositories and documentation automatically
- 🧠 **Understands** code semantically using vector embeddings (not just keyword matching)
- 🏷️ **Organizes** content with LLM-powered tagging and human input
- 🔍 **Retrieves** similar implementations and best practices instantly
- 🔗 **Integrates** with your AI assistants via MCP protocol
- 💡 **Preserves** institutional knowledge across team changes

**Ask CodeSmriti:**
- "Show me how we implemented rate limiting across our services"
- "Find all architecture decisions about authentication"
- "What are our best practices for error handling?"
- "I need to build a retry mechanism - show me similar code"

## ⚡ V4 Architecture (November 2025)

CodeSmriti V4 introduces **hierarchical document indexing** with **LLM-generated summaries**:

- ✅ **Hierarchical Structure** - repo → module → file → symbol (bottom-up aggregation)
- ✅ **LLM Summaries** - Every document has a semantic summary for better search
- ✅ **No Raw Code Storage** - Summaries only in index; fetch code on demand via API
- ✅ **Local Embeddings** - `nomic-embed-text` (768d) with Apple Silicon MPS acceleration
- ✅ **Multi-Level Search** - Query at symbol, file, module, or repo granularity

**Production Stats (101 repos):**
- 48,795 documents indexed (13K files, 31K symbols, 4K modules)
- ~1,850 tokens per file for LLM enrichment
- 32 hours total ingestion time (~20 min/repo avg)

📖 **[V4 Schema Spec](docs/V4_SCHEMA_SPEC.md)** | **[V4 Ingestion Guide](docs/V4_INGESTION.md)**

## How It Works

### Data Flow (V4)

```
┌─────────────────────────────────────────────────────────────────┐
│                      1. PARSE & EXTRACT                         │
│  GitHub Repos → Clone → tree-sitter → Symbols (functions/classes)│
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│                    2. LLM ENRICHMENT                            │
│  Symbols → LLM Summary → Files → Modules → Repo (bottom-up)    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│                      3. EMBEDDING                               │
│  Summaries → nomic-embed-text (768d) → Vector Embeddings       │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│                      4. STORAGE                                 │
│  Couchbase: {summary, embedding, metadata, hierarchy links}    │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│                    5. RETRIEVAL                                 │
│  Query → Embed → Vector Search (symbol/file/module/repo level) │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│                   6. AI INTEGRATION                             │
│  MCP Tools → Claude Code → Navigate hierarchy → Fetch code     │
└─────────────────────────────────────────────────────────────────┘
```

### Step-by-Step Process (V4)

**1. Parse & Extract**
- Clone repositories to local storage
- tree-sitter parses code into symbols (functions, classes, methods)
- Extract imports, docstrings, and structural metadata
- Symbols >= 5 lines get their own searchable document

**2. LLM Enrichment (Bottom-Up)**
- LLM generates summary for each symbol from code + docstring
- File summaries aggregate from symbol summaries
- Module (folder) summaries aggregate from file summaries
- Repository summary aggregates from module summaries

**3. Embedding Generation**
- Each summary is embedded as a 768-dimensional vector
- Uses `nomic-embed-text` with Apple Silicon MPS acceleration
- Embeddings capture semantic meaning for similarity search

**4. Hierarchical Storage**
- Couchbase stores 4 document types: `repo_summary`, `module_summary`, `file_index`, `symbol_index`
- Each document has: summary, embedding, metadata, parent/children links
- No raw code stored - fetch on demand via `get_file` API

**5. Multi-Level Retrieval**
- Search at any level: symbol (specific), file, module, or repo (broad)
- Vector search finds semantically similar summaries
- Navigate hierarchy: drill down from repo → module → file → symbol
- Fetch actual code only when needed

**6. AI Integration**
- MCP tools: `list_repos`, `explore_structure`, `search_codebase`, `get_file`, `ask_codebase`
- Claude Code navigates the hierarchy intelligently
- Progressive disclosure: overview first, then drill into details

### Example Query Flow (V4)

```
You: "How does authentication work in labcore?"

1. search_codebase("authentication", level="module")
   → Finds: associates/ module - "Handles user auth, permissions, guardian integration"

2. explore_structure("kbhalerao/labcore", "associates/")
   → Shows: models.py, views.py, backends.py, permissions.py

3. search_codebase("login flow", level="symbol", repo="kbhalerao/labcore")
   → Finds: LoginView class (views.py:45-120), authenticate() (backends.py:15-45)

4. get_file("kbhalerao/labcore", "associates/backends.py", 15, 45)
   → Returns actual code for the authenticate function

Result: Progressive disclosure from high-level overview to specific implementation
```

## Key Capabilities

**Semantic Search**
- Finds code by meaning, not just keywords
- "authentication" matches "login", "auth", "verify user"

**Contextual Metadata**
- Know who wrote it, when, and why (from commit messages)
- See how code evolved over time

**Cross-Repository Patterns**
- Discover how different teams solved similar problems
- Identify inconsistencies and standardization opportunities

**Institutional Memory**
- Tag decisions with hashtags: `#architecture-decision`, `#best-practices`
- Never lose the "why" behind your choices

**AI-Native Integration**
- Works seamlessly with Claude Desktop, VSCode, or any MCP client
- Agentic workflows: AI can iteratively explore and synthesize findings

## Architecture

### Internal Architecture

```
┌─────────────┐         ┌──────────────┐         ┌────────────────┐
│   Ollama    │◄────────│  MCP Server  │◄────────│  API Gateway   │
│  (Native)   │         │  (Docker)    │         │    (Nginx)     │
└─────────────┘         └──────┬───────┘         └────────────────┘
                                │
                        ┌───────┴───────┐
                        │               │
                   ┌────▼─────┐   ┌────▼──────────┐
                   │ Couchbase │   │   Ingestion   │
                   │  Vector   │   │    Worker     │
                   │  Database │   │   (Docker)    │
                   └───────────┘   └───────────────┘
```

### External Access (Production)

```
Internet
   │
   ▼
┌──────────────────────────────────────────────┐
│   External Nginx (SSL Termination)           │
│   - Domain routing (codesmriti.domain.com)   │
│   - Let's Encrypt SSL certificates           │
│   - Cloudflare proxy (optional)              │
└───────────────┬──────────────────────────────┘
                │ HTTP (port 80)
                ▼
┌──────────────────────────────────────────────┐
│   CodeSmriti Internal Nginx                  │
│   - No SSL needed (behind external proxy)    │
│   - Routes to MCP Server                     │
└───────────────┬──────────────────────────────┘
                │
                ▼
        MCP Server (port 8080)
```

**Components:**
- **MCP Server**: FastAPI-based MCP server with HTTP/SSE transport
- **Couchbase**: Unified document + vector storage
- **Ollama**: Local LLM running natively on Mac M3 Ultra
- **Ingestion Worker**: Background service for parsing and indexing repos
- **Internal Nginx**: API gateway for routing (no SSL)
- **External Nginx**: SSL termination and domain-based routing

## Technology Stack

- **MCP Framework**: Python + FastAPI
- **Vector DB**: Couchbase 8.0 with Vector Search
- **Embeddings**: `nomic-embed-text-v1.5` (768 dimensions, MPS acceleration)
- **LLM Enrichment**: LM Studio or Ollama (qwen2.5-coder, codellama, deepseek-coder)
- **Code Parsing**: tree-sitter (Python, JavaScript/TypeScript, Go, Rust, Java)
- **Authentication**: JWT with API keys
- **Document Schema**: V4 hierarchical (repo → module → file → symbol)

## Deployment Options

### Option 1: External Nginx Reverse Proxy (Recommended for Production)

If you have a separate nginx gateway handling SSL and domain routing:

**On your external nginx server**, add this upstream configuration:

```nginx
upstream codesmriti {
    server internal-server-ip:80;  # CodeSmriti machine IP
}

server {
    listen 443 ssl http2;
    server_name codesmriti.yourdomain.com;

    ssl_certificate /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass http://codesmriti;
        proxy_http_version 1.1;

        # WebSocket/SSE support for MCP
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";

        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Long timeouts for MCP operations
        proxy_connect_timeout 600s;
        proxy_send_timeout 600s;
        proxy_read_timeout 600s;
    }
}
```

**On your CodeSmriti machine**, just run:

```bash
docker-compose up -d
```

No SSL configuration needed on CodeSmriti - the external nginx handles it.

### Option 2: Direct Internet Access with SSL

If CodeSmriti is directly exposed to the internet, see [SSL-SETUP.md](SSL-SETUP.md) for configuring SSL with Certbot and Cloudflare.

## Quick Start

### Fresh M3 Mac Installation (Automated)

**For local Mac with GUI access:**
```bash
cd code-smriti
./quick-install.sh
```

**For remote Mac via SSH (no GUI):**
```bash
cd code-smriti
./quick-install-headless.sh    # Uses Colima instead of Docker Desktop
```

Both scripts handle everything automatically:
- Install Homebrew, Docker/Colima, and Ollama
- Download AI models (~15GB)
- Configure environment
- Start all services
- Initialize database

**Estimated time: 30-60 minutes**

See **[INSTALL.md](INSTALL.md)** for detailed installation instructions and manual setup steps.

### Quick Manual Setup (if dependencies already installed)

If you already have Docker and Ollama installed:

```bash
# 1. Configure environment
cp .env.example .env
nano .env  # Set:
          # - COUCHBASE_PASSWORD
          # - JWT_SECRET
          # - GITHUB_TOKEN
          # - GITHUB_REPOS (comma-separated list or use pipeline_ingestion.py)
          # - EMBEDDING_BACKEND=local (recommended - 10-20x faster)
          # - REPOS_PATH=/path/to/repos (outside project to prevent recursion)

# 2. Pull AI models (for Ollama - optional if using local embedding)
ollama pull nomic-embed-text  # optional - only if EMBEDDING_BACKEND=ollama
ollama pull codellama:13b     # for code generation/chat

# 3. Start services
docker-compose up -d

# 4. Initialize database (first time only)
docker exec -it codesmriti_couchbase /opt/init-couchbase.sh

# 5. Generate API key and trigger ingestion
python3 scripts/generate-api-key.py
curl -X POST http://localhost/api/ingest/trigger \
  -H "Authorization: Bearer YOUR_API_KEY"
```

## Usage

### MCP Tools

CodeSmriti provides the following MCP tools:

#### `search_code`
Search for code across all indexed repositories.

```json
{
  "name": "search_code",
  "arguments": {
    "query": "user authentication with JWT",
    "repo": "myorg/api-server",
    "language": "python",
    "limit": 10
  }
}
```

#### `get_code_context`
Retrieve a specific code file with metadata.

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

#### `find_similar`
Find code similar to a given snippet.

```json
{
  "name": "find_similar",
  "arguments": {
    "code_snippet": "async def fetch_user(id: int):\n    return await db.query(...)",
    "language": "python",
    "limit": 5
  }
}
```

#### `add_note`
Add a memory note with hashtags.

```json
{
  "name": "add_note",
  "arguments": {
    "content": "We decided to use JWT for auth because...",
    "hashtags": ["authentication", "architecture-decision"],
    "project": "api-server"
  }
}
```

#### `query_by_hashtag`
Retrieve content by hashtags.

```json
{
  "name": "query_by_hashtag",
  "arguments": {
    "hashtags": ["authentication", "best-practices"],
    "content_type": "all"
  }
}
```

#### `list_repos`
List all indexed repositories.

```json
{
  "name": "list_repos",
  "arguments": {}
}
```

### REST API

CodeSmriti also provides REST API endpoints:

```bash
# Trigger manual re-indexing
POST /api/ingest/trigger
Authorization: Bearer YOUR_API_KEY

# Add a memory note
POST /api/notes
Authorization: Bearer YOUR_API_KEY
Content-Type: application/json
{
  "content": "Note content in markdown",
  "hashtags": ["tag1", "tag2"],
  "project": "project-name"
}

# Get system status
GET /api/status
Authorization: Bearer YOUR_API_KEY
```

### Connecting from Claude Desktop

See [docs/MCP-USAGE.md](docs/MCP-USAGE.md) for detailed instructions on connecting CodeSmriti to Claude Desktop or other MCP clients.

## Management

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f mcp-server
docker-compose logs -f ingestion-worker
docker-compose logs -f couchbase
```

### Restart Services

```bash
# Restart all
docker-compose restart

# Restart specific service
docker-compose restart mcp-server
```

### Stop CodeSmriti

```bash
docker-compose down

# To also remove volumes (WARNING: deletes all data)
docker-compose down -v
```

### Re-index a Repository

```bash
# Trigger re-indexing via API
curl -X POST http://localhost/api/ingest/trigger?repo=owner/repo \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Generate API Keys for Team Members

```bash
./scripts/generate-api-key.py

# Follow the prompts to create a new API key
# Share the key securely with team members
```

## Development

### Project Structure

```
CodeSmriti/
├── docker-compose.yml           # Main orchestration
├── .env.example                 # Environment template
├── mcp-server/                  # FastMCP server
│   ├── server.py                # Main server
│   ├── config.py                # Configuration
│   ├── tools/                   # MCP tools
│   ├── resources/               # MCP resources
│   └── auth/                    # Authentication
├── ingestion-worker/            # Background ingestion
│   ├── worker.py                # Main worker
│   ├── parsers/                 # Code/doc parsers
│   └── embeddings/              # Embedding generation
├── api-gateway/                 # Nginx configuration
├── scripts/                     # Management scripts
└── docs/                        # Documentation
```

### Adding Support for New Languages

Edit `ingestion-worker/config.py`:

```python
supported_code_extensions = [".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs"]
```

Add tree-sitter parser in `ingestion-worker/parsers/code_parser.py`:

```python
self.parsers = {
    "python": get_parser("python"),
    "javascript": get_parser("javascript"),
    "typescript": get_parser("typescript"),
    "go": get_parser("go"),  # Add new language
}
```

### Adding Support for New Document Formats

Update `ingestion-worker/config.py`:

```python
supported_doc_extensions = [".md", ".txt", ".json", ".yaml", ".yml", ".pdf"]
```

Implement parser in `ingestion-worker/parsers/document_parser.py`.

## Troubleshooting

### Couchbase won't start

```bash
# Check logs
docker logs codesmriti_couchbase

# Ensure ports are not in use
lsof -i :8091-8097

# Reset Couchbase
docker-compose down
docker volume rm codesmriti_couchbase_data
docker-compose up -d couchbase
```

### MCP Server can't connect to Ollama

Ensure Ollama is running natively on the Mac:

```bash
# Check if Ollama is running
curl http://localhost:11434/api/version

# If not, start it
ollama serve
```

### Ingestion worker fails

```bash
# Check logs
docker logs codesmriti_ingestion_worker

# Common issues:
# - Invalid GitHub token (check .env)
# - Repository doesn't exist or is private
# - Network issues
```

### Vector search not working

Ensure the vector search index is created in Couchbase:

```bash
# Access Couchbase UI
open http://localhost:8091

# Navigate to Search → Full Text Search
# Create index using the definition from init-couchbase.sh
```

## Performance

**V4 Ingestion** (Mac M3 Ultra, LM Studio with qwen2.5-coder-7b):
- **101 repos**: 32 hours total (~20 min/repo average)
- **48,795 documents**: 13K files, 31K symbols, 4K modules
- **LLM tokens**: ~1,850 per file (estimated input+output)
- **Embedding Generation**: ~1,280 docs/minute (Apple Silicon MPS)

**Search Performance**:
- Vector search latency: <100ms
- Concurrent requests: 50+ simultaneous MCP calls
- Multi-level queries: symbol → file → module → repo

## Security

- JWT-based authentication with configurable expiration
- API keys can be scoped (read/write/admin)
- Rate limiting supported via Nginx
- HTTPS/TLS ready (configure certificates in api-gateway/)
- GitHub tokens stored securely in environment variables

## Roadmap

- [ ] Complete Couchbase storage integration
- [ ] Add WebSocket support for real-time updates
- [ ] Implement PDF document parsing
- [ ] Add support for more programming languages (Go, Rust, Java)
- [ ] Create web UI for browsing and managing content
- [ ] Add GitHub webhook integration for automatic re-indexing
- [ ] Implement user management and team workspaces
- [ ] Add metrics and observability (Prometheus/Grafana)

## License

MIT License - see [LICENSE](LICENSE) file for details.

CodeSmriti is open source and free to use, modify, and distribute.

## Support

For questions or issues:
1. Check the logs: `docker-compose logs -f`
2. Review documentation in `docs/`
3. Contact the team

---

**Built with 🧠 for never forgetting how we solved that problem last time.**
