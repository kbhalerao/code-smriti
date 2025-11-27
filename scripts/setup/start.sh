#!/bin/bash

# CodeSmriti Startup Script
# Boots the entire forever memory system
# Smriti (स्मृति): Sanskrit for "memory, remembrance"

set -e

echo "========================================="
echo "  CodeSmriti - Forever Memory System"
echo "========================================="
echo ""

# Check if .env file exists
if [ ! -f ".env" ]; then
    echo "⚠️  .env file not found"
    echo "Creating .env from .env.example..."
    cp .env.example .env
    echo ""
    echo "Please edit .env and set the following:"
    echo "  - COUCHBASE_PASSWORD"
    echo "  - JWT_SECRET (run: openssl rand -hex 32)"
    echo "  - GITHUB_TOKEN"
    echo "  - GITHUB_REPOS"
    echo ""
    echo "Then run this script again."
    exit 1
fi

# Load environment variables
source .env

# Check required variables
REQUIRED_VARS=("COUCHBASE_PASSWORD" "JWT_SECRET" "GITHUB_TOKEN")
MISSING_VARS=()

for var in "${REQUIRED_VARS[@]}"; do
    if [ -z "${!var}" ]; then
        MISSING_VARS+=("$var")
    fi
done

if [ ${#MISSING_VARS[@]} -gt 0 ]; then
    echo "⚠️  Missing required environment variables:"
    for var in "${MISSING_VARS[@]}"; do
        echo "    - $var"
    done
    echo ""
    echo "Please update .env and run again."
    exit 1
fi

echo "✓ Environment configured"
echo ""

# Check if Ollama is running
echo "Checking Ollama..."
if ! curl -s http://localhost:11434/api/version > /dev/null 2>&1; then
    echo "⚠️  Ollama is not running"
    echo ""
    echo "Please start Ollama:"
    echo "  ollama serve"
    echo ""
    echo "Then run ./scripts/ollama-setup.sh to pull models"
    echo ""
    exit 1
fi

echo "✓ Ollama is running"
echo ""

# Start Docker Compose services
echo "Starting Docker services..."
docker-compose up -d

echo ""
echo "Waiting for services to be healthy..."
sleep 10

# Check service health
echo ""
echo "Checking service health..."

# Check Couchbase
if curl -s http://localhost:8091/pools > /dev/null 2>&1; then
    echo "✓ Couchbase is healthy"
else
    echo "⚠️  Couchbase is not responding"
fi

# Check MCP Server
if curl -s http://localhost:8080/health > /dev/null 2>&1; then
    echo "✓ MCP Server is healthy"
else
    echo "⚠️  MCP Server is not responding (may still be starting...)"
fi

# Check Nginx
if curl -s http://localhost/health > /dev/null 2>&1; then
    echo "✓ Nginx is healthy"
else
    echo "⚠️  Nginx is not responding"
fi

echo ""
echo "========================================="
echo "  CodeSmriti is running!"
echo "========================================="
echo ""
echo "Services:"
echo "  • MCP Server:        http://localhost:8080"
echo "  • API Gateway:       http://localhost"
echo "  • Couchbase UI:      http://localhost:8091"
echo ""
echo "Ollama (running natively):"
echo "  • Ollama API:        http://localhost:11434"
echo ""
echo "To initialize Couchbase (first time):"
echo "  docker exec -it codesmriti_couchbase /opt/init-couchbase.sh"
echo ""
echo "To view logs:"
echo "  docker-compose logs -f"
echo ""
echo "To stop:"
echo "  docker-compose down"
echo ""
