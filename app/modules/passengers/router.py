"""Passenger Telegram WebApp session and booking API."""
from __future__ import annotations

import asyncio
import json
import logging
import jwt
from fastapi import APIRouter, Depends, Header, HTTPException, Path, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core.config import settings
from app.core.security import (
    create_access_token, verify_telegram_webapp_init_data,
    is_valid_telegram_subject, require_strong_secret_key,
)
from app.db.core import db
from app.modules.auth.ws_tickets import consume_ws_ticket
from app.db.repositories.order_repo import OrderRepository
from app.services.pricing import PricingService
from app.infrastructure.osrm.client import RoutingError
from app.modules.orders.stream import load_order_stream_bootstrap

router = APIRouter(prefix="/passenger", tags=["passenger"])
logger = logging.getLogger(__name__)


class TelegramSessionRequest(BaseModel):
    init_data: str = Field(min_length=1, max_length=8192)


class BookingRequest(BaseModel):
    pickup_lat: float = Field(ge=-90, le=90)
    pickup_lng: float = Field(ge=-180, le=180)
    dropoff_lat: float = Field(ge=-90, le=90)
    dropoff_lng: float = Field(ge=-180, le=180)


@router.post("/session")
async def passenger_session(request: TelegramSessionRequest):
    if not settings.passenger_bot_token:
        raise HTTPException(status_code=503, detail="Passenger Telegram bot is not configured")
    try:
        verified = verify_telegram_webapp_init_data(request.init_data, settings.passenger_bot_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Telegram WebApp authentication") from exc
    user = verified["user"]
    telegram_id = int(user["id"])
    async with db.pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, first_name, username, locale, driver_status)
            VALUES ($1, $2, $3, $4, 'NONE')
            ON CONFLICT (telegram_id) DO UPDATE SET
                first_name=COALESCE(EXCLUDED.first_name, users.first_name),
                username=COALESCE(EXCLUDED.username, users.username),
                locale=COALESCE(EXCLUDED.locale, users.locale),
                updated_at=now()
            """,
            telegram_id, user.get("first_name"), user.get("username"), user.get("language_code", "en"),
        )
    token = create_access_token(subject=str(telegram_id), role="passenger", expires_minutes=30)
    return {"token": token, "tg_id": telegram_id, "expires_in": 1800}


def passenger_claims(authorization: str | None) -> dict:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Bearer token required")
    secret_key = require_strong_secret_key(settings.secret_key)
    try:
        claims = jwt.decode(
            authorization[7:], secret_key, algorithms=["HS256"],
            options={"require": ["exp"]},
        )
    except jwt.PyJWTError as exc:
        raise HTTPException(status_code=401, detail="Invalid or expired passenger token") from exc
    if claims.get("role") != "passenger" or not is_valid_telegram_subject(claims.get("sub")):
        raise HTTPException(status_code=403, detail="Passenger identity or permissions are invalid")
    return claims


@router.post("/orders", status_code=201)
async def create_booking(
    request: BookingRequest,
    authorization: str | None = Header(default=None),
):
    claims = passenger_claims(authorization)
    async with db.pool.acquire() as conn:
        customer_id = await conn.fetchval(
            "SELECT id FROM users WHERE telegram_id=$1", int(claims["sub"])
        )
    if customer_id is None:
        raise HTTPException(status_code=403, detail="Passenger profile not found")
    try:
        distance_km, price = await PricingService.estimate(
            request.pickup_lat, request.pickup_lng,
            request.dropoff_lat, request.dropoff_lng,
        )
    except (RoutingError, ValueError) as exc:
        raise HTTPException(status_code=503, detail="Unable to calculate a safe route quote") from exc
    order = await OrderRepository.create(
        customer_id, request.pickup_lng, request.pickup_lat,
        request.dropoff_lng, request.dropoff_lat, distance_km, price,
    )
    return {"order_id": order["id"], "status": "DISPATCHING", "distance_km": distance_km, "price_estimate": price}



@router.get("/orders/{order_id}")
async def passenger_order_status(
    order_id: int = Path(gt=0, le=2**63 - 1),
    authorization: str | None = Header(default=None),
):
    claims = passenger_claims(authorization)
    async with db.pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, status, price_estimate, distance_km
            FROM orders
            WHERE id=$1 AND customer_id=(SELECT id FROM users WHERE telegram_id=$2)
            """,
            order_id, int(claims["sub"]),
        )
    if row is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return dict(row)



