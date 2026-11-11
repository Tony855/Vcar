"""Security primitives shared by the Vcar API."""
from __future__ import annotations
import base64
import hashlib
import hmac
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_ALGORITHM = "HS256"
_PASSWORD_ITERATIONS = 310_000
_bearer = HTTPBearer(auto_error=False)


def hash_password(password: str, *, salt: str | None = None) -> str:
    if not password or len(password) < 12:
        raise ValueError("Password must contain at least 12 characters")
    salt_value = salt or secrets.token_urlsafe(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt_value.encode(), _PASSWORD_ITERATIONS)
    encoded = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    return f"pbkdf2_sha256${_PASSWORD_ITERATIONS}${salt_value}${encoded}"


def verify_password(password: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected = encoded_hash.split("$", 3)
        iterations = int(iterations_text)
        if algorithm != "pbkdf2_sha256" or not 100_000 <= iterations <= 2_000_000:
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
        actual = base64.urlsafe_b64encode(digest).decode().rstrip("=")
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError, AttributeError):
        return False


def _secret_key() -> str:
    key = os.getenv("SECRET_KEY", "")
    if len(key.encode()) < 32 or key.startswith("REPLACE_"):
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    return key


def create_access_token(subject: str, role: str = "admin", expires_minutes: int = 30) -> str:
    now = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "sub": subject, "role": role, "iat": now,
        "exp": now + timedelta(minutes=expires_minutes),
    }
    return jwt.encode(payload, _secret_key(), algorithm=_ALGORITHM)


async def verify_token(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(credentials.credentials, _secret_key(), algorithms=[_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired",
                            headers={"WWW-Authenticate": "Bearer"})
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token",
                            headers={"WWW-Authenticate": "Bearer"})
    if not payload.get("sub") or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return payload
