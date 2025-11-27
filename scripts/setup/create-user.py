#!/usr/bin/env python3
"""
CLI script to create new users with random passwords for CodeSmriti.

Usage:
    python scripts/setup/create-user.py user@example.com
    python scripts/setup/create-user.py user@example.com --password mypassword
"""

import argparse
import os
import secrets
import string
import sys
import uuid
from datetime import datetime

# Add the api-server app to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..', 'services', 'api-server'))

from app.config import settings
from app.auth.utils import get_password_hash, create_access_token
from app.database.couchbase_client import get_cluster, get_users_collection
from app.models import UserDocument


def generate_random_password(length: int = 16) -> str:
    """Generate a secure random password."""
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def check_email_exists(email: str) -> bool:
    """Check if a user with this email already exists."""
    cluster = get_cluster()
    query = """
        SELECT META().id as doc_id
        FROM users
        WHERE email = $1 AND type = 'user'
        LIMIT 1
    """
    result = cluster.query(query, email)
    return len(list(result)) > 0


def create_user(email: str, password: str) -> tuple[str, str]:
    """
    Create a new user in the database.

    Args:
        email: User's email address
        password: Plain text password (will be hashed)

    Returns:
        Tuple of (user_id, jwt_token)
    """
    # Check if email already exists
    if check_email_exists(email):
        raise ValueError(f"User with email '{email}' already exists")

    # Generate user ID
    user_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat() + "Z"

    # Create user document
    user_doc = UserDocument(
        type="user",
        user_id=user_id,
        email=email,
        password_hash=get_password_hash(password),
        github_pat_encrypted=None,
        repos=[],
        quota_max_repos=10,
        quota_max_chunks=100000,
        created_at=now,
        updated_at=now,
        last_login=None,
    )

    # Insert into database
    collection = get_users_collection()
    doc_key = f"user::{user_id}"
    collection.insert(doc_key, user_doc.model_dump())

    # Generate JWT token
    token_data = {
        "sub": user_id,
        "user_id": user_id,
        "email": email,
        "tenant_id": "code_kosha",
    }
    token = create_access_token(data=token_data)

    return user_id, token


def main():
    parser = argparse.ArgumentParser(
        description="Create a new CodeSmriti user with email/password authentication"
    )
    parser.add_argument(
        "email",
        help="User's email address"
    )
    parser.add_argument(
        "--password", "-p",
        help="Password (if not provided, a random one will be generated)"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Only output the password and token (for scripting)"
    )

    args = parser.parse_args()

    # Validate email format (basic check)
    if "@" not in args.email or "." not in args.email.split("@")[-1]:
        print(f"Error: Invalid email format: {args.email}", file=sys.stderr)
        sys.exit(1)

    # Generate or use provided password
    password = args.password or generate_random_password()

    try:
        user_id, token = create_user(args.email, password)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error creating user: {e}", file=sys.stderr)
        sys.exit(1)

    if args.quiet:
        # Machine-readable output for scripting
        print(f"PASSWORD={password}")
        print(f"TOKEN={token}")
        print(f"USER_ID={user_id}")
    else:
        # Human-readable output
        print()
        print("=" * 60)
        print("User Created Successfully!")
        print("=" * 60)
        print()
        print(f"  Email:     {args.email}")
        print(f"  User ID:   {user_id}")
        print(f"  Password:  {password}")
        print()
        print("-" * 60)
        print("JWT Token (valid for 24 hours):")
        print("-" * 60)
        print(token)
        print("-" * 60)
        print()
        print("Login example:")
        print()
        print(f'  curl -X POST http://localhost:8000/api/auth/login \\')
        print(f'    -H "Content-Type: application/json" \\')
        print(f'    -d \'{{"email": "{args.email}", "password": "{password}"}}\'')
        print()


if __name__ == "__main__":
    main()
