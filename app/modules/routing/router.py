from __future__ import annotations

from typing import Sequence

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.core.security import verify_token
from app.infrastructure.osrm.client import OSRMClient, RoutingError

router = APIRouter(
    prefix="/routing",
    tags=["routing"],
    dependencies=[Depends(verify_token)],
)
_client = OSRMClient()


class RouteRequest(BaseModel):
    coordinates: list[tuple[float, float]] = Field(min_length=2, max_length=25)
    alternatives: bool = True
    steps: bool = True


class TableRequest(BaseModel):
    coordinates: list[tuple[float, float]] = Field(min_length=2, max_length=100)
    sources: list[int] | None = None
    destinations: list[int] | None = None


class MatchRequest(BaseModel):
    coordinates: list[tuple[float, float]] = Field(min_length=2, max_length=10_000)
    timestamps: list[int] | None = None
    radiuses: list[float] | None = None


class NearestRequest(BaseModel):
    coordinate: tuple[float, float]
    number: int = Field(default=1, ge=1, le=10)


def _routing_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=503, detail="Routing service unavailable")


@router.post("/route")
async def route(request: RouteRequest):
    try:
        return await _client.route(
            request.coordinates,
            alternatives=request.alternatives,
            steps=request.steps,
        )
    except (RoutingError, ValueError) as exc:
        raise _routing_error(exc) from exc


@router.post("/table")
async def table(request: TableRequest):
    try:
        return await _client.table(
            request.coordinates,
            sources=request.sources,
            destinations=request.destinations,
        )
    except (RoutingError, ValueError) as exc:
        raise _routing_error(exc) from exc


@router.post("/match")
async def match(request: MatchRequest):
    try:
        return await _client.match(
            request.coordinates,
            timestamps=request.timestamps,
            radiuses=request.radiuses,
        )
    except (RoutingError, ValueError) as exc:
        raise _routing_error(exc) from exc


@router.post("/nearest")
async def nearest(request: NearestRequest):
    try:
        return await _client.nearest(request.coordinate, number=request.number)
    except (RoutingError, ValueError) as exc:
        raise _routing_error(exc) from exc
