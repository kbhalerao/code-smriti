# CodeSmriti Installation Guide

Quick installation guide for setting up CodeSmriti on a fresh M3 Mac.

## Prerequisites

- Fresh M3 Mac
- Git installed
- This repository cloned

## Installation Methods

### Option 1: Automated Installation with GUI (Recommended for local use)

Run the quick install script that automates everything:

```bash
cd code-smriti
./quick-install.sh
```

This script will:
1. Install Homebrew (if not present)
2. Install Docker Desktop (GUI)
3. Install Ollama
4. Download AI models (~15GB)
5. Configure CodeSmriti
6. Start all services
7. Initialize database

**Estimated time: 30-60 minutes** (depending on internet speed for model downloads)

The script will pause and prompt you to:
- Start Docker Desktop manually (GUI app)
- Edit `.env` file with your credentials
- Confirm when services are ready

### Option 2: Headless Installation via SSH (No GUI required)

For remote servers or SSH-only access:

```bash
cd code-smriti
./quick-install-headless.sh
```

This uses **Colima** (CLI Docker runtime) instead of Docker Desktop:
- No GUI required
- Perfect for SSH access
- Same functionality as Docker Desktop
- Lightweight and fast

**Commands for Colima:**
```bash
colima status    # Check if running
colima stop      # Stop Docker
colima start     # Start Docker
```

### Option 3: Manual Installation

If you prefer to install components yourself:

#### Step 1: Install Dependencies

**Option A: With Docker Desktop (GUI)**
```bash
# Install Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Docker Desktop
brew install --cask docker

# Start Docker Desktop from Applications
# Wait for Docker to be running (whale icon in menu bar)

# Install Ollama
brew install ollama
brew services start ollama
```

**Option B: With Colima (Headless/SSH)**
```bash
# Install Homebrew
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# Install Docker CLI and Colima
brew install docker docker-compose colima

# Start Colima with appropriate resources
colima start --cpu 4 --memory 8 --disk 100 --arch aarch64

# Install Ollama
brew install ollama
brew services start ollama
```

#### Step 2: Pull AI Models

```bash
# Essential embedding model
ollama pull nomic-embed-text

# Code understanding models
ollama pull codellama:13b
ollama pull deepseek-coder:6.7b
```

**Note:** This downloads ~15GB of models. On a typical broadband connection, expect 10-30 minutes.

#### Step 3: Configure CodeSmriti

```bash
# Create environment configuration
cp .env.example .env

# Generate secure JWT secret
JWT_SECRET=$(openssl rand -hex 32)

# Edit .env and set:
# - COUCHBASE_PASSWORD (strong password)
# - JWT_SECRET (paste generated secret above)
# - GITHUB_TOKEN (your GitHub personal access token)
# - GITHUB_REPOS (optional: repos to index)
nano .env
```

#### Step 4: Start Services

```bash
# Start all Docker containers
docker-compose up -d

# Wait for services to be healthy (2-3 minutes)
docker-compose logs -f
```

#### Step 5: Initialize Database

```bash
# Run inside Couchbase container (first time only)
docker exec -it codesmriti_couchbase /opt/init-couchbase.sh
```

## Verification

Check that all services are running:

```bash
# Check Docker containers
docker-compose ps

# Should show all services as "Up" and "healthy"

# Check individual services
curl http://localhost:8091/pools              # Couchbase
curl http://localhost:8080/health             # MCP Server
curl http://localhost/health                  # Nginx
curl http://localhost:11434/api/version       # Ollama
```

## Post-Installation

### 1. Generate API Key

```bash
python3 scripts/generate-api-key.py
```

Follow the prompts to create an API key for yourself.

### 2. Trigger Repository Ingestion

```bash
# Replace YOUR_API_KEY with the key from step 1
curl -X POST http://localhost/api/ingest/trigger \
  -H "Authorization: Bearer YOUR_API_KEY"
```

### 3. Monitor Progress

```bash
# Watch ingestion logs
docker-compose logs -f ingestion-worker

# The first ingestion may take hours depending on repo sizes
```

### 4. Connect to Claude Desktop

See [docs/MCP-USAGE.md](docs/MCP-USAGE.md) for detailed MCP client configuration.

## System Requirements

### Minimum (for testing)
- 16GB RAM
- 50GB free disk space
- Modern Mac (M1 or newer)

### Recommended (for ~100 repos)
- 32GB+ RAM
- 200GB+ free disk space
- Mac M3 or newer
- Fast internet for initial model downloads

### Optimal (as designed)
- Mac Studio M3 Ultra
- 256GB RAM
- 2TB SSD
- Handles ~100 large repositories with fast search

## Troubleshooting

### Docker Desktop won't start
1. Check System Requirements in Docker Desktop
2. Ensure at least 10GB free disk space
3. Restart Mac and try again

### Ollama models fail to download
```bash
# Check Ollama is running
curl http://localhost:11434/api/version

# If not running
brew services restart ollama

# Or start manually
ollama serve
```

### Couchbase initialization fails
```bash
# Check Couchbase logs
docker logs codesmriti_couchbase

# Reset if needed (WARNING: deletes data)
docker-compose down -v
docker-compose up -d couchbase
sleep 30
docker exec -it codesmriti_couchbase /opt/init-couchbase.sh
```

### MCP Server can't connect to Ollama
Ensure Ollama is running natively (not in Docker):
```bash
curl http://localhost:11434/api/version
```

From inside Docker containers, Ollama is accessible at:
`http://host.docker.internal:11434`

### Port conflicts
If ports 80, 8080, 8091, or 11434 are already in use:

```bash
# Check what's using ports
lsof -i :80
lsof -i :8080
lsof -i :8091
lsof -i :11434

# Stop conflicting services or edit docker-compose.yml
# to use different ports
```

## Useful Commands

```bash
# View all logs
docker-compose logs -f

# View specific service logs
docker-compose logs -f mcp-server
docker-compose logs -f ingestion-worker

# Restart all services
docker-compose restart

# Restart specific service
docker-compose restart mcp-server

# Stop everything
docker-compose down

# Stop and remove all data (WARNING: deletes everything)
docker-compose down -v

# Check service status
docker-compose ps

# Enter a container shell
docker exec -it codesmriti_mcp_server /bin/bash
```

## Getting Help

1. Check logs: `docker-compose logs -f`
2. Review [README.md](README.md) for architecture details
3. Check [docs/](docs/) folder for additional documentation
4. Verify all environment variables in `.env`

## Uninstallation

To completely remove CodeSmriti:

```bash
# Stop and remove containers, networks, volumes
docker-compose down -v

# Remove Docker images (optional)
docker image prune -a

# Remove Ollama models (optional)
ollama rm nomic-embed-text
ollama rm codellama:13b
ollama rm deepseek-coder:6.7b

# Uninstall Ollama (optional)
brew uninstall ollama

# Remove cloned repositories
rm -rf repos/
```

---

**Ready to never forget how you solved that problem last time!** 🧠
