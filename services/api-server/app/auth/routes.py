"""Minimal auth routes for internal LAN access"""
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from .utils import create_access_token
from ..config import settings

router = APIRouter()


class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


@router.post("/login", response_model=TokenResponse)
async def login(request: LoginRequest):
    """
    Login endpoint with single-user authentication.

    Verifies credentials against configured API_USERNAME and API_PASSWORD.
    Returns a JWT token upon successful authentication.

    Example:
        curl -X POST http://localhost:8000/api/auth/login \
          -H "Content-Type: application/json" \
          -d '{"username": "codesmriti", "password": "your-password"}'
    """
    # Verify credentials against configured values
    if request.username != settings.api_username or request.password != settings.api_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Generate JWT token with user info
    token_data = {
        "sub": request.username,
        "username": request.username,
        "tenant_id": "code_kosha"  # Default tenant for internal use
    }

    access_token = create_access_token(data=token_data)

    return TokenResponse(
        access_token=access_token,
        token_type="bearer"
    )


@router.get("/me")
async def get_current_user_info():
    """Get current user info (placeholder)"""
    return {"message": "Use JWT token for authentication"}
