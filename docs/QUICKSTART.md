# CodeSmriti Quick Start Guide

One-page reference for getting started with CodeSmriti after installation.

## Installation

On a fresh M3 Mac with Git:

```bash
cd code-smriti
./quick-install.sh
```

This takes 30-60 minutes and handles everything automatically.

## Daily Operations

### Start CodeSmriti

```bash
docker-compose up -d
```

Check all services are running:
```bash
docker-compose ps
```

### Stop CodeSmriti

```bash
docker-compose down
```

### View Logs

```bash
# All services
docker-compose logs -f

# Specific service
docker-compose logs -f mcp-server
docker-compose logs -f ingestion-worker
```

### Restart a Service

```bash
docker-compose restart mcp-server
```

## Common Tasks

### Generate API Key

```bash
python3 scripts/generate-api-key.py
```

### Trigger Manual Ingestion

```bash
curl -X POST http://localhost/api/ingest/trigger \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Re-index Specific Repository

```bash
curl -X POST "http://localhost/api/ingest/trigger?repo=owner/repo" \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### Add Repository to Ingestion List

Edit `.env` and add to `GITHUB_REPOS`:
```bash
GITHUB_REPOS=owner/repo1,owner/repo2,owner/new-repo
```

Then restart:
```bash
docker-compose restart ingestion-worker
```

### Check System Status

```bash
# Couchbase UI (username: Administrator, password from .env)
open http://localhost:8091

# MCP Server health
curl http://localhost:8080/health

# API Gateway
curl http://localhost/health

# Ollama
curl http://localhost:11434/api/version
```

## MCP Integration

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "codesmriti": {
      "url": "http://localhost:8080/sse",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

Restart Claude Desktop.

### VSCode

Install MCP extension and configure similarly.

## Troubleshooting

### Service Won't Start

```bash
# Check logs
docker-compose logs service-name

# Reset service
docker-compose down
docker-compose up -d
```

### Couchbase Issues

```bash
# Access Couchbase UI
open http://localhost:8091

# Reset bucket (WARNING: deletes data)
docker exec -it codesmriti_couchbase /opt/init-couchbase.sh
```

### Ollama Connection Failed

```bash
# Check Ollama is running
curl http://localhost:11434/api/version

# If not, start it
brew services start ollama
# or
ollama serve
```

### Out of Disk Space

```bash
# Clean old Docker images and volumes
docker system prune -a --volumes

# Clean Ollama models you don't need
ollama list
ollama rm model-name
```

## URLs Quick Reference

| Service | URL | Credentials |
|---------|-----|-------------|
| **Couchbase UI** | http://localhost:8091 | Admin / .env password |
| **MCP Server** | http://localhost:8080 | Bearer token |
| **API Gateway** | http://localhost | Bearer token |
| **Ollama API** | http://localhost:11434 | None |

## Service Ports

- `80/443` - Nginx API Gateway
- `8080` - MCP Server (HTTP/SSE)
- `8091-8097` - Couchbase Web Console and APIs
- `11434` - Ollama API (native, not Docker)

## Environment Variables

Edit `.env` to configure:

```bash
# Database
COUCHBASE_PASSWORD=your_secure_password

# Authentication
JWT_SECRET=your_jwt_secret_from_openssl

# GitHub Integration
GITHUB_TOKEN=ghp_your_token
GITHUB_REPOS=owner/repo1,owner/repo2

# Optional
LOG_LEVEL=INFO
RATE_LIMIT_PER_MINUTE=60
```

After changing `.env`:
```bash
docker-compose down
docker-compose up -d
```

## Performance Tuning

### For Limited RAM (16-32GB)

Use lighter models:
```bash
ollama pull deepseek-coder:1.3b
ollama pull qwen2:1.5b
```

Limit Docker resources in Docker Desktop preferences.

### For Maximum Performance (128GB+)

Pull larger models:
```bash
ollama pull codellama:70b
ollama pull deepseek-coder:33b
```

## Getting Help

1. **Check logs**: `docker-compose logs -f`
2. **Service status**: `docker-compose ps`
3. **Review docs**: `README.md`, `INSTALL.md`, `docs/`
4. **Verify config**: Check `.env` file
5. **Reset**: `docker-compose down -v` then reinstall

## Data Locations

- **Couchbase data**: Docker volume `couchbase_data`
- **Model cache**: Docker volume `model_cache`
- **Cloned repos**: `./repos/` directory
- **Logs**: `docker-compose logs`

## Backup & Restore

### Backup

```bash
# Backup Couchbase bucket
docker exec codesmriti_couchbase cbbackup \
  http://localhost:8091 /backup -u Administrator -p YOUR_PASSWORD

# Backup .env configuration
cp .env .env.backup
```

### Restore

```bash
# Restore Couchbase
docker exec codesmriti_couchbase cbrestore \
  /backup http://localhost:8091 -u Administrator -p YOUR_PASSWORD
```

---

**Need more help?** See full documentation in `README.md` and `INSTALL.md`
