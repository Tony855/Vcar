from __future__ import annotations

from typing import Any, Sequence
import math

import httpx

from app.core.config import settings


class RoutingError(RuntimeError):
    """Raised when OSRM is unavailable or rejects a routing request."""


Coordinate = tuple[float, float]  # longitude, latitude
MAX_MATCHING_COORDINATES = 100  # matches docker-compose.osrm.yml --max-matching-size default


class OSRMClient:
    """Small async adapter around OSRM HTTP APIs.

    The client intentionally exposes domain-oriented methods rather than leaking
    OSRM URLs or response handling into routers and dispatch services.
    """

    def __init__(
        self,
        base_url: str | None = None,
        profile: str | None = None,
        timeout: float | None = None,
        max_table_coordinates: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = (base_url or settings.osrm_base_url).rstrip("/")
        self.profile = profile or settings.osrm_profile
        self.timeout = timeout or settings.osrm_timeout_seconds
        self.max_table_coordinates = max_table_coordinates or settings.osrm_max_table_coordinates
        self._transport = transport

    @staticmethod
    def _validate_coordinate(coordinate: Coordinate) -> None:
        if not isinstance(coordinate, (tuple, list)) or len(coordinate) != 2:
            raise ValueError("Coordinates must be (longitude, latitude)")
        lon, lat = coordinate
        if (
            isinstance(lon, bool) or not isinstance(lon, (int, float))
            or isinstance(lat, bool) or not isinstance(lat, (int, float))
            or not math.isfinite(lon) or not math.isfinite(lat)
        ):
            raise ValueError("Coordinates must contain finite numeric longitude and latitude")
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError("Coordinate is outside valid longitude/latitude bounds")

    @staticmethod
    def _validate_indexes(indexes: Sequence[int], coordinate_count: int, field: str) -> None:
        if not indexes:
            raise ValueError(f"{field} must contain at least one coordinate index")
        if any(
            isinstance(index, bool)
            or not isinstance(index, int)
            or index < 0
            or index >= coordinate_count
            for index in indexes
        ):
            raise ValueError(f"{field} contains an invalid coordinate index")

    @classmethod
    def _coordinates_path(cls, coordinates: Sequence[Coordinate]) -> str:
        if len(coordinates) < 2:
            raise ValueError("At least two coordinates are required")
        for coordinate in coordinates:
            cls._validate_coordinate(coordinate)
        return ";".join(f"{float(lon):.6f},{float(lat):.6f}" for lon, lat in coordinates)

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout, transport=self._transport) as client:
                response = await client.get(url, params=params)
                response.raise_for_status()
                payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise RoutingError("Routing service request failed") from exc
        if not isinstance(payload, dict) or payload.get("code") != "Ok":
            code = payload.get("code", "InvalidResponse") if isinstance(payload, dict) else "InvalidResponse"
            raise RoutingError(f"Routing service returned {code}")
        return payload

    async def route(
        self,
        coordinates: Sequence[Coordinate],
        *,
        alternatives: bool = True,
        steps: bool = True,
    ) -> dict[str, Any]:
        path = self._coordinates_path(coordinates)
        return await self._get(
            f"route/v1/{self.profile}/{path}",
            {"overview": "full", "geometries": "geojson", "steps": str(steps).lower(),
             "alternatives": str(alternatives).lower()},
        )

    async def table(
        self,
        coordinates: Sequence[Coordinate],
        *,
        sources: Sequence[int] | None = None,
        destinations: Sequence[int] | None = None,
        annotations: str = "duration,distance",
    ) -> dict[str, Any]:
        if len(coordinates) > self.max_table_coordinates:
            raise ValueError("Too many coordinates for one OSRM Table request")
        path = self._coordinates_path(coordinates)
        params: dict[str, Any] = {"annotations": annotations}
        if sources is not None:
            self._validate_indexes(sources, len(coordinates), "sources")
            params["sources"] = ";".join(str(i) for i in sources)
        if destinations is not None:
            self._validate_indexes(destinations, len(coordinates), "destinations")
            params["destinations"] = ";".join(str(i) for i in destinations)
        return await self._get(f"table/v1/{self.profile}/{path}", params)

    async def match(
        self,
        coordinates: Sequence[Coordinate],
        *,
        timestamps: Sequence[int] | None = None,
        radiuses: Sequence[float] | None = None,
    ) -> dict[str, Any]:
        if len(coordinates) > MAX_MATCHING_COORDINATES:
            raise ValueError("Too many coordinates for one OSRM Match request")
        path = self._coordinates_path(coordinates)
        params: dict[str, Any] = {"overview": "full", "geometries": "geojson", "steps": "true"}
        if timestamps is not None:
            if len(timestamps) != len(coordinates):
                raise ValueError("Timestamp count must match coordinate count")
            if any(
                isinstance(value, bool) or not isinstance(value, int) or value < 0
                for value in timestamps
            ):
                raise ValueError("Timestamps must be non-negative integers")
            params["timestamps"] = ";".join(str(value) for value in timestamps)
        if radiuses is not None:
            if len(radiuses) != len(coordinates):
                raise ValueError("Radius count must match coordinate count")
            if any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value <= 0
                for value in radiuses
            ):
                raise ValueError("Radiuses must be finite positive numbers")
            params["radiuses"] = ";".join(str(float(value)) for value in radiuses)
        return await self._get(f"match/v1/{self.profile}/{path}", params)

    async def nearest(self, coordinate: Coordinate, *, number: int = 1) -> dict[str, Any]:
        self._validate_coordinate(coordinate)
        if not 1 <= number <= 10:
            raise ValueError("Nearest result count must be between 1 and 10")
        lon, lat = coordinate
        path = f"{float(lon):.6f},{float(lat):.6f}"
        return await self._get(f"nearest/v1/{self.profile}/{path}", {"number": number})
