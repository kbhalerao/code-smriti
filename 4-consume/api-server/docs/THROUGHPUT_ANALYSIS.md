# RAG API Throughput Analysis

**Date**: 2025-11-20
**System**: Manual RAG Agent with Ollama llama3.1:latest
**Test**: 37 evaluation questions from `tests/search_eval_questions.json`

## Executive Summary

**Best Performance**: Sequential mode with batch=3, delay=1s → **0.22 questions/second**

**Bottleneck Identified**: Ollama LLM (single-threaded processing) - not the API server or Couchbase

**System Stability**: 100% success rate across all modes, no crashes with singleton pattern

---

## Test Configuration

### Hardware
- MacBook Pro with Colima Docker runtime
- Ollama running llama3.1:latest (8B parameter model)
- Couchbase 7.6.0 with 46,089 indexed documents

### Software
- Manual RAG agent (bypasses pydantic-ai tool execution bug)
- Singleton pattern for shared resources (HTTP client, embedding model)
- Vector search via Couchbase FTS + kNN (nomic-embed-text-v1.5)

---

## Throughput Test Results

### Test 1: Sequential (batch=3, delay=1s) ✅ WINNER
```
Batch size: 3 questions
Delay between batches: 1.0s
Mode: SEQUENTIAL

Total questions: 37
Successful: 37 (100.0%)
Failed: 0 (0.0%)
Average response time: 4.26s
Total time: 169.8s
Overall throughput: 0.22 questions/second
```

**Per-batch throughput variation**:
- Batch 1: 0.11 q/s (slow start)
- Batch 2: 0.38 q/s
- Batch 3: 0.66 q/s (peak)
- Batch 9: 0.25 q/s (average)

**Why it won**: Small batches with delays prevent resource contention, allow Ollama to process efficiently.

---

### Test 2: Sequential (batch=5, delay=0s)
```
Batch size: 5 questions
Delay between batches: 0.0s
Mode: SEQUENTIAL

Total questions: 37
Successful: 37 (100.0%)
Failed: 0 (0.0%)
Average response time: 6.11s
Total time: 226.1s
Overall throughput: 0.16 questions/second
```

**Why slower**: Larger batches with no delay → more time per batch, higher avg response time.

---

### Test 3: Concurrent (batch=5, parallel processing)
```
Batch size: 5 questions (processed in parallel)
Delay between batches: 0.0s
Mode: CONCURRENT

Total questions: 37
Successful: 37 (100.0%)
Failed: 0 (0.0%)
Average response time: 16.65s
Total time: 206.6s
Overall throughput: 0.18 questions/second
```

**Per-batch timing analysis**:
- Batch 1: 38.3s (0.13 q/s) - slowest question dominated
- Batch 4: 19.2s (0.26 q/s) - best batch
- Batch 7: 31.1s (0.16 q/s) - variable performance

**Why concurrent is slower**:
1. Ollama processes LLM requests **sequentially** (single-threaded)
2. 5 parallel requests queue up and wait
3. Batch time = slowest individual question (e.g., 38.3s for batch 1)
4. Higher average response time (16.65s vs 4.26s sequential)

---

## Bottleneck Analysis

### Hypothesis: Ollama is the bottleneck

**Evidence**:
1. **Resource utilization**:
   - Couchbase: 8.53% CPU, 1.83GB memory (plenty of headroom)
   - Server: Stable, no memory issues with singleton pattern
   - Ollama: Single model runner process

2. **Concurrent penalty**:
   - Concurrent mode didn't improve throughput (0.18 vs 0.22 sequential)
   - Much higher avg response time (16.65s vs 4.26s)
   - Parallel requests waited in queue

3. **Sequential efficiency**:
   - Best performance with small batches (batch=3)
   - Delays between batches helped (delay=1s better than 0s)

**Conclusion**: Ollama LLM is single-threaded and processes one request at a time. Concurrent API requests don't help because they queue up waiting for the LLM.

---

## Throughput Optimization Recommendations

### Short Term (Current System)
1. ✅ **Use sequential processing** with batch=3, delay=1s
   - Achieves best throughput (0.22 q/s)
   - Most predictable response times (~4s avg)

2. ✅ **Singleton pattern** for resources (already implemented)
   - Prevents memory exhaustion
   - Stable across all test modes

3. ⚠️ **Don't use concurrent mode** for single Ollama instance
   - No throughput benefit
   - Higher latency due to queueing

### Medium Term (Scale Ollama)
1. **Multiple Ollama instances** behind load balancer
   - Run 3-5 Ollama containers on different ports
   - Nginx/HAProxy round-robin distribution
   - Expected throughput: 0.22 × N instances

