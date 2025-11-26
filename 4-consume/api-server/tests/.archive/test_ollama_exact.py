#!/usr/bin/env python3
"""Test exact payload that worked before"""
import httpx
import json
import asyncio

async def test():
    client = httpx.AsyncClient(timeout=60.0)

    url = "http://localhost:11434/v1/chat/completions"

    # Exact payload from earlier successful test
    payload = {
        "model": "llama3.1:latest",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant with access to a code search tool. When the user asks about code, use the search_code tool to find relevant code snippets from indexed repositories."},
            {"role": "user", "content": "Find Django Channels code"}
        ],
        "tools": [
            {
                "type": "function",
                "function": {
                    "name": "search_code",
                    "description": "Search for code",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"}
                        },
                        "required": ["query"]
                    }
                }
            }
        ]
    }

    print("Testing exact payload...")
    print("="*80)

    response = await client.post(url, json=payload)
    data = response.json()

    print(f"Status: {response.status_code}")
    print(f"Finish reason: {data['choices'][0]['finish_reason']}")
    print(f"Has tool_calls: {bool(data['choices'][0]['message'].get('tool_calls'))}")

    if data['choices'][0]['message'].get('tool_calls'):
        print(f"\n✓ Tool calls found!")
        print(json.dumps(data['choices'][0]['message']['tool_calls'], indent=2))
    else:
        print(f"\n✗ No tool calls - got text response:")
        print(data['choices'][0]['message'].get('content', '')[:200])

asyncio.run(test())
