"""Best-effort Redis projection for GPS data already committed to PostgreSQL."""
from __future__ import annotations

import json
import logging

logger = logging.getLogger(__name__)


async def publish_driver_location(
    redis_client,
    *,
    telegram_id: int,
    longitude: float,
    latitude: float,
    heartbeat_at: float,
    event: dict,
) -> bool:
    """Update Redis presence and streams without undoing an accepted DB write.

    PostgreSQL is the durable source of truth. Redis is a realtime projection;
    its outage must not turn a committed GPS sample into an apparent API failure.
    """
    try:
        await redis_client.geoadd(
            "driver:online", (longitude, latitude, str(telegram_id))
        )
        await redis_client.zadd(
            "driver:heartbeat", {str(telegram_id): heartbeat_at}
        )
        await redis_client.zremrangebyscore(
            "driver:heartbeat", "-inf", heartbeat_at - 120
        )
        encoded = json.dumps(event)
        await redis_client.publish(f"driver:location:{telegram_id}", encoded)
        await redis_client.publish("fleet:locations", encoded)
    except Exception:
        logger.exception(
            "GPS sample committed but Redis realtime projection failed",
            extra={"telegram_id": telegram_id},
        )
        return False
    return True
