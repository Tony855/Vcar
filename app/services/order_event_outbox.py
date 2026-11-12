import asyncio
import json
import logging

from app.db.core import db
from app.db.repositories.order_repo import OrderRepository

logger = logging.getLogger(__name__)


async def dispatch_order_event_batch(redis_client, repository=OrderRepository, limit=100):
    """Publish a claimed batch to Redis; delivery is at-least-once.

    A process crash after Redis publish but before the database acknowledgement can
    cause the same event to be published again. Consumers should deduplicate by
    the stable event_id included in every payload.
    """
    events = await repository.claim_status_events(limit=limit)
    delivered = 0
    for event in events:
        outbox_id = event["id"]
        try:
            payload = event["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            payload = dict(payload)
            # Durable outbox columns are authoritative for deduplication and routing.
            payload["event_id"] = event["event_id"]
            payload["order_id"] = event["order_id"]
            await redis_client.publish(
                f"order:status:{event['order_id']}",
                json.dumps(payload, separators=(",", ":")),
            )
            acknowledged = await repository.mark_status_event_delivered(outbox_id, event["lock_token"])
            if acknowledged:
                delivered += 1
            else:
                # A concurrent worker or recovery path may have changed the claim.
                # The publish happened, but this worker did not confirm durable ack.
                logger.warning(
                    "Order status event was published but not acknowledged by this worker",
                    extra={"outbox_id": outbox_id, "event_id": event.get("event_id")},
                )
        except Exception as exc:
            logger.exception(
                "Order status outbox delivery failed",
                extra={"outbox_id": outbox_id, "event_id": event.get("event_id")},
            )
            try:
                await repository.retry_status_event(outbox_id, event["lock_token"], exc)
            except Exception:
                logger.exception(
                    "Unable to schedule order status outbox retry",
                    extra={"outbox_id": outbox_id},
                )
    return delivered


async def run_order_event_outbox_worker(
    stop_event, poll_interval=1.0, batch_size=100
):
    """Continuously dispatch durable order status events until shutdown."""
    while not stop_event.is_set():
        try:
            redis_client = db.redis
            if redis_client is not None:
                await dispatch_order_event_batch(
                    redis_client, limit=batch_size
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Order status outbox worker iteration failed")

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval)
        except asyncio.TimeoutError:
            pass
