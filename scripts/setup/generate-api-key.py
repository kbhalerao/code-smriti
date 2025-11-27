#!/usr/bin/env python3
"""
Generate API keys (JWT tokens) for CodeSmriti users
Smriti (स्मृति): Sanskrit for "memory, remembrance"
"""

import os
import sys
from datetime import datetime, timedelta
import jwt

# Add parent directory to path to import config
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'mcp-server'))

try:
    from config import settings
except ImportError:
    print("Error: Could not import settings from mcp-server/config.py")
    print("Make sure you're running this from the TotalRecall directory")
    sys.exit(1)


def generate_api_key(user_id: str, user_email: str, scopes: list = None, days: int = 30) -> str:
    """
    Generate an API key (JWT token) for a user

    Args:
        user_id: Unique user identifier
        user_email: User's email address
        scopes: List of permission scopes
        days: Number of days until expiration

    Returns:
        JWT token that can be used as an API key
    """
    if scopes is None:
        scopes = ["read", "write"]

    payload = {
        "sub": user_id,
        "email": user_email,
        "scopes": scopes,
        "type": "api_key",
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + timedelta(days=days)
    }

    token = jwt.encode(
        payload,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm
    )

    return token


def main():
    """Main entry point"""
    print("=" * 60)
    print("CodeSmriti API Key Generator")
    print("=" * 60)
    print()

    # Get user input
    user_id = input("Enter user ID (e.g., user123): ").strip()
    if not user_id:
        print("Error: User ID is required")
        sys.exit(1)

    user_email = input("Enter user email: ").strip()
    if not user_email:
        print("Error: User email is required")
        sys.exit(1)

    print()
    print("Select scopes (comma-separated):")
    print("  - read: Can search and retrieve code/docs")
    print("  - write: Can add notes and trigger ingestion")
    print("  - admin: Can manage system settings")
    scopes_input = input("Scopes [read,write]: ").strip() or "read,write"
    scopes = [s.strip() for s in scopes_input.split(",")]

    days_input = input("Expiration (days) [30]: ").strip() or "30"
    days = int(days_input)

    # Generate the key
    api_key = generate_api_key(user_id, user_email, scopes, days)

    print()
    print("=" * 60)
    print("API Key Generated Successfully!")
    print("=" * 60)
    print()
    print(f"User ID:     {user_id}")
    print(f"Email:       {user_email}")
    print(f"Scopes:      {', '.join(scopes)}")
    print(f"Expires:     {days} days ({datetime.utcnow() + timedelta(days=days)})")
    print()
    print("API Key:")
    print("-" * 60)
    print(api_key)
    print("-" * 60)
    print()
    print("Usage:")
    print("  Include in requests as: Authorization: Bearer <api_key>")
    print()
    print("Example:")
    print(f'  curl -H "Authorization: Bearer {api_key[:50]}..." \\')
    print("       http://localhost:8080/mcp/tools/list")
    print()


if __name__ == "__main__":
    main()
