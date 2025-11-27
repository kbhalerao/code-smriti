#!/usr/bin/env python3
"""Test chat endpoint"""
import requests
import json

url = "http://localhost:8000/api/chat/test"
data = {
    "query": "Say hello!",
    "stream": False
}

response = requests.post(url, json=data)
print(f"Status: {response.status_code}")
print(f"Response: {json.dumps(response.json(), indent=2)}")
