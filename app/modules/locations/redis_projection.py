"""Best-effort, ordered Redis projection for GPS data already committed to PostgreSQL."""
from __future__ import annotations

from datetime import datetime
import json
import logging

logger = logging.getLogger(__name__)

# Compare the driver's sample timestamp, not request completion time. Requests
# can be scheduled out of order after their PostgreSQL transactions commit.
# All projection writes and both Pub/Sub frames are one Redis atomic operation.
_PUBLISH_DRIVER_LOCATION = """
local previous = redis.call('ZSCORE', KEYS[3], ARGV[3])
local incoming_sample = tonumber(ARGV[4])
if previous and tonumber(previous) >= incoming_sample then
    return 0
end

redis.call('GEOADD', KEYS[1], ARGV[1], ARGV[2], ARGV[3])
redis.call('ZADD', KEYS[2], tonumber(ARGV[5]), ARGV[3])
redis.call('ZADD', KEYS[3], incoming_sample, ARGV[3])
redis.call('ZREMRANGEBYSCORE', KEYS[2], '-inf', tonumber(ARGV[5]) - 120)
redis.call('PUBLISH', KEYS[4], ARGV[6])
redis.call('PUBLISH', KEYS[5], ARGV[6])
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
    Returns True only when this sample was actually applied and published.
    """
    try:
        sample_at = datetime.fromisoformat(event["recorded_at"]).timestamp()
        encoded = json.dumps(event)
        applied = await redis_client.eval(
            _PUBLISH_DRIVER_LOCATION,
            5,
            "driver:online",
            "driver:heartbeat",
            "driver:location:version",
            f"driver:location:{telegram_id}",
            "fleet:locations",
            longitude,
            latitude,
            str(telegram_id),
            sample_at,
            heartbeat_at,
            encoded,
        )
    except Exception:
        logger.exception(
            "GPS sample committed but Redis realtime projection failed",
            extra={"telegram_id": telegram_id},
        )
        return False
    # The Lua script returns 0 when a newer/equal sample is already projected.
    # Do not report that stale sample as realtime-published to the API caller.
    return applied == 1
