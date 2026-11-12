"""Best-effort, ordered Redis projection for GPS data already committed to PostgreSQL."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)

# A single Redis script prevents concurrent requests from interleaving GEO,
# heartbeat and Pub/Sub writes. A late request with an older presence timestamp
# is ignored, so it cannot move a driver's realtime position backwards.
_PUBLISH_DRIVER_LOCATION = """
local previous = redis.call('ZSCORE', KEYS[2], ARGV[3])
local incoming = tonumber(ARGV[4])
if previous and tonumber(previous) >= incoming then
    return 0
end

redis.call('GEOADD', KEYS[1], ARGV[1], ARGV[2], ARGV[3])
redis.call('ZADD', KEYS[2], incoming, ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', incoming - 120)
redis.call('PUBLISH', KEYS[3], ARGV[5])
redis.call('PUBLISH', KEYS[4], ARGV[5])
return 1
"""


async def publish_driver_location(
    redis_client,
    *,
    telegram_id: int,
    longitude: float,
    latitude: float,
    heartbeat_at: float,
    event: dict,
) -> bool:
    """Atomically update Redis presence and streams without undoing a DB write.

    PostgreSQL is the durable source of truth. Redis is a realtime projection;
    its outage must not turn a committed GPS sample into an apparent API failure.
    """
    try:
        encoded = json.dumps(event)
        await redis_client.eval(
            _PUBLISH_DRIVER_LOCATION,
            4,
            "driver:online",
            "driver:heartbeat",
            f"driver:location:{telegram_id}",
            "fleet:locations",
            longitude,
            latitude,
            str(telegram_id),
            heartbeat_at,
            encoded,
        )
    except Exception:
        logger.exception(
            "GPS sample committed but Redis realtime projection failed",
            extra={"telegram_id": telegram_id},
        )
        return False
    return True
