from app.db.core import db
import logging

class DispatchService:
    @staticmethod
    async def get_nearby_drivers(lng, lat, radius_km=20):
        # 暂时放宽到 50km 用于排查，确认是不是坐标太远
        return await db.redis.geosearch("driver:online", longitude=lng, latitude=lat, radius=radius_km, unit="km", sort="ASC", withdist=True)
    
    @staticmethod
    async def remove_order_from_pool(oid):
        await db.redis.zrem("order:pool", str(oid))
        
    @staticmethod
    async def go_online(driver_tid, lng, lat):
        await db.redis.geoadd("driver:online", (float(lng), float(lat), str(driver_tid)))

    @staticmethod
    async def go_offline(driver_tid):
        await db.redis.zrem("driver:online", str(driver_tid))

    @classmethod
    async def smart_dispatch(cls, pickup_lng, pickup_lat, top_n=3):
        # 扩大到 50km 进行测试
        raw_drivers = await cls.get_nearby_drivers(pickup_lng, pickup_lat, radius_km=50)
        logging.info(f"🔍 [派单] 乘客坐标({pickup_lng},{pickup_lat})，50km内发现原始司机: {raw_drivers}")
        
        if not raw_drivers:
            logging.warning("❌ [派单] 50km 内没有发现任何上线司机！")
            return []

        async with db.pool.acquire() as c:
            busy_rows = await c.fetch("SELECT DISTINCT driver_id FROM orders WHERE status IN ('ACCEPTED', 'ARRIVED', 'IN_PROGRESS') AND driver_id IS NOT NULL")
            busy_ids = {r['driver_id'] for r in busy_rows}
            
            tg_ids = [int(d[0]) for d in raw_drivers]
            user_rows = await c.fetch("SELECT id, telegram_id, rating, locale FROM users WHERE telegram_id = ANY($1)", tg_ids)
            user_map = {r['telegram_id']: {"db_id": r['id'], "rating": float(r['rating'] or 5.0), "locale": r['locale'] or "en"} for r in user_rows}
            
            logging.info(f"🔍 [派单] 忙碌内部ID: {busy_ids}")
            logging.info(f"🔍 [派单] 数据库中匹配到的司机: {user_map}")

        scored_drivers = []
        for d in raw_drivers:
            tg_id = int(d[0])
            dist = float(d[1])
            
            if tg_id not in user_map:
                logging.warning(f"❌ [派单] 司机 TG:{tg_id} 不在数据库 users 表中，被过滤")
                continue
                
            db_id = user_map[tg_id]["db_id"]
            rating = user_map[tg_id]["rating"]

            # 如果是忙碌司机，直接跳过
            if db_id in busy_ids:
                logging.warning(f"❌ [派单] 司机 TG:{tg_id} (DB_ID:{db_id}) 正在忙碌，被过滤")
                continue

            dist_score = max(0, (50 - dist) / 50) * 60
            rating_score = (rating / 5.0) * 40
            total_score = dist_score + rating_score
            
            scored_drivers.append({
                "tg_id": tg_id,
                "dist": dist,
                "rating": rating,
                "score": total_score, "locale": user_map[tg_id].get("locale", "en")
            })

        scored_drivers.sort(key=lambda x: x["score"], reverse=True)
        logging.info(f"✅ [派单] 最终筛选出 {len(scored_drivers)} 个司机: {scored_drivers}")
        return scored_drivers[:top_n]
