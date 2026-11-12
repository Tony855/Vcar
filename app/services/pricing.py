import math

from app.core.config import settings
from app.db.core import db
from app.infrastructure.osrm.client import OSRMClient, RoutingError


class PricingInputError(ValueError):
    """Raised when coordinates or configured fare inputs are invalid."""


class PricingService:
    @staticmethod
    def _coordinate(value, name, minimum, maximum):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise PricingInputError(f"{name} must be a numeric coordinate")
        value = float(value)
        if not math.isfinite(value) or not minimum <= value <= maximum:
            raise PricingInputError(f"{name} is outside its valid range")
        return value

    @classmethod
    def _coordinates(cls, plat, plng, dlat, dlng):
        return (
            cls._coordinate(plat, "pickup latitude", -90, 90),
            cls._coordinate(plng, "pickup longitude", -180, 180),
            cls._coordinate(dlat, "dropoff latitude", -90, 90),
            cls._coordinate(dlng, "dropoff longitude", -180, 180),
        )

    @staticmethod
    def _nonnegative_finite(value, name):
        try:
            parsed = float(value)
        except (TypeError, ValueError, OverflowError) as exc:
            raise PricingInputError(f"{name} must be a finite non-negative number") from exc
        if not math.isfinite(parsed) or parsed < 0:
            raise PricingInputError(f"{name} must be a finite non-negative number")
        return parsed

    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        lat1, lon1, lat2, lon2 = PricingService._coordinates(lat1, lon1, lat2, lon2)
        radius_km = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        value = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2
        )
        value = min(1.0, max(0.0, value))
        return radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))

    @classmethod
    async def estimate(cls, plat, plng, dlat, dlng):
        plat, plng, dlat, dlng = cls._coordinates(plat, plng, dlat, dlng)
        async with db.pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT key, value FROM settings WHERE key IN ('base_fare', 'per_km_rate')"
            )
            data = {row["key"]: row["value"] for row in rows}
            base_fare = cls._nonnegative_finite(data.get("base_fare", 1.0), "base_fare")
            per_km_rate = cls._nonnegative_finite(data.get("per_km_rate", 0.5), "per_km_rate")

        if settings.osrm_enabled:
            result = await OSRMClient().route(
                [(plng, plat), (dlng, dlat)],
                alternatives=False,
                steps=False,
            )
            routes = result.get("routes") if isinstance(result, dict) else None
            if not routes:
                raise RoutingError("No drivable route found for this trip")
            try:
                route_distance_m = cls._nonnegative_finite(routes[0]["distance"], "OSRM route distance")
            except (KeyError, IndexError, TypeError) as exc:
                raise RoutingError("Routing service returned an invalid distance") from exc
            distance_km = route_distance_m / 1000.0
        else:
            distance_km = cls.haversine(plat, plng, dlat, dlng) * 1.3

        if not math.isfinite(distance_km) or distance_km < 0:
            raise RoutingError("Routing service returned an invalid distance")
        distance_km = round(distance_km, 1)
        price = base_fare + distance_km * per_km_rate
        if not math.isfinite(price) or price < 0:
            raise PricingInputError("Calculated fare is outside the supported numeric range")
        return distance_km, round(price, 2)
