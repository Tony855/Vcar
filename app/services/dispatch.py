import logging

from app.core.config import settings
from app.db.core import db
from app.infrastructure.osrm.client import OSRMClient, RoutingError


class DispatchService:
    @staticmethod
    async def get_nearby_drivers(lng, lat, radius_km=20):
        now = __import__("time").time()
        expired = await db.redis.zrangebyscore("driver:heartbeat", "-inf", now - 120)
        if expired:
            await db.redis.zrem("driver:heartbeat", *expired)
            await db.redis.zrem("driver:online", *expired)
            await db.redis.zrem("driver:location:version", *expired)
        return await db.redis.geosearch(
            "driver:online",
            longitude=float(lng),
            latitude=float(lat),
            radius=radius_km,
            unit="km",
            sort="ASC",
            withdist=True,
            withcoord=True,
        )

    @staticmethod
    async def remove_order_from_pool(oid):
        await db.redis.zrem("order:pool", str(oid))

    @staticmethod
    async def go_online(driver_tid, lng, lat):
        await db.redis.geoadd("driver:online", (float(lng), float(lat), str(driver_tid)))
        await db.redis.zadd("driver:heartbeat", {str(driver_tid): __import__("time").time()})

    @staticmethod
    async def go_offline(driver_tid):
        await db.redis.zrem("driver:online", str(driver_tid))
        await db.redis.zrem("driver:heartbeat", str(driver_tid))
        await db.redis.zrem("driver:location:version", str(driver_tid))

    @classmethod
    async def smart_dispatch(cls, pickup_lng, pickup_lat, top_n=3):
        raw_drivers = await cls.get_nearby_drivers(
            pickup_lng, pickup_lat, radius_km=50
        )
        logging.info(
            "[dispatch] pickup=(%s,%s), nearby drivers=%s",
            pickup_lng, pickup_lat, len(raw_drivers),
        )
        if not raw_drivers:
            return []

        candidates = []
        async with db.pool.acquire() as connection:
            busy_rows = await connection.fetch(
                """
                SELECT DISTINCT driver_id FROM orders
                WHERE status IN ('ACCEPTED','ARRIVED','IN_PROGRESS')
                  AND driver_id IS NOT NULL
                """
            )
            busy_ids = {row["driver_id"] for row in busy_rows}
            telegram_ids = [int(driver[0]) for driver in raw_drivers]
            user_rows = await connection.fetch(
                """
                SELECT id, telegram_id, rating, locale
                FROM users
                WHERE telegram_id = ANY($1) AND driver_status='APPROVED'
                """,
                telegram_ids,
            )
            user_map = {
                row["telegram_id"]: {
                    "db_id": row["id"],
                    "rating": float(row["rating"] or 5.0),
                    "locale": row["locale"] or "en",
                }
                for row in user_rows
            }

        for driver in raw_drivers:
            telegram_id = int(driver[0])
            if telegram_id not in user_map:
                continue
            user = user_map[telegram_id]
            if user["db_id"] in busy_ids:
                continue

            distance_km = float(driver[1])
            coordinates = None
            if len(driver) > 2 and driver[2]:
                try:
                    coordinates = (float(driver[2][0]), float(driver[2][1]))
                except (TypeError, ValueError, IndexError):
                    coordinates = None
            candidates.append({
                "tg_id": telegram_id,
                "db_id": user["db_id"],
                "dist": distance_km,
                "coords": coordinates,
                "rating": user["rating"],
                "locale": user["locale"],
            })

        if not candidates:
            return []

        eta_by_driver = {}
        table_result_available = False
        if settings.osrm_enabled:
            routable = [candidate for candidate in candidates if candidate["coords"]]
            if routable:
                try:
                    matrix = await OSRMClient().table(
                        [candidate["coords"] for candidate in routable]
                        + [(float(pickup_lng), float(pickup_lat))],
                        sources=list(range(len(routable))),
                        destinations=[len(routable)],
                        annotations="duration,distance",
                    )
                    table_result_available = True
                    durations = matrix.get("durations") or []
                    for candidate, row in zip(routable, durations):
                        if row and row[0] is not None:
                            eta_by_driver[candidate["tg_id"]] = float(row[0])
                except (RoutingError, ValueError) as exc:
                    logging.warning("OSRM Table unavailable; dispatch ranking falls back to GEO distance: %s", exc)

        scored_drivers = []
        for candidate in candidates:
            eta_seconds = eta_by_driver.get(candidate["tg_id"])
            if table_result_available and eta_seconds is None:
                continue

            if eta_seconds is not None:
                eta_score = max(0.0, (1800.0 - eta_seconds) / 1800.0) * 60.0
            else:
                eta_score = max(0.0, (50.0 - candidate["dist"]) / 50.0) * 60.0

            rating_score = (candidate["rating"] / 5.0) * 40.0
            scored_drivers.append({
                "tg_id": candidate["tg_id"],
                "dist": candidate["dist"],
                "eta_seconds": round(eta_seconds, 1) if eta_seconds is not None else None,
                "rating": candidate["rating"],
                "score": eta_score + rating_score,
                "locale": candidate["locale"],
            })

        scored_drivers.sort(key=lambda item: item["score"], reverse=True)
        logging.info("[dispatch] ranked %s eligible drivers", len(scored_drivers))
        return scored_drivers[:top_n]
