from app.db.core import db
class DispatchService:
    @staticmethod
    async def get_nearby_drivers(lng, lat, radius_km=20):
        return await db.redis.geosearch("driver:online", longitude=lng, latitude=lat, radius=radius_km, unit="km", sort="ASC", withdist=True)
    @staticmethod
    async def remove_order_from_pool(oid):
        await db.redis.zrem("order:pool", str(oid))
    @staticmethod
    async def go_online(driver_tid, lng, lat):
        await db.redis.geoadd("driver:online", (lng, lat, driver_tid))
