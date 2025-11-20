"""Simplified RAG agent using direct Ollama calls (Python 3.9 compatible)"""
import httpx
import os
from typing import List, Dict, Any
from pydantic import BaseModel, Field
from loguru import logger
from sentence_transformers import SentenceTransformer

from app.database.couchbase_client import CouchbaseClient

# Global embedding model (loaded once)
_embedding_model = None

def get_embedding_model():
    """Get or create the embedding model (singleton)"""
    global _embedding_model
    if _embedding_model is None:
        logger.info("Loading sentence-transformers embedding model...")
        # Disable tokenizer parallelism to avoid warnings
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
        # MUST match ingestion model: nomic-ai/nomic-embed-text-v1.5 (768 dims)
        _embedding_model = SentenceTransformer(
            'nomic-ai/nomic-embed-text-v1.5',
            trust_remote_code=True
        )
        logger.info("✓ Embedding model loaded: nomic-ai/nomic-embed-text-v1.5")
    return _embedding_model


class ChatResponse(BaseModel):
    """Chat response structure"""
    answer: str
    sources: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}


class SimpleRAGAgent:
    """Simple RAG agent with direct Ollama integration"""

    def __init__(
        self,
        db: CouchbaseClient,
        tenant_id: str,
        ollama_host: str = "http://localhost:11434"
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.ollama_host = ollama_host
        self.client = httpx.AsyncClient(timeout=60.0)

    async def chat(self, query: str) -> ChatResponse:
        """
        Process a chat query using two-phase architecture.

        Phase 1: Determine if query can be answered from code
        Phase 2: Search code and generate answer

        Args:
            query: User's query

        Returns:
            ChatResponse with answer and sources
        """
        logger.info(f"Processing query: '{query}'")

        # Skip intent classification - just search directly
        # Phase 1: Intent classification (DISABLED - was blocking legitimate queries)
        # intent = await self._classify_intent(query)
        intent = {"can_answer": True, "confidence": 1.0, "reasoning": "Intent classification disabled", "max_results": 5}

        # Phase 2: Search and generate
        sources = await self._search_code(query, limit=5)
        logger.info(f"Found {len(sources)} code sources")

        answer = await self._generate_answer(query, sources)

        return ChatResponse(
            answer=answer,
            sources=sources,
            metadata={
                "intent": intent,
                "num_sources": len(sources)
            }
        )

    async def _classify_intent(self, query: str) -> Dict[str, Any]:
        """Classify user intent using Ollama"""
        system_prompt = """You are analyzing if a query can be answered by searching code repositories.

Respond with JSON only:
{
  "can_answer": true/false,
  "confidence": 0.0-1.0,
  "reasoning": "brief explanation",
  "max_results": 5
}

CAN answer: code examples, API usage, implementations, architecture questions
CANNOT answer: weather, jokes, general knowledge, off-topic questions"""

        try:
            response = await self.client.post(
                f"{self.ollama_host}/api/generate",
                json={
                    "model": "deepseek-coder:6.7b",
                    "prompt": f"{system_prompt}\n\nQuery: {query}\n\nJSON response:",
                    "stream": False,
                    "format": "json"
                }
            )
            result = response.json()
            import json
            intent = json.loads(result['response'])
            return intent
        except Exception as e:
            logger.error(f"Intent classification failed: {e}")
            # Default to allowing query
            return {
                "can_answer": True,
                "confidence": 0.5,
                "reasoning": "Classification failed, allowing query",
                "max_results": 5
            }

    async def _search_code(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search code in Couchbase using native vector similarity via FTS"""
        try:
            # Generate embedding for the query
            logger.info(f"Generating embedding for query...")
            model = get_embedding_model()
            # MUST use same prefix as ingestion: 'search_document:'
            query_with_prefix = f"search_document: {query}"
            query_embedding = model.encode(query_with_prefix).tolist()
            logger.info(f"Query embedding generated: {len(query_embedding)} dimensions")

            # Use Couchbase FTS vector search (much faster than Python-based similarity)
            # The index "code_vector_index" is already configured with 768-dim vectors
            search_request = {
                "query": {
                    "match_none": {}  # We only want vector results, no text matching
                },
                "knn": [
                    {
                        "field": "embedding",
                        "vector": query_embedding,
                        "k": limit
                    }
                ],
                "size": limit,
                "fields": ["*"]
            }

            logger.debug(f"Executing FTS vector search...")

            # Call Couchbase FTS API directly via HTTP
            import os
            fts_url = f"http://{os.getenv('COUCHBASE_HOST', 'localhost')}:8094/api/index/code_vector_index/query"

            response = await self.client.post(
                fts_url,
                json=search_request,
                auth=(os.getenv('COUCHBASE_USERNAME', 'Administrator'),
                      os.getenv('COUCHBASE_PASSWORD', 'password123'))
            )

            if response.status_code != 200:
                logger.error(f"FTS search failed: {response.status_code} - {response.text}")
                return []

            result = response.json()
            hits = result.get('hits', [])

            # FTS returns document IDs; fetch full documents from Couchbase
            sources = []
            for hit in hits:
                doc_id = hit.get('id')
                if not doc_id:
                    continue

                try:
                    # Fetch full document from Couchbase using the ID
                    bucket = self.db.cluster.bucket(self.tenant_id)
                    collection = bucket.default_collection()
                    doc_result = collection.get(doc_id)
                    doc = doc_result.content_as[dict]

                    sources.append({
                        "content": doc.get('code_text', ''),  # Field is 'code_text' not 'content'
                        "repo": doc.get('repo_id', ''),
                        "file": doc.get('file_path', ''),
                        "language": doc.get('language', ''),
                        "score": hit.get('score', 0.0)
                    })
                except Exception as e:
                    logger.warning(f"Failed to fetch document {doc_id}: {e}")
                    continue

            top_scores = [s['score'] for s in sources[:3]]
            logger.info(f"Found {len(sources)} relevant chunks via FTS (top scores: {top_scores})")
            return sources

        except Exception as e:
            logger.error(f"Vector search failed: {e}", exc_info=True)
            return []

    async def _generate_answer(self, query: str, sources: List[Dict[str, Any]]) -> str:
        """Generate answer using Ollama with code context"""
        if not sources:
            return "No relevant code found in the indexed repositories."

        # Build context
        context = "\n\n".join([
            f"File: {s['repo']}/{s['file']}\n```{s['language']}\n{s['content'][:500]}\n```"
            for s in sources[:3]
        ])

        system_prompt = """You are a helpful code research assistant. Answer questions using the provided code context.

Guidelines:
- Reference specific files in your answer
- Provide code examples when helpful
- Be concise but thorough"""

        try:
            response = await self.client.post(
                f"{self.ollama_host}/api/generate",
                json={
                    "model": "deepseek-coder:6.7b",
                    "prompt": f"{system_prompt}\n\nContext:\n{context}\n\nQuestion: {query}\n\nAnswer:",
                    "stream": False
                }
            )
            result = response.json()
            return result['response']
        except Exception as e:
            logger.error(f"Answer generation failed: {e}")
            return f"Error generating answer: {str(e)}"

    async def close(self):
        """Close HTTP client"""
        await self.client.aclose()
