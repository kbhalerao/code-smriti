"""
Authentication module for TotalRecall MCP Server
"""

from .jwt_middleware import verify_token, JWTBearer, create_access_token

__all__ = ["verify_token", "JWTBearer", "create_access_token"]
