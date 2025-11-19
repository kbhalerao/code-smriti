# 0. Setup

Get CodeSmriti running on your system.

## Quick Start

```bash
./install.sh
```

This will:
1. Install Python dependencies
2. Start Couchbase via Docker
3. Initialize the database
4. Verify everything works

## Scripts

- **install.sh** - Full installation (interactive)
- **install-headless.sh** - Headless server installation
- **ssl/setup-ssl.sh** - Configure SSL for production
- **ssl/renew-ssl.sh** - Renew SSL certificates
- **docker/init-couchbase.sh** - Initialize Couchbase database

## Prerequisites

- Docker Desktop (or Docker Engine on Linux)
- Python 3.11+
- 16GB RAM recommended
- 50GB disk space for repos

## Verification

After installation, verify everything works:

```bash
# Check Couchbase is running
docker ps | grep couchbase

# Check database is accessible
curl -u Administrator:password123 http://localhost:8091/pools/default
```

## Troubleshooting

**Docker not running:**
```bash
# macOS
open -a Docker

# Linux
sudo systemctl start docker
```

**Port conflicts (8091, 8094):**
```bash
# Check what's using the port
lsof -i :8091
```

## Next Step

→ **[1-config](../1-config/README.md)** - Configure what to index
