import logging

from app.core.config import settings
from app.db.core import db
from app.infrastructure.osrm.client import OSRMClient, RoutingError


# Remove an expired driver only if its heartbeat is still at or before the
# cutoff observed by this dispatch pass. A concurrent GPS publish must win.
_REMOVE_EXPIRED_DRIVERS = """
local cutoff = tonumber(ARGV[1])
local removed = 0
for index = 2, #ARGV do
    local member = ARGV[index]
    local heartbeat = redis.call('ZSCORE', KEYS[1], member)
    if not heartbeat or tonumber(heartbeat) <= cutoff then
        redis.call('ZREM', KEYS[1], member)
        redis.call('ZREM', KEYS[2], member)
        redis.call('ZREM', KEYS[3], member)
        removed = removed + 1
    end
end
return removed
"""

_SET_DRIVER_ONLINE = """
local current = redis.call('GET', KEYS[3])
if current and tonumber(current) > tonumber(ARGV[5]) then
    return 0
end
redis.call('SET', KEYS[3], ARGV[5], 'EX', 2592000)
redis.call('GEOADD', KEYS[1], ARGV[1], ARGV[2], ARGV[3])
redis.call('ZADD', KEYS[2], ARGV[4], ARGV[3])
return 1
"""

_SET_DRIVER_OFFLINE = """
local current = redis.call('GET', KEYS[4])
if current and tonumber(current) > tonumber(ARGV[2]) then
    return 0
end
redis.call('SET', KEYS[4], ARGV[2], 'EX', 2592000)
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
redis.call('ZREM', KEYS[3], ARGV[1])
return 1
"""


class DispatchService:
    @staticmethod
    async def get_nearby_drivers(lng, lat, radius_km=20):
        now = __import__("time").time()
        cutoff = now - 120
        # Bound each Redis script invocation. Long Redis outages or large fleets
        # can accumulate many stale members; process them in chunks while
        # retaining the atomic heartbeat recheck for every member.
        while True:
            expired = await db.redis.zrangebyscore(
                "driver:heartbeat", "-inf", cutoff, start=0, num=500
            )
            if not expired:
                break
            await db.redis.eval(
                _REMOVE_EXPIRED_DRIVERS, 3,
                "driver:heartbeat", "driver:online", "driver:location:version",
                cutoff, *expired,
            )
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
        async with db.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE users
                SET driver_is_online=TRUE,
                    driver_presence_generation=driver_presence_generation+1
                WHERE telegram_id=$1 AND driver_status='APPROVED'
                  AND driver_presence_generation < 9223372036854775807
                RETURNING driver_presence_generation
                """,
                int(driver_tid),
            )
        if not row:
            return False
        generation = int(row["driver_presence_generation"])
        applied = await db.redis.eval(
            _SET_DRIVER_ONLINE, 3,
            "driver:online", "driver:heartbeat", f"driver:presence:generation:{driver_tid}",
            float(lng), float(lat), str(driver_tid), __import__("time").time(), generation,
        )
        return applied == 1

    @staticmethod
    async def go_offline(driver_tid):
        async with db.pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE users
                SET driver_is_online=FALSE,
                    driver_presence_generation=driver_presence_generation+1
                WHERE telegram_id=$1 AND driver_status='APPROVED'
                  AND driver_presence_generation < 9223372036854775807
                RETURNING driver_presence_generation
                """,
                int(driver_tid),
            )
        if not row:
            return False
        generation = int(row["driver_presence_generation"])
        applied = await db.redis.eval(
            _SET_DRIVER_OFFLINE, 4,
            "driver:online", "driver:heartbeat", "driver:location:version",
            f"driver:presence:generation:{driver_tid}", str(driver_tid), generation,
        )
        return applied == 1

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
