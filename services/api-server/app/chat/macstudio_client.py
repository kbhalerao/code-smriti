"""Simple OpenAPI-compatible chat client for macstudio.local LLM endpoint.

Usage:
    from app.chat.macstudio_client import MacStudioClient

    client = MacStudioClient(host="http://macstudio.local/llm", api_key=None)
    resp = await client.chat(messages=[{"role": "user", "content": "Say hi"}], model="qwen3-30b")
"""
from typing import List, Dict, Optional, Any
import os
import httpx
from loguru import logger


class MacStudioClient:
    def __init__(self, host: Optional[str] = None, api_key: Optional[str] = None, timeout: float = 30.0):
        # base path expected like: http://macstudio.local/llm
        self.host = host or os.getenv("MACSTUDIO_HOST", "http://macstudio.local/llm")
        self.api_key = api_key or os.getenv("MACSTUDIO_API_KEY")
        self.timeout = timeout

    async def chat(self, messages: List[Dict[str, str]], model: str = "qwen3-30b", **kwargs: Any) -> Dict[str, Any]:
        """Send a chat completion request to the OpenAPI-compatible endpoint.

        Args:
            messages: List of {'role': 'user'|'assistant'|'system', 'content': '...'}
            model: Model identifier
            kwargs: Additional top-level payload fields (max_tokens, temperature, etc.)

        Returns:
            Response JSON parsed as dict
        """
        url = f"{self.host}/v1/chat/completions"

        payload = {
            "model": model,
            "messages": messages,
        }
        payload.update(kwargs)

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        logger.debug(f"Sending chat request to {url} (model={model})")

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.post(url, json=payload, headers=headers)
            except Exception as e:
                logger.error(f"HTTP request to MacStudio failed: {e}")
                raise

        if resp.status_code >= 400:
            logger.error(f"MacStudio API returned {resp.status_code}: {resp.text[:1000]}")
            raise Exception(f"MacStudio API error {resp.status_code}: {resp.text[:200]}")

        try:
            data = resp.json()
        except ValueError:
            logger.error("MacStudio response was not valid JSON")
            raise

        return data


def example_payload():
    return {
        "model": "qwen3-30b",
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": "Give a one-paragraph summary of RAG systems."}
        ],
        "max_tokens": 256,
        "temperature": 0.2,
    }
