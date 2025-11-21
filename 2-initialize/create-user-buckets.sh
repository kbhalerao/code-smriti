#!/bin/bash
#
# Create Couchbase buckets for multitenancy
# - users: User credentials and GitHub PAT storage (privileged)
# - ingestion_jobs: Job queue and progress tracking
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Load environment variables
if [ -f "$PROJECT_ROOT/.env" ]; then
    source "$PROJECT_ROOT/.env"
fi

CB_HOST="${COUCHBASE_HOST:-localhost}"
CB_PORT="${COUCHBASE_PORT:-8091}"
CB_USER="${COUCHBASE_USER:-Administrator}"
CB_PASSWORD="${COUCHBASE_PASSWORD:-password123}"

echo "========================================"
echo "Creating Multitenancy Buckets"
echo "========================================"
echo ""

# Function to create bucket
create_bucket() {
    local bucket_name=$1
    local ram_quota=$2

    echo "Creating bucket: $bucket_name ($ram_quota MB RAM)..."

    # Check if bucket already exists
    if docker exec codesmriti_couchbase \
        couchbase-cli bucket-list \
        -c $CB_HOST:$CB_PORT \
        -u $CB_USER \
        -p $CB_PASSWORD \
        | grep -q "^$bucket_name$"; then
        echo "   ⚠ Bucket '$bucket_name' already exists, skipping..."
        return 0
    fi

    # Create bucket
    docker exec codesmriti_couchbase \
        couchbase-cli bucket-create \
        -c $CB_HOST:$CB_PORT \
        -u $CB_USER \
        -p $CB_PASSWORD \
        --bucket $bucket_name \
        --bucket-type couchbase \
        --bucket-ramsize $ram_quota \
        --bucket-replica 0 \
        --enable-flush 1 \
        --wait

    if [ $? -eq 0 ]; then
        echo "   ✓ Bucket '$bucket_name' created successfully"
    else
        echo "   ✗ Failed to create bucket '$bucket_name'"
        return 1
    fi
}

# Create users bucket (privileged - stores credentials and GitHub PATs)
create_bucket "users" 256

# Create ingestion_jobs bucket (job queue and progress tracking)
create_bucket "ingestion_jobs" 128

echo ""
echo "========================================"
echo "✓ Bucket creation complete"
echo "========================================"
echo ""
echo "Next steps:"
echo "  1. Run: ./create-database-indexes"
echo "  2. Run: ./migrate-add-user-id.js"
echo ""
