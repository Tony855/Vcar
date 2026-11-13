from __future__ import annotations

import math
from typing import Sequence

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.core.security import verify_token
from app.core.config import settings
from app.infrastructure.osrm.client import OSRMClient, RoutingError

router = APIRouter(
    prefix="/routing",
    tags=["routing"],
    dependencies=[Depends(verify_token)],
)
_client = OSRMClient()


def _validate_coordinate_input(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError("Coordinates must be longitude/latitude pairs")
    lon, lat = value
    if (
        isinstance(lon, bool) or not isinstance(lon, (int, float))
        or isinstance(lat, bool) or not isinstance(lat, (int, float))
        or not math.isfinite(lon) or not math.isfinite(lat)
        or not -180 <= lon <= 180 or not -90 <= lat <= 90
    ):
        raise ValueError("Coordinates must be finite numbers within longitude/latitude bounds")
    return value


def _validate_coordinates_input(value):
    if not isinstance(value, list):
        raise ValueError("Coordinates must be a list")
    for coordinate in value:
        _validate_coordinate_input(coordinate)
    return value


def _validate_integer_indexes(value):
    if value is not None and (
        not isinstance(value, list)
        or any(isinstance(item, bool) or not isinstance(item, int) for item in value)
    ):
        raise ValueError("Coordinate indexes must be integers")
    return value


def _validate_timestamps_input(value):
    if value is not None and (
        not isinstance(value, list)
        or any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in value)
    ):
        raise ValueError("Timestamps must be non-negative integers")
    return value


def _validate_radiuses_input(value):
    if value is not None and (
        not isinstance(value, list)
        or any(
            isinstance(item, bool) or not isinstance(item, (int, float))
            or not math.isfinite(item) or item <= 0
            for item in value
        )
    ):
        raise ValueError("Radiuses must be finite positive numbers")
    return value


class RouteRequest(BaseModel):
    coordinates: list[tuple[float, float]] = Field(min_length=2, max_length=25)
    alternatives: bool = True
    steps: bool = True

    @field_validator("coordinates", mode="before")
    @classmethod
    def validate_coordinates(cls, value):
        return _validate_coordinates_input(value)


class TableRequest(BaseModel):
    coordinates: list[tuple[float, float]] = Field(min_length=2, max_length=100)
    sources: list[int] | None = None
    destinations: list[int] | None = None

    @field_validator("coordinates", mode="before")
    @classmethod
    def validate_coordinates(cls, value):
        return _validate_coordinates_input(value)

    @field_validator("sources", "destinations", mode="before")
    @classmethod
    def validate_indexes(cls, value):
        return _validate_integer_indexes(value)


class MatchRequest(BaseModel):
    coordinates: list[tuple[float, float]] = Field(min_length=2, max_length=10_000)
    timestamps: list[int] | None = None
    radiuses: list[float] | None = None

    @field_validator("coordinates", mode="before")
    @classmethod
    def validate_coordinates(cls, value):
        return _validate_coordinates_input(value)

    @field_validator("timestamps", mode="before")
    @classmethod
    def validate_timestamps(cls, value):
        return _validate_timestamps_input(value)

    @field_validator("radiuses", mode="before")
    @classmethod
    def validate_radiuses(cls, value):
        return _validate_radiuses_input(value)


class NearestRequest(BaseModel):
    coordinate: tuple[float, float]
    number: int = Field(default=1, ge=1, le=10)

    @field_validator("coordinate", mode="before")
    @classmethod
    def validate_coordinate(cls, value):
        return _validate_coordinate_input(value)

    @field_validator("number", mode="before")
    @classmethod
    def validate_number(cls, value):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Nearest result count must be an integer")
        return value


def _routing_error(exc: Exception) -> HTTPException:
    if isinstance(exc, ValueError):
        return HTTPException(status_code=422, detail=str(exc))
    return HTTPException(status_code=503, detail="Routing service unavailable")


@router.post("/route")
async def route(request: RouteRequest):
    if not settings.osrm_enabled:
        raise HTTPException(status_code=503, detail="Routing service is disabled")
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
    if not settings.osrm_enabled:
        raise HTTPException(status_code=503, detail="Routing service is disabled")
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
    if not settings.osrm_enabled:
        raise HTTPException(status_code=503, detail="Routing service is disabled")
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
    if not settings.osrm_enabled:
        raise HTTPException(status_code=503, detail="Routing service is disabled")
    try:
        return await _client.nearest(request.coordinate, number=request.number)
    except (RoutingError, ValueError) as exc:
        raise _routing_error(exc) from exc