@router.websocket("/orders/{order_id}/stream")
async def passenger_order_stream(
    websocket: WebSocket,
    order_id: int,
    ticket: str,
    last_event_id: int = 0,
):
    if (
        not isinstance(order_id, int) or isinstance(order_id, bool)
        or not 0 < order_id <= 2**63 - 1
        or not isinstance(last_event_id, int) or isinstance(last_event_id, bool)
        or not 0 <= last_event_id <= 2**63 - 1
    ):
        await websocket.close(code=4400)
        return
    claims = await consume_ws_ticket(ticket, f"passenger-order:{order_id}")
    if (
        not claims
        or claims.get("role") != "passenger"
        or not is_valid_telegram_subject(claims.get("sub"))
    ):
        await websocket.close(code=4401)
        return

    async with db.pool.acquire() as conn:
        owned = await conn.fetchval(
            """
            SELECT id FROM orders
            WHERE id=$1 AND customer_id=(SELECT id FROM users WHERE telegram_id=$2)
            """,
            order_id, int(claims["sub"]),
        )
    if owned is None:
        await websocket.close(code=4403)
        return

    await websocket.accept()
    channel = f"order:status:{order_id}"
    pubsub = None
    last_sent_event_id = last_event_id
    reconnect_delay = 1.0
    last_reconciliation = 0.0

    async def subscribe():
        subscriber = db.redis.pubsub()
        try:
            await subscriber.subscribe(channel)
        except Exception:
            try:
                await subscriber.aclose()
            except Exception:
                logger.debug(
                    "Failed to close unsuccessful passenger order Pub/Sub",
                    extra={"order_id": order_id},
                    exc_info=True,
                )
            raise
        return subscriber

    async def send_durable_updates():
        """Replay committed outbox events to repair Pub/Sub gaps and restarts."""
        nonlocal last_sent_event_id
        async with db.pool.acquire() as conn:
            bootstrap = await load_order_stream_bootstrap(
                conn, order_id=order_id, last_event_id=last_sent_event_id
            )
        if bootstrap["type"] == "replay":
            for event in bootstrap["events"]:
                if not isinstance(event, dict):
                    continue
                event_id = event.get("event_id")
                if (
                    not isinstance(event_id, int)
                    or isinstance(event_id, bool)
                    or not 0 < event_id <= 2**63 - 1
                    or event_id <= last_sent_event_id
                    or event.get("order_id") != order_id
                ):
                    continue
                await websocket.send_json(event)
                last_sent_event_id = event_id
                if event.get("status") in ("COMPLETED", "CANCELLED"):
                    return True
        else:
            await websocket.send_json(bootstrap)
            last_sent_event_id = int(bootstrap["last_event_id"])
        return bootstrap.get("status") in ("COMPLETED", "CANCELLED")

    try:
        # Subscribe before the durable bootstrap so events committed during the
        # database read are queued and later deduplicated by event_id.
        try:
            pubsub = await subscribe()
        except Exception:
            logger.warning(
                "Passenger order Pub/Sub unavailable at connect; using durable replay",
                extra={"order_id": order_id},
                exc_info=True,
            )
            pubsub = None
        if await send_durable_updates():
            return
        last_reconciliation = asyncio.get_running_loop().time()

        while True:
            if pubsub is None:
                await asyncio.sleep(reconnect_delay)
                try:
                    pubsub = await subscribe()
                    reconnect_delay = 1.0
                except Exception:
                    logger.warning(
                        "Passenger order Pub/Sub resubscribe failed",
                        extra={"order_id": order_id},
                        exc_info=True,
                    )
                    pubsub = None
                    reconnect_delay = min(reconnect_delay * 2, 15.0)
                # Poll the durable outbox even while Redis is unavailable.
                if await send_durable_updates():
                    break
                last_reconciliation = asyncio.get_running_loop().time()
                continue

            try:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
            except WebSocketDisconnect:
                raise
            except Exception:
                logger.warning(
                    "Passenger order Pub/Sub unavailable; rebuilding subscription",
                    extra={"order_id": order_id},
                    exc_info=True,
                )
                try:
                    await pubsub.aclose()
                except Exception:
                    logger.debug(
                        "Failed to close stale passenger order Pub/Sub",
                        extra={"order_id": order_id},
                        exc_info=True,
                    )
                pubsub = None
                reconnect_delay = 1.0
                if await send_durable_updates():
                    break
                last_reconciliation = asyncio.get_running_loop().time()
                continue

            if message and message.get("type") == "message":
                try:
                    payload = json.loads(message["data"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                event_id = payload.get("event_id")
                if (
                    not isinstance(event_id, int)
                    or isinstance(event_id, bool)
                    or not 0 < event_id <= 2**63 - 1
                    or payload.get("order_id") != order_id
                    or event_id <= last_sent_event_id
                ):
                    continue
                await websocket.send_json(payload)
                last_sent_event_id = event_id
                if payload.get("status") in ("COMPLETED", "CANCELLED"):
                    break
            else:
                now = asyncio.get_running_loop().time()
                if now - last_reconciliation >= 15.0:
                    # Pub/Sub is at-most-once. Periodic replay repairs a lost
                    # publication even if the Redis connection stayed healthy.
                    if await send_durable_updates():
                        break
                    last_reconciliation = now
    except WebSocketDisconnect:
        pass
    finally:
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(channel)
            except Exception:
                logger.debug(
                    "Failed to unsubscribe passenger order Pub/Sub",
                    extra={"order_id": order_id},
                    exc_info=True,
                )
            try:
                await pubsub.aclose()
            except Exception:
                logger.debug(
                    "Failed to close passenger order Pub/Sub",
                    extra={"order_id": order_id},
                    exc_info=True,
                )
