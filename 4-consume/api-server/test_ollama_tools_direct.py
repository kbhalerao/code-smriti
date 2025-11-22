#!/usr/bin/env python3
"""Test Ollama tool calling directly via OpenAI-compatible API"""
import httpx
import json

# Test with OpenAI-compatible API
url = "http://localhost:11434/v1/chat/completions"

payload = {
    "model": "llama3.1:latest",
    "messages": [
        {"role": "user", "content": "What's the weather in San Francisco?"}
    ],
    "tools": [
        {
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the current weather for a location",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "location": {
                            "type": "string",
                            "description": "The city and state, e.g. San Francisco, CA"
                        }
                    },
                    "required": ["location"]
                }
            }
        }
    ]
}

print("Testing Ollama tool calling via OpenAI API...")
print("="*80)

response = httpx.post(url, json=payload, timeout=30.0)
print(f"Status: {response.status_code}")
print(f"\nResponse:")
print(json.dumps(response.json(), indent=2))
