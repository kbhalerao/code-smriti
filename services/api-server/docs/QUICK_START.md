# CodeSmriti API - Quick Start

## Server URL
```
http://192.168.1.29
```

## Simplest Way to Use (No Auth)

```bash
curl -X POST http://192.168.1.29/api/chat/test \
  -H "Content-Type: application/json" \
  -d '{"query": "How do I implement retry logic for async functions?"}'
```

## With Authentication

### 1. Get Token (one-time)
```bash
curl -X POST http://192.168.1.29/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "YOUR_NAME", "password": "demo"}'
```

Response:
```json
{"access_token": "eyJhbGc...", "token_type": "bearer"}
```

### 2. Use Token
```bash
curl -X POST http://192.168.1.29/api/chat/ \
  -H "Authorization: Bearer YOUR_TOKEN_HERE" \
  -H "Content-Type: application/json" \
  -d '{"query": "your question"}'
```

## One-Liner (Bash)

```bash
TOKEN=$(curl -s -X POST http://192.168.1.29/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "YOUR_NAME", "password": "demo"}' | jq -r '.access_token')

curl -X POST http://192.168.1.29/api/chat/ \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "Show me Django background task examples"}'
```

## Python Example

```python
import requests

# Simple (no auth)
response = requests.post(
    'http://192.168.1.29/api/chat/test',
    json={'query': 'How do I implement authentication?'},
    timeout=120
)
print(response.json()['answer'])

# With auth
def ask_with_auth(query):
    # Get token
    token_response = requests.post(
        'http://192.168.1.29/api/auth/login',
        json={'username': 'YOUR_NAME', 'password': 'demo'}
    )
    token = token_response.json()['access_token']

    # Ask question
    response = requests.post(
        'http://192.168.1.29/api/chat/',
        headers={'Authorization': f'Bearer {token}'},
        json={'query': query},
        timeout=120
    )
    return response.json()['answer']
```

## Notes

- **Timeout**: Set to 120 seconds for LLM responses
- **Token lifetime**: 24 hours
- **Auth**: Any username/password works for internal use
- **Full docs**: See `TEAM_ACCESS_GUIDE.md`

## Health Check

```bash
curl http://192.168.1.29/health
```

Should return:
```json
{"status": "healthy", "service": "CodeSmriti API", "version": "v1"}
```
