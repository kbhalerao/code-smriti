#!/bin/bash
# Run ingestion worker manually to index repositories
# Usage: ./run-ingestion.sh [follow]

set -e

echo "Starting ingestion worker..."

if [ "$1" == "follow" ] || [ "$1" == "-f" ]; then
    # Run and follow logs
    docker-compose --profile manual run --rm ingestion-worker
else
    # Run in background
    docker-compose --profile manual up -d ingestion-worker
    echo ""
    echo "Ingestion worker started in background."
    echo ""
    echo "To follow logs:    docker-compose logs -f ingestion-worker"
    echo "To check status:   docker-compose ps"
    echo "To stop:           docker-compose stop ingestion-worker"
fi
