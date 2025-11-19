"""
JWT Authentication Middleware
Provides token generation, verification, and FastAPI dependency injection
"""

import jwt
from datetime import datetime, timedelta
from typing import Optional
from fastapi import Request, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from loguru import logger

from config import settings


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token

    Args:
        data: Payload data to encode in the token
        expires_delta: Optional custom expiration time

    Returns:
        Encoded JWT token string
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=settings.jwt_expiration_hours)

    to_encode.update({"exp": expire, "iat": datetime.utcnow()})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm
    )

    return encoded_jwt


def verify_token(token: str) -> dict:
    """
    Verify and decode a JWT token

    Args:
        token: JWT token string

    Returns:
        Decoded token payload

    Raises:
        HTTPException: If token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        return payload
    except jwt.ExpiredSignatureError:
        logger.warning("Token has expired")
        raise HTTPException(status_code=401, detail="Token has expired")
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        raise HTTPException(status_code=401, detail="Invalid token")


class JWTBearer(HTTPBearer):
    """
    FastAPI dependency for JWT authentication
    Usage: @app.get("/protected", dependencies=[Depends(JWTBearer())])
    """

    def __init__(self, auto_error: bool = True):
        super(JWTBearer, self).__init__(auto_error=auto_error)

    async def __call__(self, request: Request) -> str:
        """
        Validate the Authorization header and verify the JWT token

        Returns:
            The token string if valid

        Raises:
            HTTPException: If authentication fails
        """
        credentials: HTTPAuthorizationCredentials = await super(JWTBearer, self).__call__(request)

        if credentials:
            if not credentials.scheme == "Bearer":
                raise HTTPException(
                    status_code=403,
                    detail="Invalid authentication scheme"
                )

            token = credentials.credentials
            payload = verify_token(token)

            # Add user info to request state for downstream handlers
            request.state.user = payload

            return token
        else:
            raise HTTPException(
                status_code=403,
                detail="Invalid authorization code"
            )


def generate_api_key(user_id: str, user_email: str, scopes: list[str] = None) -> str:
    """
    Generate an API key (JWT token) for a user

    Args:
        user_id: Unique user identifier
        user_email: User's email address
        scopes: List of permission scopes (optional)

    Returns:
        JWT token that can be used as an API key
    """
    if scopes is None:
        scopes = ["read", "write"]

    payload = {
        "sub": user_id,
        "email": user_email,
        "scopes": scopes,
        "type": "api_key"
    }

    # API keys have longer expiration (30 days)
    return create_access_token(payload, expires_delta=timedelta(days=30))
