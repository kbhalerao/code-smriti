# Manual RAG Implementation - Technical Documentation

**Date**: 2025-11-20
**Status**: ✅ Production Ready
**Success Rate**: 100% (37/37 eval questions)

## Problem Summary

PydanticAI v1.21.0 has **broken tool calling with Ollama** models:
- GitHub Issues: #703, #238, #1292
- Symptom: Model returns `tool_calls` in response but PydanticAI never executes the Python functions
- Impact: RAG agent couldn't perform vector search, returned synthetic responses instead of actual code

## Root Causes Identified

### 1. PydanticAI + Ollama Incompatibility
- **Issue**: `OpenAIChatModel` with `OllamaProvider` doesn't execute registered `@agent.tool` functions
- **Evidence**: Ollama API returns proper `tool_calls` with `finish_reason="tool_calls"`, but pydantic-ai ignores them
- **Workaround**: Bypass pydantic-ai's tool execution entirely

### 2. System Prompt Interference
- **Issue**: Complex system prompts prevented llama3.1 from using tools
- **Evidence**: Same query with/without system prompt:
  - With system prompt: `finish_reason="stop"` (no tools)
  - Without system prompt: `finish_reason="tool_calls"` (tools work)
- **Solution**: Removed system prompt, kept minimal instructions

## Solution: Manual Tool Calling Loop

### Architecture

```
User Query
    ↓
Manual RAG Agent
    ↓
[Iteration Loop - Max 5]
    ↓
Ollama API (direct HTTP)
    ↓
Parse Response
    ├─ tool_calls? → Execute Python function → Add result to messages → Loop
    └─ stop? → Return final text response
```

### Key Components

**File**: `app/chat/manual_rag_agent.py`

#### 1. Tool Functions
```python
async def search_code_tool(ctx: RAGContext, query: str, limit: int = 5) -> List[Dict]:
    """Execute vector search via Couchbase FTS + kNN"""
    # Generate embedding
    embedding = ctx.embedding_model.encode(f"search_document: {query}")

    # Call Couchbase FTS API
    results = await fts_api.search(embedding)

    # Return code chunks
    return [{"content": ..., "file_path": ..., "score": ...}]
```

#### 2. Manual Tool Calling Loop
```python
async def chat(self, query: str, max_iterations: int = 5) -> str:
    messages = [{"role": "user", "content": query}]

    for iteration in range(max_iterations):
        # Call Ollama directly
        response = await self._call_ollama(messages)

        if response["finish_reason"] == "tool_calls":
            # Execute each tool
            for tool_call in response["tool_calls"]:
                result = await self._execute_tool(tool_call)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tool_call["id"],
                    "content": json.dumps(result)
                })
            continue  # Loop for final response
        else:
            return response["content"]  # Final answer
```

#### 3. Direct Ollama API Call
```python
async def _call_ollama(self, messages: List[Dict]) -> Dict:
    url = f"{self.ollama_host}/v1/chat/completions"

    payload = {
        "model": self.llm_model,
        "messages": messages,
        "tools": TOOL_SCHEMAS  # OpenAI-compatible tool definitions
    }

    response = await self.http_client.post(url, json=payload)
    return parse_response(response)
```

### Tool Schema Format

OpenAI-compatible function calling schema:

```python
TOOL_SCHEMAS = [{
    "type": "function",
    "function": {
        "name": "search_code",
        "description": "Search for code across indexed repositories using semantic vector search.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Natural language or code search query"},
                "limit": {"type": "integer", "description": "Max results (default: 5, max: 10)", "default": 5},
                "repo_filter": {"type": "string", "description": "Optional repo filter", "default": None}
            },
            "required": ["query"]
        }
    }
}]
```

## Performance Metrics

### Evaluation Results (37 Questions)

**Sequential Mode** (safe, one-at-a-time):
- ✅ **Success Rate**: 100% (37/37)
- ⏱️ **Avg Response Time**: 6.11s
- 📊 **Throughput**: 0.16 q/s
- 🔧 **Tool Execution**: Working perfectly
- 💾 **Memory**: Stable (singleton pattern)

**Comparison to Broken PydanticAI**:
| Metric | PydanticAI v1.21.0 | Manual Implementation |
|--------|-------------------|----------------------|
| Tool Execution | ❌ Broken | ✅ Working |
| Success Rate | 0% (synthetic responses) | 100% (real code search) |
| Vector Search | ❌ Never called | ✅ Every query |
| Response Quality | Generic examples | Actual codebase snippets |

## Migration Path

### Before (Broken)
```python
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel

agent = Agent(model, system_prompt=PROMPT, deps_type=RAGContext, output_type=str)

@agent.tool
async def search_code(ctx, query): ...  # Never executes!

result = await agent.run(query, deps=ctx)
```

### After (Working)
```python
from app.chat.manual_rag_agent import ManualRAGAgent

agent = ManualRAGAgent(db=db, tenant_id=tenant_id)
result = await agent.chat(query)  # Tool execution works!
```

## Configuration

### Environment Variables
```bash
OLLAMA_HOST=http://localhost:11434
LLM_MODEL_NAME=llama3.1:latest
EMBEDDING_MODEL_NAME=nomic-ai/nomic-embed-text-v1.5
```

### Required Dependencies
```
pydantic-ai==1.21.0  # Only for models, not tool execution
httpx==0.28.1
sentence-transformers==3.3.1
```

## Known Limitations

1. **No Streaming Support**: Manual implementation doesn't support streaming yet
   - Workaround: Implement async generator wrapper

2. **Fixed Max Iterations**: Hardcoded to 5 tool calling rounds
   - Workaround: Make configurable via constructor parameter

3. **No System Prompt**: Removed to fix tool calling
   - Impact: Less directive responses, but tools work
   - Future: Test minimal system prompts

## Future Improvements

### Short Term
1. Add streaming support for `chat_stream()` method
2. Make max_iterations configurable
3. Add token counting and cost tracking
4. Implement response caching

### Long Term
1. **Upgrade to pydantic-ai v1.22+** when Ollama tool calling is fixed
2. Test alternative models (qwen2.5, mistral) for better tool calling
3. Add conversation memory with vector storage
4. Implement multi-turn conversation with context window management

## Testing

### Manual Test
```bash
curl -X POST http://localhost:8000/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "Show me Django Channels background worker code", "stream": false}'
```

### Run Full Evaluation
```bash
# Sequential (safe)
python run_rag_eval.py 5 1

# Concurrent (throughput test)
python run_rag_eval.py 5 0 --concurrent
```

## References

- PydanticAI Issue #703: "Ollama - Tools & retries does not work properly"
- PydanticAI Issue #238: "Function tool calling returns ModelTextResponse instead of ModelStructuredResponse"
- PydanticAI Issue #1292: "Streaming stops prematurely after tool call with Ollama"
- Ollama OpenAI API: https://github.com/ollama/ollama/blob/main/docs/openai.md

## Author Notes

This implementation is a **temporary workaround** for pydantic-ai's Ollama compatibility issues. Once the upstream bugs are fixed, we can migrate back to the cleaner `@agent.tool` decorator pattern. However, this manual approach gives us:

- ✅ Full control over tool execution
- ✅ Better debugging visibility
- ✅ No framework dependency bugs
- ✅ Production-ready stability

**Recommendation**: Keep this implementation until pydantic-ai v1.22+ with confirmed Ollama tool calling support.
