"""Durable order-event replay for passenger WebSocket reconnects."""
from __future__ import annotations

import json


MAX_ORDER_EVENT_REPLAY = 500


async def load_order_stream_bootstrap(
    connection,
    *,
    order_id: int,
    last_event_id: int,
    max_replay: int = MAX_ORDER_EVENT_REPLAY,
) -> dict:
    """Return ordered outbox replay or a current-state snapshot.

    The caller subscribes to Redis before invoking this function. Pub/Sub frames
    queued during the database read are deduplicated by event_id in the stream.
    """
    # Read status and event cursor in one SQL statement so both values share
    # one PostgreSQL statement snapshot. Separate reads could pair an old status
    # with a new cursor and make a just-committed transition impossible to replay.
    current = await connection.fetchrow(
        """
        SELECT o.status,
               COALESCE((
                   SELECT MAX(outbox.event_id)
                   FROM order_event_outbox AS outbox
                   WHERE outbox.order_id=o.id
               ), 0) AS latest_event_id
        FROM orders AS o
        WHERE o.id=$1
        """,
        order_id,
    )
    if current is None:
        return {
            "type": "snapshot",
            "order_id": order_id,
            "status": None,
            "last_event_id": 0,
            "resync_required": True,
        }
    status = current["status"]
    latest_event_id = int(current["latest_event_id"] or 0)
    cursor = max(0, int(last_event_id or 0))

    if cursor == 0 or cursor > latest_event_id:
        return {
            "type": "snapshot",
            "order_id": order_id,
            "status": status,
            "last_event_id": latest_event_id,
            "resync_required": cursor > latest_event_id,
        }

    pending_count = await connection.fetchval(
        """
        SELECT COUNT(*) FROM order_event_outbox
        WHERE order_id=$1 AND event_id>$2
        """,
        order_id,
        cursor,
    )
    if int(pending_count or 0) > max_replay:
        return {
            "type": "snapshot",
            "order_id": order_id,
            "status": status,
            "last_event_id": latest_event_id,
            "resync_required": True,
        }

    rows = await connection.fetch(
        """
        SELECT event_id, payload FROM order_event_outbox
        WHERE order_id=$1 AND event_id>$2
        ORDER BY event_id
        LIMIT $3
        """,
        order_id,
        cursor,
        max_replay + 1,
    )
    # The count and row fetch use separate READ COMMITTED snapshots. A burst of
    # committed events between them can exceed the earlier count, so enforce the
    # replay ceiling in the query itself and fall back if the sentinel row exists.
    if len(rows) > max_replay:
        return {
            "type": "snapshot",
            "order_id": order_id,
            "status": status,
            "last_event_id": latest_event_id,
            "resync_required": True,
        }
    events = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = dict(payload)
        # The durable outbox columns are authoritative; never trust IDs copied
        # into a historical JSON payload when advancing the client event cursor.
        payload["event_id"] = int(row["event_id"])
        payload["order_id"] = order_id
        events.append(payload)
    return {
        "type": "replay",
        "order_id": order_id,
        "status": status,
        "last_event_id": latest_event_id,
        "events": events,
    }