2. **GPU acceleration** for Ollama
   - Current: CPU-only llama3.1:8B
   - With GPU: Faster inference, higher throughput

3. **Streaming responses** for better UX
   - Current: Wait for full response
   - Streaming: Show tokens as generated
   - Perceived latency improvement

### Long Term (Production Architecture)
1. **Upgrade pydantic-ai** when Ollama tool calling fixed (v1.22+)
   - Cleaner code with `@agent.tool` decorators
   - Better framework support

2. **Model optimization**:
   - Test quantized models (Q4_K_M) for faster inference
   - Evaluate smaller models (3B) for simpler queries
   - Consider specialized code models (Qwen-Coder, DeepSeek-Coder)

3. **Caching layer**:
   - Cache vector search results for common queries
   - Cache LLM responses for repeated questions
   - Redis/Memcached integration

---

## Batch Prompting Alternative

### Concept
User suggested: "Put multiple questions in a single prompt and get multiple answers out"

**Example**:
```python
prompt = "Answer these 3 questions: 1) Django Channels code, 2) authentication, 3) ..."
→ Single LLM call returns all answers in one response
```

**Why it works for simple QA**:
- Single forward pass (efficient)
- Better GPU utilization
- Avoids per-request overhead

**Why it's problematic for RAG**:
1. ❌ **Different tool calls**: Each question needs different vector searches
   - Q1: Search "Django Channels"
   - Q2: Search "authentication"
   - Can't batch these into one tool call

2. ❌ **Tool routing complexity**: Which search results belong to which question?

3. ❌ **Context explosion**: All questions + all search results in one prompt
   - Current: 1 question + 5 code chunks ≈ 2K tokens
   - Batched: 5 questions + 25 code chunks ≈ 10K+ tokens

4. ❌ **Can't parallelize vector search**: Questions are bundled, searches still sequential

5. ❌ **Manual RAG agent architecture**: Designed for single-question tool loop
   - Iteration: question → tool call → results → final answer
   - Batch would require major refactoring

**Conclusion**: Batch prompting doesn't fit the RAG use case. Real solution is multiple Ollama instances.

---

## System Stability Assessment

### ✅ All Tests Passed
- **37/37 questions succeeded** in every mode
- **No crashes** with singleton pattern
- **No memory leaks** during extended runs
- **No Couchbase errors** under load

### Resource Monitoring
```
Couchbase: 8.53% CPU, 1.83GB memory (healthy)
Ollama: Single runner process (bottleneck)
Docker: Stable, no container issues
```

### Comparison to Previous Crashes
- **Before**: Per-request resource creation → memory exhaustion → crash
- **After**: Singleton pattern → stable resources → no crashes

---

## Production Recommendations

### For Single Ollama Instance (Current)
```python
# Optimal configuration
BATCH_SIZE = 3
BATCH_DELAY = 1.0  # seconds
MODE = "sequential"

# Expected performance
THROUGHPUT = 0.22  # questions/second
AVG_LATENCY = 4.26  # seconds
SUCCESS_RATE = 100%  # (with manual RAG agent)
```

### For Scaled Deployment
```yaml
# docker-compose.yml
services:
  ollama-1:
    image: ollama/ollama
    ports: ["11434:11434"]

  ollama-2:
    image: ollama/ollama
    ports: ["11435:11434"]

  ollama-3:
    image: ollama/ollama
    ports: ["11436:11434"]

  nginx:
    # Load balance across ollama-1, ollama-2, ollama-3
    # Expected throughput: 0.22 × 3 = 0.66 q/s
```

---

## Files Generated

### Evaluation Results
- `eval_results_final_20251120_231316.json` - Sequential (batch=3, delay=1s)
- `eval_results_final_20251120_234610.json` - Sequential (batch=5, delay=0s)
- `eval_results_final_20251120_235703.json` - Concurrent (batch=5)

### Logs
- `concurrent_eval.log` - Full concurrent mode output

### Test Scripts
- `run_rag_eval.py` - Evaluation harness with concurrent/sequential modes
- `tests/search_eval_questions.json` - 37 test questions

---

## Next Steps

1. ✅ Document throughput findings (this file)
2. ⏳ Commit manual RAG implementation
3. ⏳ Add throughput metrics to production monitoring
4. ⏳ Plan multi-instance Ollama deployment
5. ⏳ Evaluate streaming response implementation

---

## References

- Manual RAG implementation: `docs/MANUAL_RAG_IMPLEMENTATION.md`
- Evaluation script: `run_rag_eval.py`
- RAG agent: `app/chat/manual_rag_agent.py`
- PydanticAI issues: #703, #238, #1292 (Ollama tool calling broken)
