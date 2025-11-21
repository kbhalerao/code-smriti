"""
Manual RAG agent with explicit tool calling loop (bypasses pydantic-ai tool execution bug).
"""
import json
import os
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

import httpx
from loguru import logger
from pydantic import BaseModel, Field
from sentence_transformers import SentenceTransformer

from app.database.couchbase_client import CouchbaseClient


# ============================================================================
# Models and Data Structures
# ============================================================================

class CodeChunk(BaseModel):
    """Represents a code chunk from search results."""
    content: str
    repo_id: str
    file_path: str
    language: str
    score: float
    start_line: Optional[int] = None
    end_line: Optional[int] = None


class ConversationMessage(BaseModel):
    """Single message in conversation history."""
    role: str
    content: str


@dataclass
class RAGContext:
    """Context for RAG operations."""
    db: CouchbaseClient
    tenant_id: str
    ollama_host: str
    http_client: httpx.AsyncClient
    embedding_model: SentenceTransformer


# ============================================================================
# Global Resources (from parent module)
# ============================================================================

from .pydantic_rag_agent import get_embedding_model, get_http_client


# ============================================================================
# Tool Functions
# ============================================================================

async def search_code_tool(
    ctx: RAGContext,
    query: str,
    limit: int = 5,
    repo_filter: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    Search for code across indexed repositories using semantic vector search.

    Returns list of dicts (for JSON serialization to LLM).
    """
    logger.warning(f"🔧 TOOL EXECUTING: search_code(query='{query}', limit={limit}, repo={repo_filter})")

    try:
        # Generate embedding for the query
        query_with_prefix = f"search_document: {query}"
        query_embedding = ctx.embedding_model.encode(query_with_prefix).tolist()

        # Build FTS search request with kNN vector search
        search_request = {
            "query": {
                "match_none": {}  # We only want vector results
            },
            "knn": [
                {
                    "field": "embedding",
                    "vector": query_embedding,
                    "k": min(limit, 10)
                }
            ],
            "size": min(limit, 10),
            "fields": ["*"]
        }

        # Add repository filter if specified
        if repo_filter:
            search_request["query"] = {
                "conjuncts": [
                    {"match": repo_filter, "field": "repo_id"}
                ]
            }

        # Call Couchbase FTS API
        couchbase_host = os.getenv('COUCHBASE_HOST', 'localhost')
        couchbase_user = os.getenv('COUCHBASE_USERNAME', 'Administrator')
        couchbase_pass = os.getenv('COUCHBASE_PASSWORD', 'password123')

        fts_url = f"http://{couchbase_host}:8094/api/index/code_vector_index/query"

        response = await ctx.http_client.post(
            fts_url,
            json=search_request,
            auth=(couchbase_user, couchbase_pass)
        )

        if response.status_code != 200:
            logger.error(f"FTS search failed: {response.status_code} - {response.text}")
            return []

        result = response.json()
        hits = result.get('hits', [])

        # Fetch full documents from Couchbase
        code_chunks = []
        for hit in hits:
            doc_id = hit.get('id')
            if not doc_id:
                continue

            try:
                bucket = ctx.db.cluster.bucket(ctx.tenant_id)
                collection = bucket.default_collection()
                doc_result = collection.get(doc_id)
                doc = doc_result.content_as[dict]

                code_chunks.append({
                    "content": doc.get('code_text', '')[:500],  # Truncate for LLM context
                    "repo_id": doc.get('repo_id', ''),
                    "file_path": doc.get('file_path', ''),
                    "language": doc.get('language', ''),
                    "score": hit.get('score', 0.0),
                    "start_line": doc.get('start_line'),
                    "end_line": doc.get('end_line')
                })
            except Exception as e:
                logger.warning(f"Failed to fetch document {doc_id}: {e}")
                continue

        logger.info(f"✓ Found {len(code_chunks)} code chunks")
        return code_chunks

    except Exception as e:
        logger.error(f"Vector search failed: {e}", exc_info=True)
        return []


async def list_available_repos_tool(ctx: RAGContext) -> List[str]:
    """List all repositories available in the indexed codebase."""
    logger.warning(f"🔧 TOOL EXECUTING: list_available_repos()")

    try:
        query = f"""
            SELECT DISTINCT repo_id
            FROM `{ctx.tenant_id}`
            WHERE repo_id IS NOT MISSING
            ORDER BY repo_id
            LIMIT 50
        """

        result = ctx.db.cluster.query(query)
        repos = [row['repo_id'] for row in result]

        logger.info(f"✓ Found {len(repos)} repositories")
        return repos

    except Exception as e:
        logger.error(f"List repos failed: {e}")
        return []


# ============================================================================
# Tool Registry
# ============================================================================

TOOL_FUNCTIONS = {
    "search_code": search_code_tool,
    "list_available_repos": list_available_repos_tool
}

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "search_code",
            "description": "Search for code across indexed repositories using semantic vector search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Natural language or code search query"
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of results (default: 5, max: 10)",
                        "default": 5
                    },
                    "repo_filter": {
                        "type": "string",
                        "description": "Optional repository filter (format: owner/repo)",
                        "default": None
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_available_repos",
            "description": "List all repositories available in the indexed codebase.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


# ============================================================================
# Manual RAG Agent
# ============================================================================

SYSTEM_PROMPT = """You are a helpful assistant with access to a code search tool.

When the user asks about code, use the search_code tool to find relevant code snippets from indexed repositories. Then provide a clear answer with the code examples you found."""


class ManualRAGAgent:
    """RAG agent with manual tool calling loop."""

    def __init__(
        self,
        db: CouchbaseClient,
        tenant_id: str,
        ollama_host: str = "http://localhost:11434",
        llm_model: str = "llama3.1:latest",
        embedding_model_name: str = "nomic-ai/nomic-embed-text-v1.5",
        conversation_history: Optional[List[ConversationMessage]] = None
    ):
        self.db = db
        self.tenant_id = tenant_id
        self.ollama_host = ollama_host
        self.llm_model = llm_model
        self.conversation_history = conversation_history or []

        # Use shared singletons
        self.http_client = get_http_client()
        self.embedding_model = get_embedding_model(embedding_model_name)

        # Create context for tools
        self.ctx = RAGContext(
            db=db,
            tenant_id=tenant_id,
            ollama_host=ollama_host,
            http_client=self.http_client,
            embedding_model=self.embedding_model
        )

    async def chat(self, query: str, max_iterations: int = 5) -> str:
        """
        Process a chat query with manual tool calling loop.

        Args:
            query: User's query
            max_iterations: Maximum tool calling iterations

        Returns:
            Generated response as markdown
        """
        logger.info(f"Processing query with manual tool calling: '{query}'")

        # Validate intent
        if not self._is_valid_query(query):
            return (
                "I can only help with questions about code, APIs, implementations, "
                "and technical documentation in the indexed repositories. "
                "Your query seems to be off-topic. Please ask about code-related topics."
            )

        # Build messages (skip system prompt - it seems to interfere with tool calling)
        messages = []

        # Add conversation history
        for msg in self.conversation_history[-4:]:  # Last 2 exchanges
            messages.append({"role": msg.role, "content": msg.content})

        # Add current query
        messages.append({"role": "user", "content": query})

        # Tool calling loop
        for iteration in range(max_iterations):
            logger.info(f"Iteration {iteration + 1}/{max_iterations}")

            # Call Ollama API - force tools on first iteration
            response = await self._call_ollama(messages, force_tools=(iteration == 0))

            logger.info(f"Ollama response - finish_reason: {response.get('finish_reason')}, has_tool_calls: {bool(response.get('tool_calls'))}")

            # Check if we got tool calls
            if response.get("finish_reason") == "tool_calls":
                tool_calls = response.get("tool_calls", [])
                logger.info(f"Model wants to call {len(tool_calls)} tools")

                # Add assistant message with tool calls
                messages.append({
                    "role": "assistant",
                    "content": response.get("content", ""),
                    "tool_calls": tool_calls
                })

                # Execute each tool
                for tool_call in tool_calls:
                    tool_result = await self._execute_tool(tool_call)

                    # Add tool result to messages
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call["id"],
                        "content": json.dumps(tool_result)
                    })

                # Continue loop to get final response
                continue

            else:
                # Got final text response
                final_answer = response.get("content", "")

                # Update conversation history
                self.conversation_history.append(ConversationMessage(role="user", content=query))
                self.conversation_history.append(ConversationMessage(role="assistant", content=final_answer))

                # Keep only last 6 messages
                if len(self.conversation_history) > 6:
                    self.conversation_history = self.conversation_history[-6:]

                logger.info(f"✓ Got final response ({len(final_answer)} chars)")
                return final_answer

        # Max iterations reached
        logger.warning("Max iterations reached without final response")
        return "I apologize, but I couldn't complete the search within the allowed iterations. Please try a more specific query."

    async def _call_ollama(self, messages: List[Dict], force_tools: bool = False) -> Dict:
        """Call Ollama API with tool support."""
        url = f"{self.ollama_host}/v1/chat/completions"

        payload = {
            "model": self.llm_model,
            "messages": messages,
            "tools": TOOL_SCHEMAS,
            "tool_choice": "required" if force_tools else "auto"  # Force tool usage on first call
        }

        logger.debug(f"Sending to Ollama: {len(messages)} messages, {len(TOOL_SCHEMAS)} tools, tool_choice={payload['tool_choice']}")
        logger.debug(f"Last message: {messages[-1]}")

        response = await self.http_client.post(url, json=payload, timeout=60.0)

        if response.status_code != 200:
            logger.error(f"Ollama API error: {response.status_code} - {response.text}")
            raise Exception(f"Ollama API error: {response.status_code}")

        data = response.json()
        choice = data["choices"][0]
        message = choice["message"]

        return {
            "content": message.get("content", ""),
            "tool_calls": message.get("tool_calls", []),
            "finish_reason": choice.get("finish_reason")
        }

    async def _execute_tool(self, tool_call: Dict) -> Any:
        """Execute a tool function and return the result."""
        function_name = tool_call["function"]["name"]
        arguments = json.loads(tool_call["function"]["arguments"])

        logger.info(f"Executing tool: {function_name}({arguments})")

        # Get tool function
        tool_fn = TOOL_FUNCTIONS.get(function_name)
        if not tool_fn:
            logger.error(f"Unknown tool: {function_name}")
            return {"error": f"Unknown tool: {function_name}"}

        # Execute tool
        try:
            result = await tool_fn(self.ctx, **arguments)
            return result
        except Exception as e:
            logger.error(f"Tool execution failed: {e}", exc_info=True)
            return {"error": str(e)}

    def _is_valid_query(self, query: str) -> bool:
        """Simple heuristic to check if query is code-related."""
        query_lower = query.lower()

        # Off-topic keywords
        off_topic = ['weather', 'joke', 'recipe', 'movie', 'sports', 'news']
        if any(word in query_lower for word in off_topic):
            return False

        # Code-related keywords
        code_keywords = [
            'code', 'function', 'class', 'api', 'implement', 'how does',
            'show me', 'example', 'authentication', 'database', 'endpoint',
            'route', 'handler', 'service', 'component', 'module', 'package',
            'bug', 'error', 'fix', 'refactor', 'optimize', 'test'
        ]
        if any(keyword in query_lower for keyword in code_keywords):
            return True

        # Default: allow if query is reasonably long
        return len(query.split()) >= 3
