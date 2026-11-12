"""Shared heartbeat freshness check for driver presence surfaces."""
from __future__ import annotations

import math
import time


async def is_driver_online(redis_client, telegram_id: str | int, *, now: float | None = None, ttl_seconds: int = 120) -> bool:
    """A driver is online only while a valid heartbeat remains within its TTL."""
    score = await redis_client.zscore("driver:heartbeat", str(telegram_id))
    if score is None:
        return False
    try:
        heartbeat_at = float(score)
        current_time = time.time() if now is None else float(now)
    except (TypeError, ValueError, OverflowError):
        return False
    if not math.isfinite(heartbeat_at) or not math.isfinite(current_time):
        return False
    age = current_time - heartbeat_at
    return 0 <= age <= ttl_seconds
