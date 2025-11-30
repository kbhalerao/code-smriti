#!/bin/bash
# Manual run script for api-server - bypasses docker-compose restart policy
# Use this to debug startup issues

# Stop any existing test container
docker rm -f codesmriti_api_test 2>/dev/null

# Run with no automatic restart, mapped to port 8001
docker run -d \
  --name codesmriti_api_test \
  --network code-smriti_codesmriti_network \
  -e COUCHBASE_HOST=couchbase \
  -e COUCHBASE_PORT=8091 \
  -e COUCHBASE_USER=Administrator \
  -e COUCHBASE_PASSWORD=${COUCHBASE_PASSWORD:?COUCHBASE_PASSWORD required} \
  -e COUCHBASE_BUCKET_CODE=code_kosha \
  -e COUCHBASE_BUCKET_USERS=users \
  -e OLLAMA_HOST=http://host.docker.internal:1234 \
  -e JWT_SECRET=your-secret-key-here-change-in-production \
  -e AES_ENCRYPTION_KEY=your-32-byte-encryption-key-here \
  -e API_USERNAME=codesmriti \
  -e API_PASSWORD=Kx9mP2vL8nQ5wR7jT4yB3zF6H8aC1dE9 \
  -e EMBEDDING_MODEL_NAME=nomic-ai/nomic-embed-text-v1.5 \
  -e LLM_MODEL_NAME=qwen/qwen3-30b-a3b-2507 \
  --add-host=host.docker.internal:host-gateway \
  -p 8001:8000 \
  -v model_cache:/root/.cache/huggingface \
  -v model_cache:/root/.cache/torch \
  code-smriti-api-server:latest

echo ""
echo "Container started on port 8001. Monitoring logs..."
echo "Model loading will take ~30-60 seconds on first run."
echo ""
echo "Press Ctrl+C to stop following logs (container keeps running)"
echo ""

docker logs -f codesmriti_api_test
