#!/usr/bin/env python3
"""Test chat endpoint with code query"""
import requests
import json

url = "http://localhost:8000/api/chat/test"
data = {
    "query": "Show me examples of authentication code",
    "stream": False
}

print("Sending query...")
response = requests.post(url, json=data, timeout=60)
print(f"\nStatus: {response.status_code}")
print(f"\nResponse text:\n{response.text[:1000]}")
try:
    result = response.json()
    print(f"\nAnswer:\n{result.get('answer', 'No answer')[:500]}")
    print(f"\nMetadata: {result.get('metadata', {})}")
except:
    pass
