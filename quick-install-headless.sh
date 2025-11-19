#!/bin/bash

# CodeSmriti Headless Install Script for SSH/Remote M3 Mac
# Installs Docker via CLI without requiring GUI interaction
# Prerequisites: Only git and SSH access

set -e

echo "========================================="
echo "  CodeSmriti Headless Install"
echo "  Forever Memory MCP System"
echo "========================================="
echo ""
echo "This script installs everything via CLI:"
echo "  • Homebrew (if needed)"
echo "  • Docker via Colima (CLI Docker runtime)"
echo "  • Ollama + AI models"
echo "  • CodeSmriti services"
echo ""
echo "Estimated time: 30-60 minutes (depending on downloads)"
echo ""

# Ask for confirmation
read -p "Continue with headless installation? (y/n) " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Installation cancelled."
    exit 1
fi

echo ""
echo "=== Step 1: Installing Homebrew ==="
if ! command -v brew &> /dev/null; then
    echo "Installing Homebrew..."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

    # Add Homebrew to PATH for Apple Silicon
    if [[ $(uname -m) == 'arm64' ]]; then
        echo 'eval "$(/opt/homebrew/bin/brew shellenv)"' >> ~/.zprofile
        eval "$(/opt/homebrew/bin/brew shellenv)"
    fi
    echo "✓ Homebrew installed"
else
    echo "✓ Homebrew already installed"
fi

echo ""
echo "=== Step 2: Installing Docker via Colima (headless) ==="

# Check if Docker Desktop is already installed
if command -v docker &> /dev/null && docker info > /dev/null 2>&1; then
    echo "✓ Docker is already installed and running"
else
    echo "Installing Docker and Colima..."

    # Install Docker CLI and Colima
    brew install docker docker-compose colima

    echo "✓ Docker CLI installed"

    # Start Colima with appropriate resources
    echo ""
    echo "Starting Colima Docker runtime..."
    echo "Configuring for CodeSmriti workload:"
    echo "  - CPUs: 4"
    echo "  - Memory: 8GB (adjust based on your system)"
    echo "  - Disk: 100GB"
    echo ""

    # Start Colima with configuration
    colima start --cpu 4 --memory 8 --disk 100 --arch aarch64 --vm-type vz --mount-type virtiofs

    echo "Waiting for Docker to be ready..."
    for i in {1..30}; do
        if docker info > /dev/null 2>&1; then
            echo "✓ Docker (Colima) is running"
            break
        fi
        if [ $i -eq 30 ]; then
            echo "⚠️  Docker didn't start in time"
            echo "Try manually: colima start"
            exit 1
        fi
        sleep 2
    done
fi

echo ""
echo "=== Step 3: Installing Ollama ==="
if ! command -v ollama &> /dev/null; then
    echo "Installing Ollama..."
    brew install ollama
    echo "✓ Ollama installed"
else
    echo "✓ Ollama already installed"
fi

# Start Ollama service
echo "Starting Ollama service..."
brew services start ollama

# Wait for Ollama to be ready
echo "Waiting for Ollama to start..."
for i in {1..30}; do
    if curl -s http://localhost:11434/api/version > /dev/null 2>&1; then
        echo "✓ Ollama is running"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "⚠️  Ollama didn't start automatically"
        echo "Starting Ollama manually in background..."
        nohup ollama serve > /tmp/ollama.log 2>&1 &
        sleep 5
        if curl -s http://localhost:11434/api/version > /dev/null 2>&1; then
            echo "✓ Ollama is running"
        else
            echo "⚠️  Could not start Ollama. Check /tmp/ollama.log"
            exit 1
        fi
    fi
    sleep 2
done

echo ""
echo "=== Step 4: Pulling AI Models ==="
echo "This will download ~15GB of models. Estimated time: 10-30 minutes"
echo ""

# Pull essential models
echo "[1/3] Pulling nomic-embed-text (embedding model)..."
ollama pull nomic-embed-text

echo "[2/3] Pulling codellama:13b (code understanding)..."
ollama pull codellama:13b

echo "[3/3] Pulling deepseek-coder:6.7b (fast code analysis)..."
ollama pull deepseek-coder:6.7b

echo "✓ AI models ready"

echo ""
echo "=== Step 5: Configuring CodeSmriti ==="

