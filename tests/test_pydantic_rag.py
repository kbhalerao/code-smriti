"""
Test PydanticAI RAG agent with LMStudio.
"""
import asyncio
import sys
import os

# Add api-server to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "4-consume", "api-server"))

from app.database.couchbase_client import CouchbaseClient
from app.chat.pydantic_rag_agent import CodeSmritiRAGAgent
from loguru import logger


async def test_rag_query():
    """Test RAG agent with a code search query."""

    logger.info("=== Testing PydanticAI RAG Agent ===")

    # Set up configuration
    tenant_id = "code_kosha"
    ollama_host = "http://localhost:1234"
    llm_model = "qwen/qwen3-30b-a3b-2507"

    # Create Couchbase client
    logger.info("Connecting to Couchbase...")
    db = CouchbaseClient()

    # Create RAG agent
    logger.info(f"Creating RAG agent with model: {llm_model}")
    agent = CodeSmritiRAGAgent(
        db=db,
        tenant_id=tenant_id,
        ollama_host=ollama_host,
        llm_model=llm_model
    )

    # Test query
    query = "show me code examples where job_counter was used"
    logger.info(f"\n📝 Query: {query}\n")

    # Get response
    logger.info("Generating response...")
    response = await agent.chat(query)

    logger.info("\n=== RESPONSE ===")
    print(response)
    logger.info("=== END RESPONSE ===\n")

    logger.info("✅ Test completed successfully")


if __name__ == "__main__":
    asyncio.run(test_rag_query())
