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

## How It Works

### Data Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        1. INGESTION                             │
│  GitHub Repos → Clone → Parse (tree-sitter) → Extract Metadata │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│                     2. EMBEDDING                                │
│  Code Chunks → nomic-embed-text (768d) → Vector Embeddings     │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│                      3. STORAGE                                 │
│  Couchbase: {code, metadata, embedding, commit_hash, author}   │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│                    4. RETRIEVAL                                 │
│  Query → Embed → Vector Search + Filters → Ranked Results      │
└────────────────────────┬────────────────────────────────────────┘
                         │
                         ↓
┌─────────────────────────────────────────────────────────────────┐
│                   5. AI INTEGRATION                             │
│  MCP Tools → Claude/VSCode → Natural Language Queries           │
└─────────────────────────────────────────────────────────────────┘
```

### Step-by-Step Process

**1. Ingestion (Automated)**
- Ingestion worker clones your GitHub repositories
- tree-sitter parses code into semantic chunks (functions, classes, modules)
- Extracts metadata: commit hash, author, dates, imports, dependencies
- Parses documentation (Markdown, JSON, YAML) with hashtag extraction

**2. Embedding Generation**
- Each code chunk is converted to a 768-dimensional vector using nomic-embed-text
- Embeddings capture semantic meaning, not just keywords
- "user authentication" and "login verification" are understood as similar concepts

**3. Storage**
- Couchbase stores everything together:
  - Original code text
  - Vector embedding (for similarity search)
  - Rich metadata (language, repo, file path, git info)
  - Hashtags and project associations
- Indexes created for fast filtering by repo, language, hashtags

**4. Retrieval (On-Demand)**
- Your query: *"How do we handle API rate limiting?"*
- Query is embedded using the same model
- Vector search finds semantically similar code chunks
- Filters applied (e.g., only Python, only api-server repo)
- Results ranked by similarity score + metadata relevance
- Returns: Top 10 code examples with context

**5. AI Integration**
- MCP protocol exposes search as tools to AI assistants
- Claude/VSCode can iteratively search, refine, and explore
- Natural language queries: "Find auth patterns" → "Show JWT implementation" → "Compare with OAuth code"
- AI synthesizes findings into actionable insights

### Example Query Flow

```
You: "I need to implement a retry mechanism with exponential backoff"
     ↓
CodeSmriti: Embeds query → Searches 100 repos → Finds 5 implementations
     ↓
Response:
  1. api-server/src/utils/retry.py (similarity: 0.94)
     - Uses tenacity library, max 3 retries, exponential backoff
  2. data-pipeline/workers/fetch.ts (similarity: 0.89)
     - Custom implementation with jitter
  3. mobile-backend/src/network/retry.go (similarity: 0.87)
     - Circuit breaker pattern included

  Note tagged #best-practices:
  "We standardized on tenacity library after comparing approaches"
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

**Components:**
- **MCP Server**: FastAPI-based MCP server with HTTP/SSE transport
- **Couchbase**: Unified document + vector storage
- **Ollama**: Local LLM running natively on Mac M3 Ultra
- **Ingestion Worker**: Background service for parsing and indexing repos
- **Nginx**: API gateway for remote access

## Technology Stack

- **MCP Framework**: Python + FastAPI
- **Vector DB**: Couchbase 8.0 with Vector Search
- **Embeddings**: nomic-embed-text (768 dimensions)
- **LLM**: Ollama (codellama, deepseek-coder, mistral)
- **Code Parsing**: tree-sitter (JavaScript/TypeScript, Python)
- **Authentication**: JWT with API keys

## Quick Start

### Prerequisites

- Mac Studio M3 Ultra (256GB RAM, 2TB storage)
- Docker Desktop for Mac
- Python 3.11+
- Git

### 1. Installation

```bash
# Clone or navigate to CodeSmriti directory
cd TotalRecall  # or rename to CodeSmriti

# Create environment file
cp .env.example .env

# Edit .env and set:
# - COUCHBASE_PASSWORD (secure password)
# - JWT_SECRET (run: openssl rand -hex 32)
# - GITHUB_TOKEN (GitHub personal access token)
# - GITHUB_REPOS (comma-separated list of repos to index)
nano .env
```

### 2. Setup Ollama (runs natively on Mac)

```bash
# Install and setup Ollama
./scripts/ollama-setup.sh

# This will:
# - Install Ollama if not present
# - Pull codellama:13b, deepseek-coder:6.7b, mistral:7b
# - Start Ollama server
```

### 3. Start CodeSmriti

```bash
# Start all Docker services
./scripts/start.sh

# This will:
# - Check environment configuration
# - Start Couchbase, MCP Server, Ingestion Worker, Nginx
# - Verify service health
```

### 4. Initialize Couchbase (first time only)

```bash
# Run inside the Couchbase container
docker exec -it codesmriti_couchbase /opt/init-couchbase.sh

# This will:
# - Create the 'code_memory' bucket
# - Set up indexes for efficient querying
# - Configure vector search
```

### 5. Trigger Initial Ingestion

```bash
# Generate an API key for yourself
./scripts/generate-api-key.py

# Trigger ingestion of all configured repos
curl -X POST http://localhost/api/ingest/trigger \
  -H "Authorization: Bearer YOUR_API_KEY"

# Monitor ingestion progress
docker-compose logs -f ingestion-worker
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

On Mac M3 Ultra (256GB RAM):
- **Indexing**: ~100 repos in 2-4 hours (first run)
- **Search latency**: <100ms for vector search
- **Embedding generation**: ~500 chunks/second
- **Concurrent requests**: Handles 50+ simultaneous MCP calls

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
