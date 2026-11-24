"""Redis-backed, fail-closed rate limiting for administrator login."""
from __future__ import annotations

import hashlib

from fastapi import HTTPException

_WINDOW_SECONDS = 900
_IP_LIMIT = 20
_IDENTITY_LIMIT = 5

_CONSUME_SCRIPT = """
local ip_count = redis.call('INCR', KEYS[1])
if ip_count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
local identity_count = redis.call('INCR', KEYS[2])
if identity_count == 1 then redis.call('EXPIRE', KEYS[2], ARGV[1]) end
local ip_ttl = redis.call('TTL', KEYS[1])
local identity_ttl = redis.call('TTL', KEYS[2])
return {ip_count, identity_count, math.max(ip_ttl, identity_ttl)}
"""

def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()


def _keys(client_ip: str, username: str) -> tuple[str, str]:
    ip_digest = _digest(client_ip or "unknown")
    identity_digest = _digest(f"{client_ip or 'unknown'}\\0{username}")
    return (
        f"vcar:auth:login:ip:{ip_digest}",
        f"vcar:auth:login:identity:{identity_digest}",
    )


async def consume_login_attempt(redis_client, client_ip: str, username: str) -> None:
    """Reserve an attempt atomically; Redis errors deliberately propagate."""
    ip_key, identity_key = _keys(client_ip, username)
    ip_count, identity_count, ttl = await redis_client.eval(
        _CONSUME_SCRIPT,
        2,
        ip_key,
        identity_key,
        _WINDOW_SECONDS,
    )
    if int(ip_count) > _IP_LIMIT or int(identity_count) > _IDENTITY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="登录尝试过于频繁，请稍后再试",
            headers={"Retry-After": str(max(1, int(ttl)))},
        )


async def clear_identity_login_attempts(redis_client, client_ip: str, username: str) -> None:
    """Clear the username-specific counter after successful authentication."""
    _, identity_key = _keys(client_ip, username)
    await redis_client.delete(identity_key)
