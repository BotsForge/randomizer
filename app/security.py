from __future__ import annotations
from passlib.context import CryptContext

# Centralized password hashing utilities to avoid circular imports
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    """Hash a plaintext password using bcrypt via passlib.
    A unique salt is generated internally per hash.
    """
    return _pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Verify a plaintext password against a stored bcrypt hash."""
    return _pwd_context.verify(password, password_hash)
