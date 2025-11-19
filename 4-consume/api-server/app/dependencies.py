"""
FastAPI dependencies for authentication and authorization.
"""

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .auth.utils import verify_token

# HTTP Bearer token scheme
security = HTTPBearer()


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)]
) -> dict:
    """
    Dependency to get and verify the current authenticated user from JWT.

    Args:
        credentials: HTTP Bearer credentials from Authorization header

    Returns:
        dict: Decoded JWT payload containing user_id and email

    Raises:
        HTTPException: 401 if token is invalid or expired
    """
    token = credentials.credentials

    payload = verify_token(token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return payload


# Type alias for dependency injection
CurrentUser = Annotated[dict, Depends(get_current_user)]
