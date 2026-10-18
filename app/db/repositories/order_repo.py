from app.db.core import db
class OrderRepository:
    @staticmethod
    async def create(cid, plng, plat, dlng, dlat, dist, price):
        async with db.pool.acquire() as c:
            return dict(await c.fetchrow("INSERT INTO orders (customer_id, pickup_lng, pickup_lat, dropoff_lng, dropoff_lat, distance_km, price_estimate) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id", cid, plng, plat, dlng, dlat, dist, price))
    
    @staticmethod
    async def get(oid):
        async with db.pool.acquire() as c:
            row = await c.fetchrow("""
                SELECT o.*, cu.telegram_id AS c_tg, cu.first_name AS c_name, 
                       du.telegram_id AS d_tg, du.first_name AS d_name, du.phone AS d_phone, du.plate_number AS d_plate, du.car_photo AS d_car_photo
                FROM orders o LEFT JOIN users cu ON o.customer_id=cu.id LEFT JOIN users du ON o.driver_id=du.id WHERE o.id=$1
            """, oid)
            return dict(row) if row else None
            
    @staticmethod
    async def get_active_order_by_user(uid):
        async with db.pool.acquire() as c:
            row = await c.fetchrow("SELECT o.*, c.telegram_id AS c_tg, d.telegram_id AS d_tg FROM orders o LEFT JOIN users c ON o.customer_id = c.id LEFT JOIN users d ON o.driver_id = d.id WHERE (o.customer_id = $1 OR o.driver_id = $1) AND status IN ('ACCEPTED','ARRIVED','IN_PROGRESS')", uid)
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
    async def cancel_with_fee(oid, uid, role, fee=0.0):
        async with db.pool.acquire() as c:
            return (await c.execute("UPDATE orders SET status='CANCELLED', cancel_by=$1, cancel_fee=$2 WHERE id=$3 AND status IN ('DISPATCHING','ACCEPTED','ARRIVED') AND (customer_id=$4 OR driver_id=$4)", role, fee, oid, uid)).endswith("1")

    @staticmethod
    async def get_customer_orders(cid):
        async with db.pool.acquire() as c:
            rows = await c.fetch("SELECT id, status, price_estimate FROM orders WHERE customer_id=$1 ORDER BY id DESC LIMIT 5", cid)
            return [dict(r) for r in rows]

    @staticmethod
    async def get_driver_orders(did):
        async with db.pool.acquire() as c:
            rows = await c.fetch("SELECT id, status, price_estimate FROM orders WHERE driver_id=$1 ORDER BY id DESC LIMIT 5", did)
            return [dict(r) for r in rows]
