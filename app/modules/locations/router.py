"""Authenticated driver GPS ingestion and low-latency location stream."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ConfigDict

from app.core.config import settings
from app.core.security import verify_driver_token
from app.db.core import db

router = APIRouter(tags=["driver-location"])


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
    if recorded_at.tzinfo is None:
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
        if latest is not None and recorded_at <= latest:
            raise HTTPException(status_code=409, detail="GPS sample is older than the last accepted sample")
        point = f"SRID=4326;POINT({payload.longitude} {payload.latitude})"
        await conn.execute(
            """
            INSERT INTO driver_locations
                (driver_id, telegram_id, position, accuracy_m, heading_deg, speed_mps, recorded_at, received_at)
            VALUES ($1, $2, ST_GeogFromText($3), $4, $5, $6, $7, now())
            ON CONFLICT (driver_id) DO UPDATE SET
                telegram_id=EXCLUDED.telegram_id, position=EXCLUDED.position,
                accuracy_m=EXCLUDED.accuracy_m, heading_deg=EXCLUDED.heading_deg,
                speed_mps=EXCLUDED.speed_mps, recorded_at=EXCLUDED.recorded_at,
                received_at=now()
            WHERE driver_locations.recorded_at <= EXCLUDED.recorded_at
            """,
            driver["id"], telegram_id, point, payload.accuracy_m,
            payload.heading_deg, payload.speed_mps, recorded_at,
        )
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

    await db.redis.geoadd("driver:online", (payload.longitude, payload.latitude, str(telegram_id)))
    await db.redis.zadd("driver:heartbeat", {str(telegram_id): now.timestamp()})
    await db.redis.zremrangebyscore("driver:heartbeat", "-inf", now.timestamp() - 120)
    event = {
        "telegram_id": telegram_id,
        "latitude": payload.latitude,
        "longitude": payload.longitude,
        "accuracy_m": payload.accuracy_m,
        "heading_deg": payload.heading_deg,
        "speed_mps": payload.speed_mps,
        "recorded_at": recorded_at.isoformat(),
    }
    await db.redis.publish(f"driver:location:{telegram_id}", json.dumps(event))
    await db.redis.publish("fleet:locations", json.dumps(event))
    return {"accepted": True, "recorded_at": recorded_at.isoformat()}


@router.websocket("/driver/location/stream")
async def driver_location_stream(websocket: WebSocket, token: str):
    # Browser WebSocket APIs cannot set Authorization headers. Require a short-lived
    # driver JWT and keep reverse-proxy query-string logging disabled for this route.
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        if claims.get("role") != "driver" or not claims.get("sub"):
            raise ValueError("invalid role")
    except (jwt.PyJWTError, ValueError):
        await websocket.close(code=4401)
        return
    telegram_id = int(claims["sub"])
    await websocket.accept()
    pubsub = db.redis.pubsub()
    try:
        await pubsub.subscribe(f"driver:location:{telegram_id}")
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                await websocket.send_text(message["data"])
            else:
                await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(f"driver:location:{telegram_id}")
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
async def admin_fleet_stream(websocket: WebSocket, token: str):
    """Authenticated admin-only fleet stream; use a short-lived token."""
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
        if claims.get("role") != "admin" or not claims.get("sub"):
            raise ValueError("invalid role")
    except (jwt.PyJWTError, ValueError):
        await websocket.close(code=4401)
        return

    await websocket.accept()
    pubsub = db.redis.pubsub()
    try:
        await pubsub.subscribe("fleet:locations")
        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                await websocket.send_text(message["data"])
            else:
                await asyncio.sleep(0.1)
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe("fleet:locations")
        await pubsub.aclose()
