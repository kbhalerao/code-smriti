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
    Verify and decode a JWT token.

    Args:
        token: JWT token string

    Returns:
        dict: Decoded payload if valid, None if invalid
    """
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm]
        )
        return payload
    except JWTError:
        return None


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
