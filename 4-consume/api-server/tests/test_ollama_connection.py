"""Test Ollama connection with PydanticAI"""
import asyncio
from pydantic_ai import Agent
from pydantic_ai.models.ollama import OllamaModel

async def test():
    # Create model
    model = OllamaModel(
        model_name='deepseek-coder:6.7b',
        base_url='http://localhost:11434',
    )

    # Create simple agent
    agent = Agent(model=model, result_type=str)

    # Test simple query
    try:
        result = await agent.run("Say hello")
        print(f"✓ Success: {result.data}")
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test())
