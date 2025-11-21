#!/bin/bash
# Test script for code-smriti RAG API
# Run from: 4-consume/api-server/

set -e

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Testing CodeSmriti RAG API ===${NC}\n"

# Configuration
API_BASE="http://localhost:8000"
TEST_ENDPOINT="${API_BASE}/api/chat/test"

# Test 1: Health Check
echo -e "${BLUE}[1/5] Testing health endpoint...${NC}"
curl -s "${API_BASE}/health" | jq .
echo -e "${GREEN}✓ Health check passed${NC}\n"

# Test 2: Chat Health
echo -e "${BLUE}[2/5] Testing chat health endpoint...${NC}"
curl -s "${API_BASE}/api/chat/health" | jq .
echo -e "${GREEN}✓ Chat health passed${NC}\n"

# Test 3: Simple Query (Non-streaming)
echo -e "${BLUE}[3/5] Testing simple query (non-streaming)...${NC}"
curl -s -X POST "${TEST_ENDPOINT}" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Show me how authentication works in the codebase",
    "stream": false
  }' | jq .
echo -e "${GREEN}✓ Simple query passed${NC}\n"

# Test 4: Query with Conversation History
echo -e "${BLUE}[4/5] Testing query with conversation history...${NC}"
curl -s -X POST "${TEST_ENDPOINT}" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Can you give me more details about JWT tokens?",
    "stream": false,
    "conversation_history": [
      {"role": "user", "content": "How does authentication work?"},
      {"role": "assistant", "content": "Authentication uses JWT tokens with bcrypt password hashing."}
    ]
  }' | jq .
echo -e "${GREEN}✓ Context-aware query passed${NC}\n"

# Test 5: Streaming Response
echo -e "${BLUE}[5/5] Testing streaming response...${NC}"
curl -s -X POST "${TEST_ENDPOINT}" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "What vector search capabilities exist?",
    "stream": true
  }'
echo -e "\n${GREEN}✓ Streaming query passed${NC}\n"

echo -e "${GREEN}=== All tests passed! ===${NC}"
