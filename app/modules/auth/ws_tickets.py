"""Short-lived, single-use WebSocket tickets.

Browser WebSocket APIs cannot attach Authorization headers. This avoids placing
reusable JWTs in query strings and reverse-proxy access logs.
"""
from __future__ import annotations

import hashlib
import json
import secrets

import jwt

from app.core.config import settings
from app.core.security import is_valid_telegram_subject, require_strong_secret_key
from app.db.core import db


def _ticket_key(ticket: str) -> str:
    digest = hashlib.sha256(ticket.encode()).hexdigest()
    return f"ws-ticket:{digest}"


def _scope_role(scope: str) -> str | None:
    if scope == "admin-fleet":
        return "admin"
    if scope == "driver-location":
        return "driver"
    if scope.startswith("passenger-order:"):
        try:
            order_id = int(scope.split(":", 1)[1])
        except ValueError:
            return None
        return "passenger" if 0 < order_id <= 2**63 - 1 else None
    return None


def _valid_subject_for_role(subject: object, role: str) -> bool:
    if role in {"driver", "passenger"}:
        return is_valid_telegram_subject(subject)
    return isinstance(subject, str) and 0 < len(subject) <= 128


async def issue_ws_ticket(access_token: str, scope: str) -> str:
    expected_role = _scope_role(scope)
    if expected_role is None:
        raise ValueError("Unsupported WebSocket scope")
    secret_key = require_strong_secret_key(settings.secret_key)
    try:
        claims = jwt.decode(
            access_token, secret_key, algorithms=["HS256"],
            options={"require": ["exp"]},
        )
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid or expired access token") from exc
    subject = claims.get("sub")
    if (
        claims.get("role") != expected_role
        or not _valid_subject_for_role(subject, expected_role)
    ):
        raise PermissionError("Access token cannot open this WebSocket scope")

    ticket = secrets.token_urlsafe(32)
    payload = json.dumps({
        "sub": subject,
        "role": expected_role,
        "scope": scope,
    })
    stored = await db.redis.set(_ticket_key(ticket), payload, ex=30, nx=True)
    if not stored:
        raise RuntimeError("Unable to issue WebSocket ticket")
    return ticket


async def consume_ws_ticket(ticket: str, expected_scope: str) -> dict | None:
    if not ticket or len(ticket) > 128:
        return None
    raw = await db.redis.getdel(_ticket_key(ticket))
    if not raw:
        return None
    try:
        claims = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(claims, dict):
        return None
    expected_role = _scope_role(expected_scope)
    if (
        expected_role is None
        or claims.get("scope") != expected_scope
        or claims.get("role") != expected_role
        or not _valid_subject_for_role(claims.get("sub"), expected_role)
    ):
        return None
    return claims
