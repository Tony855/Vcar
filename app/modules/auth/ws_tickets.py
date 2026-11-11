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
        return "passenger" if order_id > 0 else None
    return None


async def issue_ws_ticket(access_token: str, scope: str) -> str:
    expected_role = _scope_role(scope)
    if expected_role is None:
        raise ValueError("Unsupported WebSocket scope")
    try:
        claims = jwt.decode(access_token, settings.secret_key, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise ValueError("Invalid or expired access token") from exc
    if claims.get("role") != expected_role or not claims.get("sub"):
        raise PermissionError("Access token cannot open this WebSocket scope")

    ticket = secrets.token_urlsafe(32)
    payload = json.dumps({
        "sub": str(claims["sub"]),
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
    if claims.get("scope") != expected_scope or claims.get("role") != _scope_role(expected_scope):
        return None
    if not claims.get("sub"):
        return None
    return claims
