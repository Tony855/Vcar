import asyncpg
import redis.asyncio as redis
from app.config import settings
class DB:
    def __init__(self): self.pool = None; self.redis = None
    async def init(self):
        self.pool = await asyncpg.create_pool(settings.database_url, min_size=2, max_size=10)
        self.redis = redis.from_url("redis://127.0.0.1:6379/0", decode_responses=True)
db = DB()

async def get_user(tid, name=None, username=None):
    async with db.pool.acquire() as c:
        return dict(await c.fetchrow("""
            INSERT INTO users (telegram_id, first_name, username) VALUES ($1, $2, $3)
            ON CONFLICT (telegram_id) DO UPDATE SET first_name=EXCLUDED.first_name, username=EXCLUDED.username
            RETURNING *
        """, tid, name, username))

async def update_driver_status(tid, status):
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET driver_status=$1 WHERE telegram_id=$2", status, tid)

async def get_pending_drivers():
    async with db.pool.acquire() as c:
        return [dict(r) for r in await c.fetch("SELECT telegram_id, first_name, username FROM users WHERE driver_status='PENDING'")]

# --- 订单相关 ---
async def create_order(cid, plng, plat, dlng, dlat, dist, price):
    async with db.pool.acquire() as c:
        return dict(await c.fetchrow("INSERT INTO orders (customer_id, pickup_lng, pickup_lat, dropoff_lng, dropoff_lat, distance_km, price_estimate) VALUES ($1,$2,$3,$4,$5,$6,$7) RETURNING id", cid, plng, plat, dlng, dlat, dist, price))
async def get_order(oid):
    async with db.pool.acquire() as c:
        row = await c.fetchrow("""
            SELECT o.*, 
                   cu.telegram_id AS c_tg, cu.username AS c_user, cu.first_name AS c_name,
                   du.telegram_id AS d_tg, du.username AS d_user, du.first_name AS d_name
            FROM orders o 
            LEFT JOIN users cu ON o.customer_id=cu.id 
            LEFT JOIN users du ON o.driver_id=du.id 
            WHERE o.id=$1
        """, oid)
        return dict(row) if row else None
async def accept_order(oid, did):
    async with db.pool.acquire() as c:
        return (await c.execute("UPDATE orders SET driver_id=$1, status='ACCEPTED' WHERE id=$2 AND status='DISPATCHING'", did, oid)).endswith("1")
async def update_status(oid, ns, es):
    async with db.pool.acquire() as c:
        return (await c.execute("UPDATE orders SET status=$1 WHERE id=$2 AND status=$3", ns, oid, es)).endswith("1")
async def cancel_order(oid, uid):
    async with db.pool.acquire() as c:
        return (await c.execute("UPDATE orders SET status='CANCELLED' WHERE id=$1 AND status IN ('DISPATCHING', 'ACCEPTED') AND (customer_id=$2 OR driver_id=$2)", oid, uid)).endswith("1")
async def get_user_orders(uid):
    async with db.pool.acquire() as c:
        return [dict(r) for r in await c.fetch("SELECT id, status, price_estimate FROM orders WHERE customer_id=$1 ORDER BY id DESC LIMIT 5", uid)]
async def get_pricing():
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT key, value FROM settings")
        data = {r['key']: float(r['value']) for r in rows}
        return data.get('base_fare', 1.0), data.get('per_km_rate', 0.5)
