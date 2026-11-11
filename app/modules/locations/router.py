"""Authenticated driver GPS ingestion and low-latency location stream."""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ConfigDict

from app.core.security import verify_driver_token
from app.modules.auth.ws_tickets import consume_ws_ticket
from app.db.core import db
from app.modules.locations.gps import is_newer_sample, build_location_snapshot
from app.modules.locations.redis_projection import publish_driver_location
from app.modules.locations.fleet import fetch_fleet_snapshot

router = APIRouter(tags=["driver-location"])
logger = logging.getLogger(__name__)


class LocationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    accuracy_m: float | None = Field(default=None, ge=0, le=1000)
    heading_deg: float | None = Field(default=None, ge=0, lt=360)
    speed_mps: float | None = Field(default=None, ge=0, le=100)
    recorded_at: datetime


@router.post("/driver/location")
async def update_driver_location(
    payload: LocationUpdate,
    claims: dict = Depends(verify_driver_token),
):
    recorded_at = payload.recorded_at
    if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
        raise HTTPException(status_code=422, detail="recorded_at must include timezone")
    now = datetime.now(timezone.utc)
    if recorded_at > now + timedelta(seconds=30) or recorded_at < now - timedelta(minutes=5):
        raise HTTPException(status_code=422, detail="GPS sample is outside the accepted time window")

    telegram_id = int(claims["sub"])
    async with db.pool.acquire() as conn:
        driver = await conn.fetchrow(
            "SELECT id FROM users WHERE telegram_id=$1 AND driver_status='APPROVED'",
            telegram_id,
        )
        if not driver:
            raise HTTPException(status_code=403, detail="Approved driver account required")
        latest = await conn.fetchval("SELECT recorded_at FROM driver_locations WHERE driver_id=$1", driver["id"])
        if not is_newer_sample(recorded_at, latest):
            raise HTTPException(status_code=409, detail="GPS sample is not newer than the last accepted sample")
        point = f"SRID=4326;POINT({payload.longitude} {payload.latitude})"
        accepted_location = await conn.fetchrow(
            """
            INSERT INTO driver_locations
                (driver_id, telegram_id, position, accuracy_m, heading_deg, speed_mps, recorded_at, received_at)
            VALUES ($1, $2, ST_GeogFromText($3), $4, $5, $6, $7, now())
            ON CONFLICT (driver_id) DO UPDATE SET
                telegram_id=EXCLUDED.telegram_id, position=EXCLUDED.position,
                accuracy_m=EXCLUDED.accuracy_m, heading_deg=EXCLUDED.heading_deg,
                speed_mps=EXCLUDED.speed_mps, recorded_at=EXCLUDED.recorded_at,
                received_at=now()
            WHERE driver_locations.recorded_at < EXCLUDED.recorded_at
            RETURNING driver_id
            """,
            driver["id"], telegram_id, point, payload.accuracy_m,
            payload.heading_deg, payload.speed_mps, recorded_at,
        )
        if accepted_location is None:
            raise HTTPException(status_code=409, detail="GPS sample was superseded by a newer update")
        active_order = await conn.fetchval(
            """
            SELECT id FROM orders
            WHERE driver_id=$1 AND status IN ('ACCEPTED','ARRIVED','IN_PROGRESS')
            ORDER BY id DESC LIMIT 1
            """,
            driver["id"],
        )
        if active_order:
            await conn.execute(
                """
                INSERT INTO trip_tracks (order_id, driver_id, position, accuracy_m, recorded_at)
                VALUES ($1, $2, ST_GeogFromText($3), $4, $5)
                """,
                active_order, driver["id"], point, payload.accuracy_m, recorded_at,
            )

    event = {
        "telegram_id": telegram_id,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "accuracy_m": payload.accuracy_m,
        "heading_deg": payload.heading_deg,
        "speed_mps": payload.speed_mps,
        "recorded_at": recorded_at.isoformat(),
    }
    realtime_published = await publish_driver_location(
        db.redis,
        telegram_id=telegram_id,
        longitude=payload.longitude,
        latitude=payload.latitude,
        heartbeat_at=now.timestamp(),
        event=event,
    )
    return {
        "accepted": True,
        "recorded_at": recorded_at.isoformat(),
        "realtime_published": realtime_published,
    }


