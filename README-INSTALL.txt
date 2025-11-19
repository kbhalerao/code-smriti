╔══════════════════════════════════════════════════════════════╗
║                                                              ║
║              CodeSmriti - Quick Installation                 ║
║                   Forever Memory MCP System                  ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝

Fresh M3 Mac Installation
══════════════════════════════════════════════════════════════

Prerequisites: Just git and this repo cloned.

Run this ONE command:

    ./quick-install.sh

What it does:
  ✓ Installs Homebrew (if needed)
  ✓ Installs Docker Desktop
  ✓ Installs Ollama
  ✓ Downloads AI models (~15GB)
  ✓ Configures CodeSmriti
  ✓ Starts all services
  ✓ Initializes database

Time: 30-60 minutes (mostly downloads)

The script will pause and ask you to:
  • Start Docker Desktop manually (GUI app)
  • Edit .env file with your credentials
  • Confirm when ready to proceed

══════════════════════════════════════════════════════════════

Already have Docker & Ollama?
══════════════════════════════════════════════════════════════

Quick manual setup:

1. Configure:
   cp .env.example .env
   nano .env    # Set passwords and tokens

2. Pull models:
   ollama pull nomic-embed-text
   ollama pull codellama:13b

3. Start:
   docker-compose up -d

4. Initialize:
   docker exec -it codesmriti_couchbase /opt/init-couchbase.sh

5. Use:
   python3 scripts/generate-api-key.py

══════════════════════════════════════════════════════════════

Documentation
══════════════════════════════════════════════════════════════

INSTALL.md     - Detailed installation guide with troubleshooting
QUICKSTART.md  - One-page reference for daily operations
README.md      - Full project documentation and architecture

══════════════════════════════════════════════════════════════

After Installation
══════════════════════════════════════════════════════════════

Services will be running at:

  • MCP Server:        http://localhost:8080
  • API Gateway:       http://localhost
  • Couchbase UI:      http://localhost:8091
  • Ollama API:        http://localhost:11434

Next steps:

1. Generate API key:
   python3 scripts/generate-api-key.py

2. Trigger ingestion:
   curl -X POST http://localhost/api/ingest/trigger \
     -H "Authorization: Bearer YOUR_API_KEY"

3. Connect to Claude Desktop:
   See docs/MCP-USAGE.md

══════════════════════════════════════════════════════════════

Quick Commands
══════════════════════════════════════════════════════════════

Start:        docker-compose up -d
Stop:         docker-compose down
Logs:         docker-compose logs -f
Status:       docker-compose ps
Restart:      docker-compose restart

══════════════════════════════════════════════════════════════

Need Help?
══════════════════════════════════════════════════════════════

1. Check logs:    docker-compose logs -f
2. See INSTALL.md for troubleshooting
3. Review .env configuration
4. Verify services: docker-compose ps

══════════════════════════════════════════════════════════════
