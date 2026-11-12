#!/usr/bin/env python3
"""Restart the CI Redis service container and verify an existing client recovers.

This is intentionally restricted to CI: it requires exactly one running
redis:8.2-alpine container and restarts that container.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid

from redis.asyncio import Redis


def redis_container_id() -> str:
    result = subprocess.run(
        [
            "docker", "ps", "--filter", "ancestor=redis:8.2-alpine",
            "--format", "{{.ID}}",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    matches = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    if len(matches) != 1:
        raise RuntimeError(
            "Expected exactly one running redis:8.2-alpine CI service container"
        )
    return matches[0]


async def main() -> None:
    redis_url = os.environ.get("REDIS_URL")
    if not redis_url:
        raise RuntimeError("REDIS_URL must point to the CI Redis service")

    container_id = redis_container_id()
    client = Redis.from_url(
        redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    key = f"vcar:redis-process-restart:{uuid.uuid4().hex}"
    try:
        if await client.ping() is not True:
            raise RuntimeError("Redis did not answer the initial PING")
        await client.set(key, "before-restart")

        subprocess.run(
            ["docker", "restart", "--time", "1", container_id],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )

        # Reuse the same redis-py client and pool. A successful PING after the
        # container restart proves the client can recover from the dead socket.
        last_error: Exception | None = None
        for _ in range(30):
            try:
                if await client.ping() is True:
                    last_error = None
                    break
            except Exception as exc:  # transient connection refusal is expected
                last_error = exc
            await asyncio.sleep(1)
        if last_error is not None:
            raise RuntimeError("Redis client did not reconnect after process restart") from last_error

        await client.set(key, "after-restart")
        if await client.get(key) != "after-restart":
            raise RuntimeError("Redis client could not read after process restart")
        print("Redis process restart recovery passed using the original client pool.")
    finally:
        try:
            await client.delete(key)
        except Exception:
            pass
        await client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
