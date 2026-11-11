import math

from app.core.config import settings
from app.db.core import db
from app.infrastructure.osrm.client import OSRMClient, RoutingError


class PricingService:
    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
        radius_km = 6371.0
        dlat = math.radians(lat2 - lat1)
        dlon = math.radians(lon2 - lon1)
        value = (
            math.sin(dlat / 2) ** 2
            + math.cos(math.radians(lat1))
            * math.cos(math.radians(lat2))
            * math.sin(dlon / 2) ** 2
        )
        return radius_km * 2 * math.atan2(math.sqrt(value), math.sqrt(1 - value))

    @classmethod
    async def estimate(cls, plat, plng, dlat, dlng):
        async with db.pool.acquire() as connection:
            rows = await connection.fetch(
                "SELECT key, value FROM settings WHERE key IN ('base_fare', 'per_km_rate')"
            )
            data = {row["key"]: float(row["value"]) for row in rows}
            base_fare = data.get("base_fare", 1.0)
            per_km_rate = data.get("per_km_rate", 0.5)

        if settings.osrm_enabled:
            result = await OSRMClient().route(
                [(float(plng), float(plat)), (float(dlng), float(dlat))],
                alternatives=False,
                steps=False,
            )
            routes = result.get("routes") or []
            if not routes:
                raise RoutingError("No drivable route found for this trip")
            distance_km = float(routes[0]["distance"]) / 1000.0
        else:
            # Compatibility mode for deployments before OSRM graph activation.
            distance_km = cls.haversine(plat, plng, dlat, dlng) * 1.3

        distance_km = round(distance_km, 1)
        return distance_km, round(base_fare + distance_km * per_km_rate, 2)