# Create .env file if it doesn't exist
if [ ! -f ".env" ]; then
    echo "Creating .env configuration file..."
    cp .env.example .env

    # Generate secure JWT secret
    JWT_SECRET=$(openssl rand -hex 32)

    # Update .env with generated JWT secret
    if [[ "$OSTYPE" == "darwin"* ]]; then
        sed -i '' "s/your-super-secret-jwt-key-change-this-in-production/$JWT_SECRET/" .env
    else
        sed -i "s/your-super-secret-jwt-key-change-this-in-production/$JWT_SECRET/" .env
    fi

    echo "✓ .env file created with secure JWT secret"
    echo ""
    echo "⚠️  You need to update the following in .env:"
    echo "  1. COUCHBASE_PASSWORD - Set a strong password"
    echo "  2. GITHUB_TOKEN - Your GitHub personal access token"
    echo "  3. GITHUB_REPOS - Comma-separated list of repos to index (optional)"
    echo ""

    # For headless, provide instructions rather than opening editor
    echo "Edit .env now? (y/n) or skip and edit later"
    read -p "Edit now? (y/n) " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        ${EDITOR:-nano} .env
        echo ""
        read -p "Press Enter once you've updated .env..."
    else
        echo ""
        echo "⚠️  Remember to edit .env before starting services:"
        echo "   nano .env"
        echo ""
        echo "Required changes:"
        echo "  - COUCHBASE_PASSWORD=YourSecurePassword"
        echo "  - GITHUB_TOKEN=ghp_your_token_here"
        echo ""
        read -p "Press Enter to continue (you can edit .env later)..."
    fi
else
    echo "✓ .env file already exists"
fi

# Verify required variables are set
source .env
ENV_NEEDS_UPDATE=false

if [ -z "$COUCHBASE_PASSWORD" ] || [ "$COUCHBASE_PASSWORD" == "ChangeThisSecurePassword123!" ]; then
    echo "⚠️  COUCHBASE_PASSWORD not set properly in .env"
    ENV_NEEDS_UPDATE=true
fi

if [ -z "$GITHUB_TOKEN" ] || [ "$GITHUB_TOKEN" == "ghp_your_github_token_here" ]; then
    echo "⚠️  GITHUB_TOKEN not set in .env"
    echo "    (You can set this later, but ingestion won't work until configured)"
fi

if [ "$ENV_NEEDS_UPDATE" = true ]; then
    echo ""
    echo "Please update .env before continuing:"
    echo "  nano .env"
    echo ""
    read -p "Press Enter once .env is updated..."
fi

echo ""
echo "=== Step 6: Starting CodeSmriti Services ==="
echo "Starting Docker containers..."
docker-compose up -d

echo ""
echo "Waiting for services to initialize (this may take 2-3 minutes)..."
sleep 20

# Wait for Couchbase to be healthy
echo "Waiting for Couchbase to be ready..."
for i in {1..60}; do
    if curl -s http://localhost:8091/pools > /dev/null 2>&1; then
        echo "✓ Couchbase is ready"
        break
    fi
    if [ $i -eq 60 ]; then
        echo "⚠️  Couchbase is taking longer than expected"
        echo "Check logs with: docker-compose logs couchbase"
        exit 1
    fi
    echo -n "."
    sleep 3
done
echo ""

# Wait for MCP Server
echo "Waiting for MCP Server..."
for i in {1..30}; do
    if curl -s http://localhost:8080/health > /dev/null 2>&1; then
        echo "✓ MCP Server is ready"
        break
    fi
    if [ $i -eq 30 ]; then
        echo "⚠️  MCP Server is not responding"
        echo "Check logs with: docker-compose logs mcp-server"
    fi
    sleep 2
done

echo ""
echo "=== Step 7: Initializing Couchbase Database ==="
echo "Creating buckets and indexes..."
docker exec codesmriti_couchbase /opt/init-couchbase.sh

echo ""
echo "========================================="
echo "  ✓ CodeSmriti Installation Complete!"
echo "========================================="
echo ""
echo "Services Running:"
echo "  • MCP Server:        http://localhost:8080"
echo "  • API Gateway:       http://localhost"
echo "  • Couchbase UI:      http://localhost:8091"
echo "  • Ollama API:        http://localhost:11434"
echo ""
echo "Docker Runtime: Colima (headless)"
echo "  • Status:            colima status"
echo "  • Stop:              colima stop"
echo "  • Start:             colima start"
echo ""
echo "Next Steps:"
echo ""
echo "1. Generate an API key:"
echo "   python3 scripts/generate-api-key.py"
echo ""
echo "2. Trigger initial repository ingestion:"
echo "   curl -X POST http://localhost/api/ingest/trigger \\"
echo "     -H \"Authorization: Bearer YOUR_API_KEY\""
echo ""
echo "3. Connect to Claude Desktop (if using SSH tunnel):"
echo "   See docs/MCP-USAGE.md for instructions"
echo ""
echo "4. Monitor ingestion progress:"
echo "   docker-compose logs -f ingestion-worker"
echo ""
echo "Useful Commands:"
echo "  • View all logs:     docker-compose logs -f"
echo "  • Stop services:     docker-compose down"
echo "  • Restart services:  docker-compose restart"
echo "  • Colima status:     colima status"
echo ""
echo "SSH Tunneling (for remote access):"
echo "  ssh -L 8080:localhost:8080 -L 8091:localhost:8091 user@remote-mac"
echo ""
echo "Documentation: README.md and docs/ folder"
echo ""
echo "🧠 Forever Memory System is ready!"
echo ""
