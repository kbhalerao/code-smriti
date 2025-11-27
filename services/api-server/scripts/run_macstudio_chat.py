#!/usr/bin/env python3
"""Small example script that demonstrates calling macstudio.local /v1/chat/completions.

Usage:
    export MACSTUDIO_HOST="http://macstudio.local/llm"
    export MACSTUDIO_API_KEY="<token>"  # optional
    python3 scripts/run_macstudio_chat.py "What is retrieval augmented generation (RAG) in 3 sentences?"

The script will send a simple message and pretty-print the JSON response.
"""
import asyncio
import json
import os
import sys
from loguru import logger

from app.chat.macstudio_client import MacStudioClient


async def main():
    query = "How would you explain RAG to a developer in three sentences?"
    if len(sys.argv) > 1:
        query = sys.argv[1]

    host = os.getenv("MACSTUDIO_HOST", "http://macstudio.local/llm")
    api_key = os.getenv("MACSTUDIO_API_KEY")

    client = MacStudioClient(host=host, api_key=api_key)

    messages = [
        {"role": "system", "content": "You are a concise, technical assistant."},
        {"role": "user", "content": query}
    ]

    try:
        resp = await client.chat(messages=messages, model="qwen/qwen3-30b-a3b-2507", max_tokens=200, temperature=0.1)
        print(json.dumps(resp, indent=2))
    except Exception as e:
        logger.error(f"Chat call failed: {e}")


if __name__ == "__main__":
    asyncio.run(main())
