"""
Authentication routes for user registration and login.
"""
import uuid
from datetime import datetime

from couchbase.exceptions import DocumentExistsException, DocumentNotFoundException
from fastapi import APIRouter, HTTPException, status
from loguru import logger

from .utils import create_access_token, create_refresh_token, get_password_hash, verify_password, verify_token
from ..config import settings
from ..database import get_cluster, get_users_collection
from ..models import (
    LoginRequest,
    RefreshTokenRequest,
    RegisterRequest,
    TokenResponse,
    AuthResponse,
    SafeUserInfo,
    UserDocument,
)

router = APIRouter()


def _user_doc_to_safe_info(user_doc: dict) -> SafeUserInfo:
    """Convert user document to safe user info (no password hash)."""
    return SafeUserInfo(
        user_id=user_doc["user_id"],
        email=user_doc["email"],
        repos=user_doc.get("repos", []),
        quota_max_repos=user_doc.get("quota_max_repos", 10),
        quota_max_chunks=user_doc.get("quota_max_chunks", 100000),
        created_at=user_doc["created_at"],
        last_login=user_doc.get("last_login"),
    )


@router.post("/register", response_model=AuthResponse)
async def register(request: RegisterRequest):
    """
    Registration is disabled. Contact administrator for account creation.
    """
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Registration is disabled. Contact administrator for account access.",
    )


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest):
    """
    Login with email and password.

    Verifies credentials and returns a JWT token upon successful authentication.

    Example:
        curl -X POST http://localhost:8000/api/auth/login \
          -H "Content-Type: application/json" \
          -d '{"email": "user@example.com", "password": "securepassword123"}'
    """
    # Look up user by email
    cluster = get_cluster()
    query = """
        SELECT META().id as doc_id, users.*
        FROM users
        WHERE email = $1 AND type = 'user'
        LIMIT 1
    """
    try:
        result = cluster.query(query, request.email)
        users = list(result)
    except Exception as e:
        logger.error(f"Error querying user: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Database error during login",
        )

    if not users:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_doc = users[0]

    # Verify password
    if not verify_password(request.password, user_doc["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Update last_login timestamp (non-blocking, best effort)
    try:
        collection = get_users_collection()
        doc_key = user_doc["doc_id"]
        now = datetime.utcnow().isoformat() + "Z"
        # Get current doc and update
        current = collection.get(doc_key).content_as[dict]
        current["last_login"] = now
        current["updated_at"] = now
        collection.replace(doc_key, current)
    except Exception as e:
        # Non-critical, just log it
        logger.warning(f"Failed to update last_login: {e}")

    # Generate JWT tokens
    token_data = {
        "sub": user_doc["user_id"],
        "user_id": user_doc["user_id"],
        "email": user_doc["email"],
        "tenant_id": "code_kosha",
    }
    access_token = create_access_token(data=token_data)
    refresh_token = create_refresh_token(data=token_data)

    logger.info(f"User logged in: {request.email}")

    return AuthResponse(
        success=True,
        token=access_token,
        refresh_token=refresh_token,
        user=_user_doc_to_safe_info(user_doc),
    )


@router.get("/me")
async def get_current_user_info():
    """Get current user info (placeholder - requires auth middleware)"""
    return {"message": "Use JWT token for authentication"}


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(request: RefreshTokenRequest):
    """
    Refresh an access token using a valid refresh token.

    Returns a new access token without requiring re-authentication.

    Example:
        curl -X POST http://localhost:8000/api/auth/refresh \
          -H "Content-Type: application/json" \
          -d '{"refresh_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."}'
    """
    # Verify the refresh token
    payload = verify_token(request.refresh_token)

    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Ensure it's a refresh token, not an access token
    if payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token type - refresh token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Create new access token with same user data
    token_data = {
        "sub": payload["sub"],
        "user_id": payload["user_id"],
        "email": payload["email"],
        "tenant_id": payload.get("tenant_id", "code_kosha"),
    }
    new_access_token = create_access_token(data=token_data)

    logger.info(f"Token refreshed for user: {payload['email']}")

    return TokenResponse(
        access_token=new_access_token,
        token_type="bearer",
    )
