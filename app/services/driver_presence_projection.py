"""Periodic PostgreSQL-to-Redis repair for dispatch presence after Redis loss."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

# Process-local worker telemetry. Prometheus exposes these gauges per app
# process; they remain readable while Redis is unavailable.
PRESENCE_RECONCILIATION_STATE = {
    "last_success_timestamp": 0.0,
    "last_failure_timestamp": 0.0,
    "consecutive_failures": 0,
    "last_duration_seconds": 0.0,
    "database_fresh": 0,
    "repaired": 0,
    "stale_removed": 0,
}

# Repair a driver's projection only when PostgreSQL's GPS sample is at least as
# new as the currently projected sample. No Pub/Sub is emitted: WebSocket
# consumers already reconcile from PostgreSQL snapshots.
_REPAIR_DRIVER = """
-- The caller holds the PostgreSQL user row lock and has revalidated that
-- ARGV[6] is the current durable generation. PostgreSQL is authoritative:
-- repair must also replace a stale Redis generation left by DB restore.
local incoming_generation = tonumber(ARGV[6])
local previous = redis.call('ZSCORE', KEYS[3], ARGV[3])
local incoming_sample = tonumber(ARGV[4])
if previous and tonumber(previous) > incoming_sample then
    return 0
end
redis.call('SET', KEYS[4], ARGV[6], 'EX', 2592000)
redis.call('GEOADD', KEYS[1], ARGV[1], ARGV[2], ARGV[3])
-- Reconciliation may repair missing heartbeat state, but must never move a
-- newer online transition backwards to the older durable GPS receipt time.
local previous_heartbeat = redis.call('ZSCORE', KEYS[2], ARGV[3])
local incoming_heartbeat = tonumber(ARGV[5])
if not previous_heartbeat or tonumber(previous_heartbeat) < incoming_heartbeat then
    redis.call('ZADD', KEYS[2], incoming_heartbeat, ARGV[3])
end
redis.call('ZADD', KEYS[3], incoming_sample, ARGV[3])
return 1
"""

# A driver may have sent a new GPS update after the database snapshot began.
# Only remove stale Redis entries whose heartbeat has not advanced past the
# reconciliation cutoff.
_REMOVE_STALE_DRIVER = """
local heartbeat = redis.call('ZSCORE', KEYS[1], ARGV[1])
if not heartbeat or tonumber(heartbeat) <= tonumber(ARGV[2]) then
    redis.call('ZREM', KEYS[1], ARGV[1])
    redis.call('ZREM', KEYS[2], ARGV[1])
    redis.call('ZREM', KEYS[3], ARGV[1])
    return 1
end
return 0
"""

FRESH_DRIVERS_QUERY = """
    SELECT u.telegram_id,
           u.driver_presence_generation,
           ST_X(l.position::geometry) AS longitude,
           ST_Y(l.position::geometry) AS latitude,
           l.recorded_at,
           l.received_at
    FROM users u
    JOIN driver_locations l ON l.driver_id=u.id
    WHERE u.driver_status='APPROVED'
      AND u.driver_is_online IS TRUE
      AND l.received_at >= $1
      AND l.received_at <= $2
      AND l.recorded_at <= $2 + interval '30 seconds'
"""


async def reconcile_driver_presence(redis_client, connection, *, now=None) -> dict:
    """Repair fresh driver GEO/heartbeat/version keys from durable GPS rows."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    cutoff = current - timedelta(seconds=120)
    scan_epoch = current.timestamp()

    rows = await connection.fetch(FRESH_DRIVERS_QUERY, cutoff, current)
    fresh_ids: set[str] = set()
    repaired = 0
    for row in rows:
        telegram_id = row["telegram_id"]
        if not isinstance(telegram_id, int) or isinstance(telegram_id, bool) or telegram_id <= 0:
            continue
        received_at = row["received_at"]
        recorded_at = row["recorded_at"]
        if (
            not isinstance(received_at, datetime) or received_at.tzinfo is None
            or received_at.utcoffset() is None or not cutoff <= received_at <= current
            or not isinstance(recorded_at, datetime) or recorded_at.tzinfo is None
            or recorded_at.utcoffset() is None
        ):
            continue
        longitude, latitude = float(row["longitude"]), float(row["latitude"])
        if not (-180 <= longitude <= 180 and -90 <= latitude <= 90):
            continue
        member = str(telegram_id)
        generation = row["driver_presence_generation"]
        if (
            not isinstance(generation, int)
            or isinstance(generation, bool)
            or generation < 0
        ):
            continue
        # Recheck and lock the durable online state while repairing Redis.
        # An offline transition must wait for this repair, then invalidate it;
        # a stale query snapshot must never resurrect a driver after that transition.
        async with connection.transaction():
            state = await connection.fetchrow(
                """
                SELECT driver_is_online, driver_presence_generation
                FROM users
                WHERE telegram_id=$1 AND driver_status='APPROVED'
                FOR UPDATE
                """,
                telegram_id,
            )
            if (
                not state
                or not state["driver_is_online"]
                or state["driver_presence_generation"] != generation
            ):
                continue
            applied = await redis_client.eval(
                _REPAIR_DRIVER, 4,
                "driver:online", "driver:heartbeat", "driver:location:version",
                "driver:presence:generation:" + member,
                longitude, latitude, member, recorded_at.timestamp(),
                received_at.timestamp(), generation,
            )
        fresh_ids.add(member)
        repaired += int(applied == 1)

    # Iterate both indexes: a partial Redis write or old deployment may leave
    # GEO members without heartbeat scores, or heartbeat members without GEO.
    indexed = set(await redis_client.zrange("driver:heartbeat", 0, -1))
    indexed.update(await redis_client.zrange("driver:online", 0, -1))
    stale_removed = 0
    for member in indexed - fresh_ids:
        stale_removed += int(await redis_client.eval(
            _REMOVE_STALE_DRIVER, 3,
            "driver:heartbeat", "driver:online", "driver:location:version",
            member, scan_epoch,
        ) == 1)

    return {
        "database_fresh": len(fresh_ids),
        "repaired": repaired,
        "stale_removed": stale_removed,
    }


async def run_driver_presence_reconciliation_worker(
    stop_event: asyncio.Event, *, interval: float = 30.0
) -> None:
    """Keep Redis dispatch presence converged to PostgreSQL, tolerating outages."""
    if interval < 1:
        raise ValueError("interval must be at least one second")
    while not stop_event.is_set():
        started = time.monotonic()
        try:
            from app.db.core import db

            if db.pool is not None and db.redis is not None:
                async with db.pool.acquire() as connection:
                    result = await reconcile_driver_presence(db.redis, connection)
                PRESENCE_RECONCILIATION_STATE.update({
                    "last_success_timestamp": time.time(),
                    "consecutive_failures": 0,
                    "last_duration_seconds": time.monotonic() - started,
                    "database_fresh": int(result["database_fresh"]),
                    "repaired": int(result["repaired"]),
                    "stale_removed": int(result["stale_removed"]),
                })
                logger.info("Driver presence Redis projection reconciled", extra=result)
        except asyncio.CancelledError:
            raise
        except Exception:
            PRESENCE_RECONCILIATION_STATE.update({
                "last_failure_timestamp": time.time(),
                "consecutive_failures": PRESENCE_RECONCILIATION_STATE["consecutive_failures"] + 1,
                "last_duration_seconds": time.monotonic() - started,
            })
            logger.warning(
                "Driver presence reconciliation failed; PostgreSQL remains authoritative",
                exc_info=True,
            )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass
