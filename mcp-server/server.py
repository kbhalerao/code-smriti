"""
CodeSmriti MCP Server
Forever Memory system with code and documentation indexing
Smriti (स्मृति): Sanskrit for "memory, remembrance"
"""

import os
import asyncio
from typing import Any, Optional
from datetime import datetime

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import uvicorn
from loguru import logger
from pydantic import BaseModel

# Import custom modules (to be implemented)
from auth.jwt_middleware import verify_token, JWTBearer
from tools.search import SearchTools
from tools.retrieval import RetrievalTools
from tools.notes import NotesTools
from resources.code_resources import CodeResources

# Configuration
from config import settings

# Initialize FastAPI app
app = FastAPI(
    title="CodeSmriti MCP Server",
    description="Forever Memory MCP system for code and documentation - Smriti: memory/remembrance",
    version="1.0.0"
)

# Add CORS middleware for remote access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize tool handlers
search_tools = SearchTools()
retrieval_tools = RetrievalTools()
notes_tools = NotesTools()
code_resources = CodeResources()


# ============================================================================
# Health Check Endpoints
# ============================================================================

@app.get("/health")
async def health_check():
    """Health check endpoint for Docker"""
    return {
        "status": "healthy",
        "service": "codesmriti-mcp-server",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/")
async def root():
    """Root endpoint with service information"""
    return {
        "service": "CodeSmriti MCP Server",
        "version": "1.0.0",
        "description": "Forever Memory system for code and documentation - Smriti: memory/remembrance",
        "endpoints": {
            "health": "/health",
            "mcp": "/mcp/*",
            "api": "/api/*"
        }
    }


# ============================================================================
# MCP Protocol Endpoints
# ============================================================================

class MCPRequest(BaseModel):
    """MCP request structure"""
    method: str
    params: Optional[dict] = None


class MCPToolCall(BaseModel):
    """MCP tool call structure"""
    name: str
    arguments: dict


@app.post("/mcp/initialize")
async def mcp_initialize():
    """Initialize MCP connection"""
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {
            "tools": {},
            "resources": {
                "subscribe": True,
                "listChanged": True
            },
            "prompts": {}
        },
        "serverInfo": {
            "name": "codesmriti",
            "version": "1.0.0"
        }
    }


@app.post("/mcp/tools/list")
async def mcp_list_tools(token: str = Depends(JWTBearer())):
    """List available MCP tools"""
    return {
        "tools": [
            {
                "name": "search_code",
                "description": "Search for code across indexed repositories using vector similarity and filters",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (natural language or code snippet)"
                        },
                        "repo": {
                            "type": "string",
                            "description": "Filter by repository (owner/repo format)"
                        },
                        "language": {
                            "type": "string",
                            "description": "Filter by programming language (e.g., python, javascript)"
                        },
                        "hashtags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Filter by hashtags"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results",
                            "default": 10
                        }
                    },
                    "required": ["query"]
                }
            },
            {
                "name": "get_code_context",
                "description": "Retrieve specific code file with surrounding context",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository in owner/repo format"
                        },
                        "file_path": {
                            "type": "string",
                            "description": "Path to the file in the repository"
                        },
                        "function_name": {
                            "type": "string",
                            "description": "Specific function or class name (optional)"
                        }
                    },
                    "required": ["repo", "file_path"]
                }
            },
            {
                "name": "find_similar",
                "description": "Find code similar to a given snippet",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "code_snippet": {
                            "type": "string",
                            "description": "Code snippet to find similar implementations"
                        },
                        "language": {
                            "type": "string",
                            "description": "Programming language filter"
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Maximum number of results",
                            "default": 5
                        }
                    },
                    "required": ["code_snippet"]
                }
            },
            {
                "name": "list_repos",
                "description": "List all indexed repositories",
                "inputSchema": {
                    "type": "object",
                    "properties": {}
                }
            },
            {
                "name": "add_note",
                "description": "Add a memory note with hashtags for organization",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "content": {
                            "type": "string",
                            "description": "Note content in markdown format"
                        },
                        "hashtags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Hashtags for categorization"
                        },
                        "project": {
                            "type": "string",
                            "description": "Associated project name"
                        }
                    },
                    "required": ["content"]
                }
            },
            {
                "name": "query_by_hashtag",
                "description": "Retrieve all content tagged with specific hashtags",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "hashtags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Hashtags to search for"
                        },
                        "content_type": {
                            "type": "string",
                            "description": "Filter by type: code, note, or all",
                            "default": "all"
                        }
                    },
                    "required": ["hashtags"]
                }
            }
        ]
    }


