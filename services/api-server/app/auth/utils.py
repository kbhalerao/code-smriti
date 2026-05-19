"""
Authentication and encryption utilities.
"""

import base64
import os
from datetime import datetime, timedelta
from typing import Optional

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
import bcrypt
from jose import JWTError, jwt
from loguru import logger

from ..config import settings

# AES encryption constants
ALGORITHM = "aes-256-cbc"
IV_LENGTH = 16
SALT_LENGTH = 32
KEY_LENGTH = 32
ITERATIONS = 100000


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt."""
    password_bytes = password.encode('utf-8')
    salt = bcrypt.gensalt(rounds=12)
    hashed = bcrypt.hashpw(password_bytes, salt)
    return hashed.decode('utf-8')


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against its hash."""
    password_bytes = plain_password.encode('utf-8')
    hashed_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hashed_bytes)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token.

    Args:
        data: Payload to encode in the token
        expires_delta: Optional custom expiration time

    Returns:
        str: Encoded JWT token
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(hours=settings.jwt_expiry_hours)

    to_encode.update({"exp": expire, "type": "access"})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm
    )

    return encoded_jwt


def create_refresh_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT refresh token with longer expiry.

    Args:
        data: Payload to encode in the token (should include user_id)
        expires_delta: Optional custom expiration time

    Returns:
        str: Encoded JWT refresh token
    """
    to_encode = data.copy()

    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(days=settings.jwt_refresh_expiry_days)

    to_encode.update({"exp": expire, "type": "refresh"})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm
    )

    return encoded_jwt


def verify_token(token: str) -> Optional[dict]:
    """
    Verify a Bearer credential — either a JWT or a Personal Access Token.

    PATs carry the ``cos_pat_`` prefix and are minted in the Chief of
    Staff web UI; they are resolved against the shared ``api_token``
    store and hydrated into a JWT-shaped claim set, so downstream code
    (``get_current_user``, the RAG agent) never has to care which
    credential type was used. JWTs are decoded and verified against the
    shared secret.

    Args:
        token: JWT or PAT string

    Returns:
        dict: Claim payload if valid, None if invalid
    """
    # Lazy import to keep the auth-token DB lookup off the import path
    # of code that only ever decodes JWTs.
    from .api_tokens import PAT_PREFIX

    if token.startswith(PAT_PREFIX):
        return _verify_pat(token)

    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError:
        return None


def _verify_pat(token: str) -> Optional[dict]:
    """Resolve a Personal Access Token to a JWT-shaped claim set.

    Returns None if the token is unknown/revoked, or if its owner has
    no code-smriti account.
    """
    from .api_tokens import find_api_token_by_hash, hash_token, touch_api_token

    record = find_api_token_by_hash(hash_token(token))
    if not record:
        return None

    claims = _resolve_user_claims(record["user_id"])
    if claims is None:
        logger.warning(
            f"PAT {record['id']} is valid but owner '{record['user_id']}' "
            f"has no code-smriti account"
        )
        return None

    # Best-effort last_used_at update; never block auth on it.
    try:
        touch_api_token(record["id"])
    except Exception:
        pass

    claims["_auth"] = "pat"
    claims["_token_id"] = record["id"]
    return claims


def _resolve_user_claims(email: str) -> Optional[dict]:
    """Build a JWT-shaped claim set for a user, looked up by email.

    Mirrors the payload minted by the ``/login`` route so PAT and
    password auth are indistinguishable to downstream dependencies.
    Returns None if no such user exists.
    """
    from ..database import get_cluster

    query = (
        "SELECT users.* FROM users "
        "WHERE email = $1 AND type = 'user' LIMIT 1"
    )
    try:
        rows = list(get_cluster().query(query, email))
    except Exception as e:
        logger.error(f"PAT user lookup failed for {email}: {e}")
        return None
    if not rows:
        return None

    user = rows[0]
    return {
        "sub": user["user_id"],
        "user_id": user["user_id"],
        "email": user["email"],
        "tenant_id": "code_kosha",
        "type": "access",
    }


def _derive_key(salt: bytes) -> bytes:
    """Derive an encryption key from the master key using PBKDF2."""
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=ITERATIONS,
    )

    master_key = bytes.fromhex(settings.aes_encryption_key)
    return kdf.derive(master_key)


def encrypt_github_pat(plaintext: str) -> str:
    """
    Encrypt a GitHub Personal Access Token using AES-256-CBC.

    Args:
        plaintext: The PAT to encrypt

    Returns:
        str: Encrypted data in format "salt:iv:ciphertext" (base64 encoded)
    """
    # Generate random salt and IV
    salt = os.urandom(SALT_LENGTH)
    iv = os.urandom(IV_LENGTH)

    # Derive encryption key
    key = _derive_key(salt)

    # Create cipher and encrypt
    cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
    encryptor = cipher.encryptor()

    # Pad plaintext to block size (16 bytes for AES)
    plaintext_bytes = plaintext.encode('utf-8')
    padding_length = 16 - (len(plaintext_bytes) % 16)
    padded_plaintext = plaintext_bytes + (bytes([padding_length]) * padding_length)

    ciphertext = encryptor.update(padded_plaintext) + encryptor.finalize()

    # Return format: salt:iv:ciphertext (all base64)
    return f"{base64.b64encode(salt).decode()}:{base64.b64encode(iv).decode()}:{base64.b64encode(ciphertext).decode()}"


def decrypt_github_pat(encrypted_data: str) -> Optional[str]:
    """
    Decrypt a GitHub PAT encrypted with encrypt_github_pat().

    Args:
        encrypted_data: Encrypted string in format "salt:iv:ciphertext"

    Returns:
        str: Decrypted PAT, or None if decryption fails
    """
    try:
        # Parse the encrypted data
        parts = encrypted_data.split(':')
        if len(parts) != 3:
            return None

        salt = base64.b64decode(parts[0])
        iv = base64.b64decode(parts[1])
        ciphertext = base64.b64decode(parts[2])

        # Derive key
        key = _derive_key(salt)

        # Create cipher and decrypt
        cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
        decryptor = cipher.decryptor()

        padded_plaintext = decryptor.update(ciphertext) + decryptor.finalize()

        # Remove padding
        padding_length = padded_plaintext[-1]
        plaintext_bytes = padded_plaintext[:-padding_length]

        return plaintext_bytes.decode('utf-8')

    except Exception:
        return None
