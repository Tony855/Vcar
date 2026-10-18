import math
from app.db.core import get_pricing
def haversine(lat1, lon1, lat2, lon2):
    R = 6371.0; dlat, dlon = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
async def calculate_fixed_price(plat, plng, dlat, dlng):
    base, per_km = await get_pricing()
    dist = round(haversine(plat, plng, dlat, dlng) * 1.3, 1)
    return dist, round(base + dist * per_km, 2)
