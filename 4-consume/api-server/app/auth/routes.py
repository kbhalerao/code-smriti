"""Minimal auth routes for internal LAN access"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .utils import create_access_token

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
    Simple login endpoint for internal use.

    For now, accepts any username/password and returns a JWT token.
    The token includes the username and tenant_id.

    Example:
        curl -X POST http://localhost:8000/api/auth/login \
          -H "Content-Type: application/json" \
          -d '{"username": "demo", "password": "demo"}'
    """
    # For internal use - minimal security
    # Accept any username/password, generate token
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
