import httpx
from mcp.server.fastmcp import FastMCP

# Initialize FastMCP server
mcp = FastMCP("CodeSmriti RAG")

# Configuration
# Using the test endpoint which bypasses auth as requested
CHAT_API_URL = "https://macstudio.local/api/chat/test"
SEARCH_API_URL = "https://macstudio.local/api/chat/search"

@mcp.tool()
async def search_codebase(
    query: str, 
    limit: int = 5, 
    repo_filter: str = None,
    file_pattern: str = None
) -> str:
    """
    Search for raw code chunks and documents without LLM generation.
    
    Use this tool when you want to:
    - Get raw code context to analyze yourself
    - Find specific files or functions
    - Explore the codebase structure
    
    Args:
        query: The search query (semantic or keyword).
        limit: Number of results to return (default: 5, max: 20).
        repo_filter: Optional repository name to filter by.
        file_pattern: Optional file path pattern (e.g. "*.py", "src/").
    """
    async with httpx.AsyncClient(verify=False) as client:
        try:
            payload = {
                "query": query,
                "limit": limit,
                "doc_type": "code_chunk"
            }
            if repo_filter:
                payload["repo_filter"] = repo_filter
            if file_pattern:
                payload["file_path_pattern"] = file_pattern
                
            response = await client.post(
                SEARCH_API_URL,
                json=payload,
                timeout=30.0
            )
            response.raise_for_status()
            data = response.json()
            
            # Format results for the LLM
            results = data.get("results", [])
            if not results:
                return "No results found."
                
            formatted = f"Found {len(results)} results for '{query}':\n\n"
            for i, res in enumerate(results, 1):
                formatted += f"--- Result {i} ---\n"
                formatted += f"File: {res.get('repo_id')}/{res.get('file_path')}\n"
                formatted += f"Score: {res.get('score'):.3f}\n"
                formatted += f"Content:\n```\n{res.get('content')}\n```\n\n"
                
            return formatted
        except Exception as e:
            return f"Error searching codebase: {str(e)}"

@mcp.tool()
async def ask_codebase(query: str) -> str:
    """
    Ask a question and get a RAG-generated answer with citations.
    
    Use this tool when you want:
    - A high-level explanation or summary
    - To understand how different parts connect
    - A direct answer to a question
    
    Args:
        query: The natural language question.
    """
    # Disable SSL verification for local development URLs
    async with httpx.AsyncClient(verify=False) as client:
        try:
            response = await client.post(
                CHAT_API_URL,
                json={
                    "query": query, 
                    "stream": False,
                },
                timeout=120.0
            )
            response.raise_for_status()
            data = response.json()
            return data.get("answer", "No answer received.")
        except Exception as e:
            return f"Error querying RAG agent: {str(e)}"

if __name__ == "__main__":
    mcp.run()
