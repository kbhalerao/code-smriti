#!/bin/bash
#
# Test ingestion with a small public repo to verify chunk uniqueness
#

set -e

cd "$(dirname "$0")"

echo "======================================"
echo "Small Repo Ingestion Test"
echo "======================================"
echo ""

# Use a very small public repo for testing
TEST_REPO="octocat/Hello-World"

# Backup current .env
cp .env .env.test-backup

# Update .env for test
sed -i '' "s|GITHUB_REPOS=.*|GITHUB_REPOS=$TEST_REPO|" .env
echo "Updated GITHUB_REPOS to: $TEST_REPO"

# Clean database
echo "Cleaning database..."
curl -s -u Administrator:password123 -X POST "http://localhost:8093/query/service" \
  -d "statement=DELETE FROM \`code_kosha\`" > /dev/null

echo "Database cleaned"
echo ""

# Run ingestion
echo "Running ingestion..."
LOG_LEVEL=DEBUG ./run-ingestion-native.sh 2>&1 | tee /tmp/test-small-ingestion.log

# Check results
echo ""
echo "======================================"
echo "Results:"
echo "======================================"

# Count chunks in log
CHUNKS_PARSED=$(grep "Parsed.*chunks from" /tmp/test-small-ingestion.log | tail -1 | grep -oE '[0-9]+' | head -1)
echo "Chunks parsed: $CHUNKS_PARSED"

# Count chunks in database
CHUNKS_STORED=$(curl -s -u Administrator:password123 "http://localhost:8093/query/service" \
  -d 'statement=SELECT COUNT(*) as count FROM `code_kosha`' | \
  python3 -c "import sys, json; print(json.load(sys.stdin)['results'][0]['count'])")

echo "Chunks stored: $CHUNKS_STORED"
echo ""

if [ "$CHUNKS_PARSED" -eq "$CHUNKS_STORED" ]; then
    echo "✓ SUCCESS: All chunks stored (no deduplication)"
else
    LOST=$((CHUNKS_PARSED - CHUNKS_STORED))
    PCT=$((LOST * 100 / CHUNKS_PARSED))
    echo "✗ FAIL: Lost $LOST chunks ($PCT%)"
    echo ""
    echo "Check debug logs in /tmp/test-small-ingestion.log"
fi

echo ""
echo "Restoring original .env..."
mv .env.test-backup .env
echo "Done"
