#!/usr/bin/env python3
"""
Quick test to check if pydantic-ai can talk to Ollama properly
"""
import asyncio
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel

async def test_ollama():
    try:
        print("Creating Ollama model...")
        model = OllamaModel(
            model_name='deepseek-coder:6.7b',
            base_url='http://localhost:11434',
        )

        print("Creating agent...")
        agent = Agent(
            model=model,
            system_prompt="You are a helpful assistant.",
            result_type=str,
        )

        print("Running test query...")
        result = await agent.run("Say hello in one sentence")

        print(f"\n✓ Success! Response: {result.data}\n")

    except Exception as e:
        print(f"\n✗ Error: {e}\n")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    asyncio.run(test_ollama())
