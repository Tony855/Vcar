import math
from app.db.core import db

class PricingService:
    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        # 【核心修复】强制将所有入参转为 float，防止 Decimal 参与计算报错
        lat1, lon1, lat2, lon2 = float(lat1), float(lon1), float(lat2), float(lon2)
        
        R = 6371.0
        dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
        a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
        return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    @classmethod
    async def estimate(cls, plat, plng, dlat, dlng):
        async with db.pool.acquire() as c:
            rows = await c.fetch("SELECT key, value FROM settings")
            data = {r['key']: float(r['value']) for r in rows}
            base, per_km = data.get('base_fare', 1.0), data.get('per_km_rate', 0.5)
        
        dist = round(cls.haversine(plat, plng, dlat, dlng) * 1.3, 1)
        return dist, round(base + dist * per_km, 2)
