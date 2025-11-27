#!/bin/bash

# Couchbase Initialization Script
# Sets up cluster, bucket, indexes, and users for CodeSmriti
# Smriti (स्मृति): Sanskrit for "memory, remembrance"

set -e

echo "=== CodeSmriti Couchbase Initialization ==="

# Wait for Couchbase to be ready
echo "Waiting for Couchbase to start..."
until curl -s http://localhost:8091/pools > /dev/null 2>&1; do
    echo "  Couchbase not ready, waiting..."
    sleep 5
done
echo "✓ Couchbase is running"

# Configuration from environment or defaults
CB_HOST=${CB_HOST:-localhost}
CB_PORT=${CB_PORT:-8091}
CB_USERNAME=${COUCHBASE_USERNAME:-Administrator}
CB_PASSWORD=${COUCHBASE_PASSWORD}
CB_BUCKET=${COUCHBASE_BUCKET:-code_memory}

if [ -z "$CB_PASSWORD" ]; then
    echo "ERROR: COUCHBASE_PASSWORD must be set"
    exit 1
fi

# Check if cluster is already initialized
if curl -s -u "$CB_USERNAME:$CB_PASSWORD" http://$CB_HOST:$CB_PORT/pools/default > /dev/null 2>&1; then
    echo "✓ Cluster already initialized"
else
    echo "Initializing Couchbase cluster..."
    curl -s -X POST http://$CB_HOST:$CB_PORT/pools/default \
        -d "memoryQuota=4096" \
        -d "indexMemoryQuota=2048" \
        -d "ftsMemoryQuota=2048" \
        -d "eventingMemoryQuota=256" \
        -d "analyticsMemoryQuota=1024"

    echo "Setting up admin credentials..."
    curl -s -X POST http://$CB_HOST:$CB_PORT/settings/web \
        -d "username=$CB_USERNAME" \
        -d "password=$CB_PASSWORD" \
        -d "port=8091"

    echo "✓ Cluster initialized"
fi

# Wait a bit for services to stabilize
sleep 5

# Create bucket if it doesn't exist
echo "Creating bucket '$CB_BUCKET'..."
curl -s -X POST -u "$CB_USERNAME:$CB_PASSWORD" http://$CB_HOST:$CB_PORT/pools/default/buckets \
    -d "name=$CB_BUCKET" \
    -d "bucketType=couchbase" \
    -d "ramQuota=2048" \
    -d "replicaNumber=0" \
    -d "flushEnabled=0" \
    -d "conflictResolutionType=seqno" \
    || echo "  Bucket may already exist"

echo "✓ Bucket '$CB_BUCKET' ready"

# Wait for bucket to be ready
echo "Waiting for bucket to be ready..."
until curl -s -u "$CB_USERNAME:$CB_PASSWORD" http://$CB_HOST:$CB_PORT/pools/default/buckets/$CB_BUCKET > /dev/null 2>&1; do
    sleep 2
done
sleep 5
echo "✓ Bucket is ready"

# Create primary index for basic queries
echo "Creating primary index..."
curl -s -u "$CB_USERNAME:$CB_PASSWORD" http://$CB_HOST:$CB_PORT/query/service \
    -d "statement=CREATE PRIMARY INDEX ON \`$CB_BUCKET\` USING GSI" \
    || echo "  Primary index may already exist"

# Create indexes for efficient querying
echo "Creating metadata indexes..."

# Index by chunk type
curl -s -u "$CB_USERNAME:$CB_PASSWORD" http://$CB_HOST:$CB_PORT/query/service \
    -d "statement=CREATE INDEX idx_chunk_type ON \`$CB_BUCKET\`(type) WHERE type IS NOT NULL USING GSI" \
    || echo "  idx_chunk_type may already exist"

# Index by repository
curl -s -u "$CB_USERNAME:$CB_PASSWORD" http://$CB_HOST:$CB_PORT/query/service \
    -d "statement=CREATE INDEX idx_repo ON \`$CB_BUCKET\`(repo_id) WHERE repo_id IS NOT NULL USING GSI" \
    || echo "  idx_repo may already exist"

