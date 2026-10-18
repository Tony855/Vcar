from app.db.core import db
class OrderRepository:
    @staticmethod
    async def create(cid, plng, plat, dlng, dlat, dist, price):
        async with db.pool.acquire() as c:
            return dict(await c.fetchrow("INSERT INTO orders (customer_id, pickup_lng, pickup_lat, dropoff_lng, dropoff_lat, distance_km, price_estimate) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id", cid, plng, plat, dlng, dlat, dist, price))
    @staticmethod
    async def get(oid):
        async with db.pool.acquire() as c:
            row = await c.fetchrow('''SELECT o.*, cu.telegram_id AS c_tg, du.telegram_id AS d_tg, du.first_name AS d_name, du.phone AS d_phone, du.plate_number AS d_plate, du.car_photo AS d_car_photo FROM orders o LEFT JOIN users cu ON o.customer_id=cu.id LEFT JOIN users du ON o.driver_id=du.id WHERE o.id=$1''', oid)
            return dict(row) if row else None
    @staticmethod
    async def accept(oid, did):
        async with db.pool.acquire() as c:
            return (await c.execute("UPDATE orders SET driver_id=$1, status='ACCEPTED' WHERE id=$2 AND status='DISPATCHING'", did, oid)).endswith("1")
    @staticmethod
    async def update_status(oid, ns, es):
        async with db.pool.acquire() as c:
            return (await c.execute("UPDATE orders SET status=$1 WHERE id=$2 AND status=$3", ns, oid, es)).endswith("1")
    @staticmethod
    async def cancel(oid, uid):
        async with db.pool.acquire() as c:
            return (await c.execute("UPDATE orders SET status='CANCELLED' WHERE id=$1 AND status IN ('DISPATCHING', 'ACCEPTED') AND (customer_id=$2 OR driver_id=$2)", oid, uid)).endswith("1")
