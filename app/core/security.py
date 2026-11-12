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


def is_valid_telegram_subject(subject: object) -> bool:
    """Return whether a JWT subject is a positive Telegram ID fitting BIGINT."""
    return (
        isinstance(subject, str)
        and subject.isascii()
        and subject.isdecimal()
        and len(subject) <= 19
        and 0 < int(subject) <= 2**63 - 1
    )


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


def require_strong_secret_key(key: str) -> str:
    """Reject missing or placeholder signing keys before any JWT verification."""
    if not isinstance(key, str) or len(key.encode()) < 32 or key.startswith("REPLACE_"):
        raise HTTPException(status_code=503, detail="Authentication is not configured")
    return key


def _secret_key() -> str:
    return require_strong_secret_key(os.getenv("SECRET_KEY", ""))


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
        payload = jwt.decode(
            credentials.credentials, _secret_key(), algorithms=[_ALGORITHM],
            options={"require": ["exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired",
                            headers={"WWW-Authenticate": "Bearer"})
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token",
                            headers={"WWW-Authenticate": "Bearer"})
    if not payload.get("sub") or payload.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return payload

import json
import time
from urllib.parse import parse_qsl


def verify_telegram_webapp_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age_seconds: int = 86_400,
) -> dict[str, Any]:
    """Validate Telegram WebApp initData using the bot-token HMAC scheme."""
    if not init_data or not bot_token:
        raise ValueError("Telegram WebApp authentication is not configured")
    pairs = parse_qsl(init_data, keep_blank_values=True, strict_parsing=True)
    values: dict[str, str] = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("Duplicate Telegram initData field")
        values[key] = value
    supplied_hash = values.pop("hash", "")
    if len(supplied_hash) != 64:
        raise ValueError("Invalid Telegram initData hash")
    data_check_string = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret_key = hmac.new(bot_token.encode(), b"WebAppData", hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected_hash, supplied_hash):
        raise ValueError("Telegram initData signature mismatch")
    try:
        auth_date = int(values["auth_date"])
        user = json.loads(values["user"])
    except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise ValueError("Telegram initData is missing required user data") from exc
    now = int(time.time())
    if auth_date > now + 60 or now - auth_date > max_age_seconds:
        raise ValueError("Telegram initData has expired")
    telegram_id = user.get("id") if isinstance(user, dict) else None
    if (
        not isinstance(telegram_id, int)
        or isinstance(telegram_id, bool)
        or not 0 < telegram_id <= 2**63 - 1
    ):
        raise ValueError("Telegram initData user is invalid")
    return {"user": user, "auth_date": auth_date}


async def verify_driver_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict[str, Any]:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(status_code=401, detail="Bearer token required",
                            headers={"WWW-Authenticate": "Bearer"})
    try:
        payload = jwt.decode(
            credentials.credentials, _secret_key(), algorithms=[_ALGORITHM],
            options={"require": ["exp"]},
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired",
                            headers={"WWW-Authenticate": "Bearer"})
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token",
                            headers={"WWW-Authenticate": "Bearer"})
    if payload.get("role") != "driver" or not is_valid_telegram_subject(payload.get("sub")):
        raise HTTPException(status_code=403, detail="Driver permissions required")
    return payload