# Index by language
curl -s -u "$CB_USERNAME:$CB_PASSWORD" http://$CB_HOST:$CB_PORT/query/service \
    -d "statement=CREATE INDEX idx_language ON \`$CB_BUCKET\`(metadata.language) WHERE metadata.language IS NOT NULL USING GSI" \
    || echo "  idx_language may already exist"

# Compound index for common queries
curl -s -u "$CB_USERNAME:$CB_PASSWORD" http://$CB_HOST:$CB_PORT/query/service \
    -d "statement=CREATE INDEX idx_repo_type_lang ON \`$CB_BUCKET\`(repo_id, type, metadata.language) WHERE repo_id IS NOT NULL AND type IS NOT NULL USING GSI" \
    || echo "  idx_repo_type_lang may already exist"

# Index for hashtags (array indexing)
curl -s -u "$CB_USERNAME:$CB_PASSWORD" http://$CB_HOST:$CB_PORT/query/service \
    -d "statement=CREATE INDEX idx_hashtags ON \`$CB_BUCKET\`(DISTINCT ARRAY tag FOR tag IN hashtags END) WHERE hashtags IS NOT NULL USING GSI" \
    || echo "  idx_hashtags may already exist"

echo "✓ Metadata indexes created"

# Note: Vector search indexes are created via FTS UI or API
# For now, we'll document the FTS index creation process
cat << 'EOF' > /tmp/vector-search-index.json
{
  "type": "fulltext-index",
  "name": "vector_search_idx",
  "sourceType": "couchbase",
  "sourceName": "code_memory",
  "planParams": {
    "maxPartitionsPerPIndex": 1024,
    "indexPartitions": 1
  },
  "params": {
    "doc_config": {
      "docid_prefix_delim": "",
      "docid_regexp": "",
      "mode": "type_field",
      "type_field": "type"
    },
    "mapping": {
      "default_mapping": {
        "enabled": false
      },
      "type_field": "_type",
      "types": {
        "code_chunk": {
          "enabled": true,
          "properties": {
            "embedding": {
              "enabled": true,
              "dynamic": false,
              "fields": [
                {
                  "dims": 768,
                  "index": true,
                  "name": "embedding",
                  "similarity": "dot_product",
                  "type": "vector",
                  "vector_index_optimized_for": "recall"
                }
              ]
            },
            "code_text": {
              "enabled": true,
              "dynamic": false,
              "fields": [
                {
                  "analyzer": "standard",
                  "index": true,
                  "name": "code_text",
                  "store": true,
                  "type": "text"
                }
              ]
            }
          }
        },
        "note": {
          "enabled": true,
          "properties": {
            "embedding": {
              "enabled": true,
              "dynamic": false,
              "fields": [
                {
                  "dims": 768,
                  "index": true,
                  "name": "embedding",
                  "similarity": "dot_product",
                  "type": "vector",
                  "vector_index_optimized_for": "recall"
                }
              ]
            }
          }
        }
      }
    },
    "store": {
      "indexType": "scorch"
    }
  }
}
EOF

echo ""
echo "=== Vector Search Index Setup ==="
echo "A vector search index definition has been created at /tmp/vector-search-index.json"
echo "To create the vector search index, run:"
echo ""
echo "  curl -X PUT -u $CB_USERNAME:PASSWORD \\"
echo "    http://$CB_HOST:8094/api/index/vector_search_idx \\"
echo "    -H 'Content-Type: application/json' \\"
echo "    -d @/tmp/vector-search-index.json"
echo ""
echo "Or use the Couchbase Web UI at http://$CB_HOST:8091"
echo "  1. Navigate to Search → Full Text Search"
echo "  2. Click 'Add Index'"
echo "  3. Import the JSON from /tmp/vector-search-index.json"
echo ""

echo "✓ Couchbase initialization complete!"
echo ""
echo "Access Couchbase Web UI at: http://$CB_HOST:8091"
echo "Username: $CB_USERNAME"
echo "Bucket: $CB_BUCKET"
