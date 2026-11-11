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
    status = await connection.fetchval(
        "SELECT status FROM orders WHERE id=$1", order_id
    )
    latest_event_id = await connection.fetchval(
        "SELECT COALESCE(MAX(event_id), 0) FROM order_event_outbox WHERE order_id=$1",
        order_id,
    )
    latest_event_id = int(latest_event_id or 0)
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
        SELECT payload FROM order_event_outbox
        WHERE order_id=$1 AND event_id>$2
        ORDER BY event_id
        """,
        order_id,
        cursor,
    )
    events = []
    for row in rows:
        payload = row["payload"]
        if isinstance(payload, str):
            payload = json.loads(payload)
        payload = dict(payload)
        payload.setdefault("order_id", order_id)
        events.append(payload)
    return {
        "type": "replay",
        "order_id": order_id,
        "status": status,
        "last_event_id": latest_event_id,
        "events": events,
    }