@router.websocket("/driver/location/stream")
async def driver_location_stream(websocket: WebSocket, ticket: str):
    claims = await consume_ws_ticket(ticket, "driver-location")
    if not claims or claims.get("role") != "driver":
        await websocket.close(code=4401)
        return
    telegram_id = int(claims["sub"])
    await websocket.accept()
    channel = f"driver:location:{telegram_id}"
    pubsub = None

    async def load_snapshot():
        async with db.pool.acquire() as conn:
            latest = await conn.fetchrow(
                """
                SELECT ST_Y(position::geometry) AS latitude,
                       ST_X(position::geometry) AS longitude,
                       accuracy_m, heading_deg, speed_mps, recorded_at
                FROM driver_locations
                WHERE telegram_id=$1
                """,
                telegram_id,
            )
        snapshot = build_location_snapshot(latest, telegram_id)
        if snapshot is not None:
            await websocket.send_json(snapshot)

    async def subscribe():
        subscriber = db.redis.pubsub()
        await subscriber.subscribe(channel)
        return subscriber

    try:
        # Subscribe first, then read PostgreSQL: updates committed during the
        # snapshot query are queued by Redis and delivered after this snapshot.
        try:
            pubsub = await subscribe()
        except Exception:
            logger.warning(
                "Driver location Pub/Sub unavailable at connect; using durable snapshot",
                extra={"telegram_id": telegram_id},
                exc_info=True,
            )
        await load_snapshot()
        last_reconciliation = asyncio.get_running_loop().time()
        while True:
            if pubsub is None:
                await asyncio.sleep(1.0)
                try:
                    pubsub = await subscribe()
                except Exception:
                    logger.warning(
                        "Driver location Pub/Sub resubscribe failed",
                        extra={"telegram_id": telegram_id},
                        exc_info=True,
                    )
                    await load_snapshot()
                    last_reconciliation = asyncio.get_running_loop().time()
                    continue
                await load_snapshot()
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
                    "Driver location Pub/Sub unavailable; rebuilding subscription",
                    extra={"telegram_id": telegram_id},
                    exc_info=True,
                )
                try:
                    await pubsub.aclose()
                except Exception:
                    logger.debug("Failed to close stale driver Pub/Sub", exc_info=True)
                pubsub = None
                await load_snapshot()
                last_reconciliation = asyncio.get_running_loop().time()
                continue

            if message and message.get("type") == "message":
                await websocket.send_text(message["data"])
            else:
                now = asyncio.get_running_loop().time()
                if now - last_reconciliation >= 30.0:
                    # Repair events lost during a Redis restart or transient
                    # publish failure from the durable PostgreSQL row.
                    await load_snapshot()
                    last_reconciliation = now
    except WebSocketDisconnect:
        pass
    finally:
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(channel)
            finally:
                await pubsub.aclose()



class NavigationRequest(BaseModel):
    coordinates: list[tuple[float, float]] = Field(min_length=2, max_length=25)


@router.post("/driver/navigation/route")
async def driver_navigation_route(
    request: NavigationRequest,
    claims: dict = Depends(verify_driver_token),
):
    try:
        from app.infrastructure.osrm.client import OSRMClient, RoutingError
        result = await OSRMClient().route(
            request.coordinates, alternatives=False, steps=True
        )
        if not result.get("routes"):
            raise HTTPException(status_code=404, detail="No drivable route found")
        return result
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RoutingError as exc:
        raise HTTPException(status_code=503, detail="Navigation service unavailable") from exc


@router.post("/driver/navigation/match")
async def driver_navigation_match(
    request: NavigationRequest,
    claims: dict = Depends(verify_driver_token),
):
    try:
        from app.infrastructure.osrm.client import OSRMClient, RoutingError
        return await OSRMClient().match(request.coordinates)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RoutingError as exc:
        raise HTTPException(status_code=503, detail="Navigation matching unavailable") from exc



@router.websocket("/admin/fleet/stream")
async def admin_fleet_stream(websocket: WebSocket, ticket: str):
    """Authenticated admin-only fleet stream using a single-use WebSocket ticket."""
    claims = await consume_ws_ticket(ticket, "admin-fleet")
    if not claims or claims.get("role") != "admin":
        await websocket.close(code=4401)
        return

    await websocket.accept()
    channel = "fleet:locations"
    pubsub = None

    async def subscribe():
        subscriber = db.redis.pubsub()
        await subscriber.subscribe(channel)
        return subscriber

    async def send_snapshot(reason=None):
        async with db.pool.acquire() as conn:
            snapshot = await fetch_fleet_snapshot(conn)
        frame = {"type": "snapshot", "drivers": snapshot}
        if reason:
            frame["reason"] = reason
        await websocket.send_json(frame)

    try:
        # Subscribe before the durable snapshot so GPS updates committed while
        # PostgreSQL is queried remain queued in this Pub/Sub subscription.
        try:
            pubsub = await subscribe()
        except Exception:
            logger.warning(
                "Fleet Pub/Sub unavailable at connect; using durable snapshot",
                exc_info=True,
            )
        await send_snapshot()
        last_reconciliation = asyncio.get_running_loop().time()
        while True:
            if pubsub is None:
                await asyncio.sleep(5.0)
                try:
                    pubsub = await subscribe()
                except Exception:
                    logger.warning("Fleet Pub/Sub resubscribe failed", exc_info=True)
                await send_snapshot("redis_recovery")
                last_reconciliation = asyncio.get_running_loop().time()
                continue
            try:
                # Periodic durable reconciliation repairs missed Pub/Sub frames
                # after Redis restarts without requiring an admin page reload.
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
            except WebSocketDisconnect:
                raise
            except Exception:
                logger.warning(
                    "Fleet Pub/Sub unavailable; rebuilding subscription",
                    exc_info=True,
                )
                try:
                    await pubsub.aclose()
                except Exception:
                    logger.debug("Failed to close stale fleet Pub/Sub", exc_info=True)
                pubsub = None
                await send_snapshot("redis_recovery")
                continue

            if message and message.get("type") == "message":
                await websocket.send_text(message["data"])
            else:
                now = asyncio.get_running_loop().time()
                if now - last_reconciliation >= 15.0:
                    await send_snapshot("periodic_reconciliation")
                    last_reconciliation = now
    except WebSocketDisconnect:
        pass
    finally:
        if pubsub is not None:
            try:
                await pubsub.unsubscribe(channel)
            finally:
                await pubsub.aclose()
