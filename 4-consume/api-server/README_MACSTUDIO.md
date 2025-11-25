# MacStudio LLM example

This folder includes a small helper and an example script to call an OpenAPI-compatible chat endpoint provided by MacStudio at:

http://macstudio.local/llm/v1/chat/completions

## Quick python example

1. Set env vars (if needed):

```bash
export MACSTUDIO_HOST="http://macstudio.local/llm"
export MACSTUDIO_API_KEY="<your_token>"  # optional
```

2. Run the example script:

```bash
python3 scripts/run_macstudio_chat.py "Give me a short summary of RAG"
```

3. The script uses `app.chat.macstudio_client.MacStudioClient` which does a simple POST to `/v1/chat/completions` with a standard OpenAPI-compatible body, returning the parsed JSON.

## Curl example

If you prefer curl, here's an example request showing the minimal OpenAPI-compatible payload format:

```bash
curl -s -X POST "http://macstudio.local/llm/v1/chat/completions" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \  # if required
  -d '{
    "model": "qwen3-30b",
    "messages": [
      {"role": "system", "content": "You are a concise technical assistant."},
      {"role": "user", "content": "Explain RAG in two sentences."}
    ],
    "max_tokens": 150,
    "temperature": 0.2
  }'
```

## Notes

- This client assumes the endpoint follows OpenAPI-compatible chat completion format (model + messages). Adjust fields as needed for your instance.
- The example uses httpx.AsyncClient and returns the raw JSON result from the server.
