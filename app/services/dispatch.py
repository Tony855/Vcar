import math
from app.db.core import db
from app.db.repositories.user_repo import UserRepository

class DispatchService:
    @staticmethod
    async def get_nearby_drivers(lng, lat, radius_km=20):
        # 获取 20km 内所有在线司机（带距离）
        return await db.redis.geosearch("driver:online", longitude=lng, latitude=lat, radius=radius_km, unit="km", sort="ASC", withdist=True)
    
    @staticmethod
    async def remove_order_from_pool(oid):
        await db.redis.zrem("order:pool", str(oid))
        
    @staticmethod
    async def go_online(driver_tid, lng, lat):
        await db.redis.geoadd("driver:online", (lng, lat, driver_tid))

    @classmethod
    async def smart_dispatch(cls, pickup_lng, pickup_lat, top_n=3):
        """智能派单：计算多维得分，返回前 N 名最匹配的司机"""
        raw_drivers = await cls.get_nearby_drivers(pickup_lng, pickup_lat)
        if not raw_drivers:
            return []

        # 查数据库获取这批司机的评分
        driver_ids = [int(d[0]) for d in raw_drivers]
        ratings = await UserRepository.get_drivers_info(driver_ids)

        scored_drivers = []
        for d in raw_drivers:
            tg_id = int(d[0])
            dist = float(d[1]) # 直线距离 km
            rating = ratings.get(tg_id, 5.0)

            # 🔥 核心打分算法
            # 1. 距离得分 (满分 60)：距离越近分越高，超过 20km 为 0 分
            dist_score = max(0, (20 - dist) / 20) * 60 
            
            # 2. 评分得分 (满分 40)：5.0分得满分
            rating_score = (rating / 5.0) * 40 
            
            total_score = dist_score + rating_score
            
            # 记录得分
            scored_drivers.append({
                "tg_id": tg_id,
                "dist": dist,
                "rating": rating,
                "score": total_score
            })

        # 按得分从高到低排序，取前 N 名
        scored_drivers.sort(key=lambda x: x["score"], reverse=True)
        return scored_drivers[:top_n]
