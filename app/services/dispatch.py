from app.db.core import db
from app.db.repositories.user_repo import UserRepository

class DispatchService:
    @staticmethod
    async def get_nearby_drivers(lng, lat, radius_km=20):
        # 仅从 Redis 的 driver:online 集合中检索（没上线的绝对搜不到）
        return await db.redis.geosearch("driver:online", longitude=lng, latitude=lat, radius=radius_km, unit="km", sort="ASC", withdist=True)
    
    @staticmethod
    async def remove_order_from_pool(oid):
        await db.redis.zrem("order:pool", str(oid))
        
    @staticmethod
    async def go_online(driver_tid, lng, lat):
        await db.redis.geoadd("driver:online", (lng, lat, driver_tid))

    @staticmethod
    async def go_offline(driver_tid):
        await db.redis.zrem("driver:online", str(driver_tid))

    @classmethod
    async def smart_dispatch(cls, pickup_lng, pickup_lat, top_n=3):
        raw_drivers = await cls.get_nearby_drivers(pickup_lng, pickup_lat)
        if not raw_drivers:
            return []

        # 【核心闸门】查出所有正在行程中的司机，直接过滤掉
        async with db.pool.acquire() as c:
            busy_rows = await c.fetch("SELECT DISTINCT driver_id FROM orders WHERE status IN ('ACCEPTED', 'ARRIVED', 'IN_PROGRESS') AND driver_id IS NOT NULL")
            busy_driver_ids = {r['driver_id'] for r in busy_rows}

        driver_ids = [int(d[0]) for d in raw_drivers]
        ratings = await UserRepository.get_drivers_info(driver_ids)

        scored_drivers = []
        for d in raw_drivers:
            tg_id = int(d[0])
            # 如果是忙碌司机，直接跳过
            # 注意：这里为了简化，我们通过查数据库查出的是系统内部自增ID，而 tg_id 是 TG ID。
            # 所以换个方式过滤：直接查这个 TG ID 有没有活跃订单
            async with db.pool.acquire() as c:
                has_active = await c.fetchval("""
                    SELECT 1 FROM orders o 
                    JOIN users u ON o.driver_id = u.id 
                    WHERE u.telegram_id = $1 AND o.status IN ('ACCEPTED', 'ARRIVED', 'IN_PROGRESS') LIMIT 1
                """, tg_id)
                if has_active:
                    continue # 司机正忙，跳过！

            dist = float(d[1])
            rating = ratings.get(tg_id, 5.0)
            dist_score = max(0, (20 - dist) / 20) * 60
            rating_score = (rating / 5.0) * 40
            total_score = dist_score + rating_score
            
            scored_drivers.append({
                "tg_id": tg_id,
                "dist": dist,
                "rating": rating,
                "score": total_score
            })

        scored_drivers.sort(key=lambda x: x["score"], reverse=True)
        return scored_drivers[:top_n]