@app.post("/mcp/tools/call")
async def mcp_call_tool(tool_call: MCPToolCall, token: str = Depends(JWTBearer())):
    """Execute an MCP tool"""
    try:
        # Route to appropriate tool handler
        if tool_call.name == "search_code":
            result = await search_tools.search_code(**tool_call.arguments)
        elif tool_call.name == "get_code_context":
            result = await retrieval_tools.get_code_context(**tool_call.arguments)
        elif tool_call.name == "find_similar":
            result = await search_tools.find_similar(**tool_call.arguments)
        elif tool_call.name == "list_repos":
            result = await retrieval_tools.list_repos()
        elif tool_call.name == "add_note":
            result = await notes_tools.add_note(**tool_call.arguments)
        elif tool_call.name == "query_by_hashtag":
            result = await notes_tools.query_by_hashtag(**tool_call.arguments)
        else:
            raise HTTPException(status_code=404, detail=f"Tool '{tool_call.name}' not found")

        return {
            "content": [
                {
                    "type": "text",
                    "text": result
                }
            ]
        }
    except Exception as e:
        logger.error(f"Error executing tool {tool_call.name}: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/mcp/resources/list")
async def mcp_list_resources(token: str = Depends(JWTBearer())):
    """List available MCP resources"""
    return {
        "resources": [
            {
                "uri": "repo://{owner}/{repo}/{file_path}",
                "name": "Code File",
                "description": "Direct access to code files in indexed repositories",
                "mimeType": "text/plain"
            }
        ]
    }


@app.post("/mcp/resources/read")
async def mcp_read_resource(request: Request, token: str = Depends(JWTBearer())):
    """Read an MCP resource"""
    body = await request.json()
    uri = body.get("uri")

    if not uri:
        raise HTTPException(status_code=400, detail="URI is required")

    # Parse repo:// URI
    if uri.startswith("repo://"):
        content = await code_resources.read_file(uri)
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/plain",
                    "text": content
                }
            ]
        }
    else:
        raise HTTPException(status_code=400, detail="Invalid URI scheme")


# ============================================================================
# REST API Endpoints for Management
# ============================================================================

@app.post("/api/ingest/trigger", dependencies=[Depends(JWTBearer())])
async def trigger_ingestion(repo: Optional[str] = None):
    """Trigger manual re-indexing of repositories"""
    # This will send a message to the ingestion worker
    # Implementation will depend on the message queue/communication mechanism
    return {
        "status": "triggered",
        "repo": repo or "all",
        "timestamp": datetime.utcnow().isoformat()
    }


@app.get("/api/status", dependencies=[Depends(JWTBearer())])
async def system_status():
    """Get system status and statistics"""
    # Query Couchbase for statistics
    stats = await retrieval_tools.get_system_stats()
    return stats


@app.post("/api/notes", dependencies=[Depends(JWTBearer())])
async def create_note(content: str, hashtags: list[str] = [], project: Optional[str] = None):
    """Create a new memory note via REST API"""
    result = await notes_tools.add_note(content=content, hashtags=hashtags, project=project)
    return {"status": "created", "result": result}


# ============================================================================
# Error Handlers
# ============================================================================

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """Handle HTTP exceptions"""
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": exc.detail}
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception):
    """Handle general exceptions"""
    logger.error(f"Unhandled exception: {exc}")
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )


# ============================================================================
# Main Entry Point
# ============================================================================

if __name__ == "__main__":
    # Configure logging
    logger.add(
        "logs/mcp-server.log",
        rotation="100 MB",
        retention="30 days",
        level=settings.log_level
    )

    logger.info("Starting CodeSmriti MCP Server")
    logger.info(f"Couchbase: {settings.couchbase_host}:{settings.couchbase_port}")
    logger.info(f"Ollama: {settings.ollama_host}")

    # Run the server
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=8080,
        reload=True,  # Development only
        log_level=settings.log_level.lower()
    )
