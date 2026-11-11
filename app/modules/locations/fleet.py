"""Durable administrator fleet snapshot shared by HTTP and WebSocket paths."""
from __future__ import annotations

from datetime import datetime, timezone


FLEET_SNAPSHOT_QUERY = """
    SELECT u.id, u.telegram_id, u.first_name, u.phone, u.plate_number,
           u.rating, ST_Y(l.position::geometry) AS latitude,
           ST_X(l.position::geometry) AS longitude,
           l.accuracy_m, l.heading_deg, l.speed_mps,
           l.recorded_at, l.received_at
    FROM users u
    LEFT JOIN driver_locations l ON l.driver_id=u.id
    WHERE u.driver_status='APPROVED'
    ORDER BY l.received_at DESC NULLS LAST
"""


def build_fleet_snapshot(rows, *, now: datetime | None = None) -> list[dict]:
    """Serialize approved drivers and derive online state from durable receipt time.

    Naive/future timestamps fail closed to offline. PostgreSQL remains the
    source of truth; Redis Pub/Sub only carries changes after this snapshot.
    """
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None or current.utcoffset() is None:
        raise ValueError("now must be timezone-aware")

    result = []
    for row in rows:
        driver = dict(row)
        received_at = driver.get("received_at")
        online = False
        if (
            isinstance(received_at, datetime)
            and received_at.tzinfo is not None
            and received_at.utcoffset() is not None
        ):
            age_seconds = (current - received_at).total_seconds()
            online = 0 <= age_seconds <= 120
        driver["online"] = online
        result.append(driver)
    return result


async def fetch_fleet_snapshot(connection, *, now: datetime | None = None) -> list[dict]:
    rows = await connection.fetch(FLEET_SNAPSHOT_QUERY)
    return build_fleet_snapshot(rows, now=now)
