import asyncio
import logging
import math
import os
import secrets
import sys
from datetime import datetime, timezone
import aiohttp
from fastapi import FastAPI, Depends, HTTPException, Response, Query, Header
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

# 动态获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(BASE_DIR)

from app.core.config import settings
from app.db.core import db
from app.services.driver_presence import is_driver_online
from app.services.driver_presence_projection import PRESENCE_RECONCILIATION_STATE
from app.services.order_event_outbox import run_order_event_outbox_worker
from app.services.driver_presence_projection import run_driver_presence_reconciliation_worker
from app.core.security import verify_token, verify_driver_token, create_access_token, verify_password
from app.modules.routing.router import router as routing_router
from app.modules.auth.router import router as auth_router
from app.modules.auth.ws_router import router as ws_auth_router
from app.modules.locations.router import router as locations_router
from app.modules.passengers.router import router as passengers_router
from app.modules.payments.router import router as wallet_router
from app.modules.orders.router import router as driver_orders_router

app = FastAPI()
logger = logging.getLogger(__name__)
_outbox_stop_event = None
_outbox_worker_task = None
_presence_stop_event = None
_presence_worker_task = None
app.include_router(routing_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api")
app.include_router(ws_auth_router, prefix="/api")
app.include_router(locations_router, prefix="/api/v1")
app.include_router(passengers_router, prefix="/api")
app.include_router(wallet_router, prefix="/api/v1")
app.include_router(driver_orders_router, prefix="/api/v1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=bool(settings.cors_origins),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

class LoginData(BaseModel): username: str; password: str
class PriceUpdate(BaseModel):
    base_fare: float
    per_km: float

    @field_validator("base_fare", "per_km")
    @classmethod
    def validate_fare(cls, value: float) -> float:
        if not math.isfinite(value) or value < 0:
            raise ValueError("fare must be a finite non-negative number")
        return value
class ReviewData(BaseModel): tg_id: int; status: str

@app.on_event("startup")
async def startup():
    global _outbox_stop_event, _outbox_worker_task
    global _presence_stop_event, _presence_worker_task
    await db.init()
    _outbox_stop_event = asyncio.Event()
    _outbox_worker_task = asyncio.create_task(
        run_order_event_outbox_worker(
            _outbox_stop_event,
            poll_interval=settings.order_event_outbox_poll_interval,
        ),
        name="order-event-outbox-worker",
    )
    _presence_stop_event = asyncio.Event()
    _presence_worker_task = asyncio.create_task(
        run_driver_presence_reconciliation_worker(_presence_stop_event),
        name="driver-presence-redis-reconciliation-worker",
    )


@app.on_event("shutdown")
async def shutdown():
    global _outbox_stop_event, _outbox_worker_task
    global _presence_stop_event, _presence_worker_task
    if _presence_stop_event is not None:
        _presence_stop_event.set()
    if _presence_worker_task is not None:
        await _presence_worker_task
        _presence_worker_task = None
    _presence_stop_event = None
    if _outbox_stop_event is not None:
        _outbox_stop_event.set()
    if _outbox_worker_task is not None:
        await _outbox_worker_task
        _outbox_worker_task = None
    _outbox_stop_event = None
    await db.close()

# 挂载静态文件
static_dir = os.path.join(BASE_DIR, "app", "web", "static")
os.makedirs(static_dir, exist_ok=True) # 确保目录存在

# ================= 司机小程序独立模块开始 =================
from fastapi.responses import HTMLResponse







# ================= 数据模型 =================
class LoginData(BaseModel):
    username: str
    password: str

class PriceUpdate(BaseModel):
    base_fare: float
    per_km: float

class SupportUpdate(BaseModel):
    username: str

# ================= 1. 鉴权与登录 =================
# ================= 1. 鉴权与登录 =================
@app.post("/api/login")
async def login(data: LoginData):
    if not settings.admin_password_hash:
        raise HTTPException(status_code=503, detail="管理员账号尚未初始化")
    if data.username != settings.admin_username or not verify_password(data.password, settings.admin_password_hash):
        raise HTTPException(status_code=401, detail="账号或密码错误")
    return {"token": create_access_token(subject=settings.admin_username, role="admin")}

# ================= 2. 管理后台接口 =================
@app.get("/api/stats", dependencies=[Depends(verify_token)])
async def get_stats():
    async with db.pool.acquire() as c:
        online_since = __import__("time").time() - 120
        online_drivers = await db.redis.zcount("driver:heartbeat", online_since, "+inf")
        return {
            "total_orders": await c.fetchval("SELECT COUNT(*) FROM orders"),
            "active_orders": await c.fetchval("SELECT COUNT(*) FROM orders WHERE status IN ('DISPATCHING','ACCEPTED','IN_PROGRESS')"),
            "total_users": await c.fetchval("SELECT COUNT(*) FROM users"),
            "drivers_online": online_drivers
        }

@app.get("/api/orders", dependencies=[Depends(verify_token)])
async def get_admin_orders():
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT o.id, o.status, o.distance_km, o.price_estimate, o.driver_id, du.plate_number as driver_plate FROM orders o LEFT JOIN users du ON o.driver_id=du.id ORDER BY o.id DESC LIMIT 50")
        return [dict(r) for r in rows]


def _parse_pricing_settings(rows):
    """Parse persisted fare settings, falling back safely on invalid values."""
    defaults = {"base_fare": 1.0, "per_km_rate": 0.5}
    parsed = dict(defaults)
    for row in rows:
        key = row["key"]
        if key not in defaults:
            continue
        try:
            value = float(row["value"])
        except (TypeError, ValueError, OverflowError):
            logger.warning("Invalid pricing setting %s; using default %s", key, defaults[key])
            continue
        if not math.isfinite(value):
            logger.warning("Non-finite pricing setting %s; using default %s", key, defaults[key])
            continue
        parsed[key] = value
    return parsed

@app.get("/api/pricing", dependencies=[Depends(verify_token)])
async def get_pricing():
    async with db.pool.acquire() as c:
        # 只查起步价和每公里，防止把客服字符串转 float 报错
        rows = await c.fetch("SELECT key, value FROM settings WHERE key IN ('base_fare', 'per_km_rate')")
        data = _parse_pricing_settings(rows)
        return {
            "base_fare": data["base_fare"],
            "per_km": data["per_km_rate"],
        }

@app.post("/api/pricing", dependencies=[Depends(verify_token)])
async def set_pricing(price: PriceUpdate):
    async with db.pool.acquire() as c:
        await c.execute("UPDATE settings SET value=$1 WHERE key='base_fare'", str(price.base_fare))
        await c.execute("UPDATE settings SET value=$1 WHERE key='per_km_rate'", str(price.per_km))
    return {"status": "ok"}

@app.get("/api/config/support", dependencies=[Depends(verify_token)])
async def get_support_config():
    async with db.pool.acquire() as c:
        val = await c.fetchval("SELECT value FROM settings WHERE key='support_username'")
        return {"username": val or "Tony855"}

@app.post("/api/config/support", dependencies=[Depends(verify_token)])
async def set_support_config(data: SupportUpdate):
    async with db.pool.acquire() as c:
        exists = await c.fetchval("SELECT 1 FROM settings WHERE key='support_username'")
        if exists:
            await c.execute("UPDATE settings SET value=$1 WHERE key='support_username'", data.username)
        else:
            await c.execute("INSERT INTO settings (key, value) VALUES ('support_username', $1)", data.username)
    return {"status": "ok"}

# ================= 3. 司机小程序接口 =================
@app.get("/api/driver/{tg_id}/profile")
async def get_driver_profile(tg_id: int, claims: dict = Depends(verify_driver_token)):
    if str(tg_id) != str(claims["sub"]):
        raise HTTPException(status_code=403, detail="Cannot access another driver profile")
    async with db.pool.acquire() as c:
        driver = await c.fetchrow("SELECT id, rating, plate_number FROM users WHERE telegram_id=$1", tg_id)
        if not driver: return {"error": "Driver not found"}
        stats = await c.fetchrow("SELECT COALESCE(AVG(rating), 5.0) as avg_r, COUNT(id) as total_r FROM reviews WHERE driver_id=$1", driver['id'])
        order_stats = await c.fetchrow("SELECT COUNT(*) as total_orders, COALESCE(SUM(price_estimate), 0) as total_income FROM orders WHERE driver_id=$1 AND status='COMPLETED'", driver['id'])
        
        # 🌟 新增：去 Redis 里查他是不是真的在线
        is_online = await is_driver_online(db.redis, tg_id)
        real_status = "在线" if is_online else "离线"
        
        return {
            "plate": driver['plate_number'] or "未绑定",
            "rating": round(float(stats['avg_r']), 1),
            "total_reviews": stats['total_r'],
            "completed_orders": order_stats['total_orders'],
            "total_income": round(float(order_stats['total_income']), 2),
            "is_online": real_status
        }

@app.get("/api/driver/{tg_id}/reviews")
async def get_driver_reviews(tg_id: int, claims: dict = Depends(verify_driver_token)):
    if str(tg_id) != str(claims["sub"]):
        raise HTTPException(status_code=403, detail="Cannot access another driver reviews")
    async with db.pool.acquire() as c:
        driver = await c.fetchrow("SELECT id FROM users WHERE telegram_id=$1", tg_id)
        if not driver: return []
        rows = await c.fetch("SELECT rating, comment, created_at FROM reviews WHERE driver_id=$1 ORDER BY created_at DESC LIMIT 10", driver['id'])
        return [dict(r) for r in rows]

@app.get("/api/driver/{tg_id}/orders")
async def get_driver_orders(tg_id: int, claims: dict = Depends(verify_driver_token)):
    if str(tg_id) != str(claims["sub"]):
        raise HTTPException(status_code=403, detail="Cannot access another driver orders")
    async with db.pool.acquire() as c:
        driver = await c.fetchrow("SELECT id FROM users WHERE telegram_id=$1", tg_id)
        if not driver: return []
        orders = await c.fetch("SELECT id, status, price_estimate as price FROM orders WHERE driver_id=$1 ORDER BY id DESC LIMIT 20", driver['id'])
        res = []
        for o in orders:
            d = dict(o)
            d['time_str'] = f"订单号: #{d['id']}"
            res.append(d)
        return res

@app.get("/api/driver/{tg_id}/income")
async def get_driver_income(tg_id: int, claims: dict = Depends(verify_driver_token)):
    if str(tg_id) != str(claims["sub"]):
        raise HTTPException(status_code=403, detail="Cannot access another driver income")
    async with db.pool.acquire() as c:
        driver = await c.fetchrow("SELECT id FROM users WHERE telegram_id=$1", tg_id)
        if not driver: return []
        orders = await c.fetch("SELECT id, price_estimate as price FROM orders WHERE driver_id=$1 AND status='COMPLETED' ORDER BY id DESC LIMIT 50", driver['id'])
        return [dict(o) for o in orders]

# ================= 4. 静态文件与 SPA 回退 =================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
static_dir = os.path.join(BASE_DIR, "app", "web", "static")

if os.path.exists(os.path.join(static_dir, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")
app.mount("/admin", StaticFiles(directory=static_dir, html=True), name="admin")




@app.get("/api/admin/fleet", dependencies=[Depends(verify_token)])
async def get_live_fleet():
    async with db.pool.acquire() as c:
        rows = await c.fetch(
            """
            SELECT u.id, u.telegram_id, u.first_name, u.phone, u.plate_number,
                   u.rating, ST_Y(l.position::geometry) AS latitude,
                   ST_X(l.position::geometry) AS longitude,
                   l.accuracy_m, l.heading_deg, l.speed_mps,
                   l.recorded_at, l.received_at
            FROM users u
            LEFT JOIN driver_locations l ON l.driver_id=u.id
            WHERE u.driver_status='APPROVED'
            ORDER BY l.received_at DESC NULLS LAST
            """
        )
    return [
        {**dict(row), "online": bool(row["received_at"] and
          (datetime.now(timezone.utc) - row["received_at"]).total_seconds() <= 120)}
        for row in rows
    ]


@app.get("/metrics")
async def prometheus_metrics(authorization: str | None = Header(default=None)):
    """Private Prometheus metrics; disabled unless a dedicated token is configured."""
    expected = settings.metrics_bearer_token
    if not expected:
        raise HTTPException(status_code=404, detail="Metrics endpoint is disabled")
    supplied = ""
    if authorization and authorization.lower().startswith("bearer "):
        supplied = authorization[7:].strip()
    if not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Metrics authentication required")

    async with db.pool.acquire() as connection:
        pending_outbox = await connection.fetchval(
            "SELECT COUNT(*) FROM order_event_outbox WHERE delivered_at IS NULL"
        )
        oldest_outbox_age = await connection.fetchval(
            """
            SELECT COALESCE(EXTRACT(EPOCH FROM (now() - MIN(created_at))), 0)
            FROM order_event_outbox WHERE delivered_at IS NULL
            """
        )
        ready_outbox = await connection.fetchval(
            """
            SELECT COUNT(*) FROM order_event_outbox
            WHERE delivered_at IS NULL AND available_at <= now()
              AND (locked_at IS NULL OR locked_at < now() - interval '5 minutes')
            """
        )
        claimed_outbox = await connection.fetchval(
            """
            SELECT COUNT(*) FROM order_event_outbox
            WHERE delivered_at IS NULL AND locked_at IS NOT NULL AND lock_token IS NOT NULL
            """
        )
        expired_claims_outbox = await connection.fetchval(
            """
            SELECT COUNT(*) FROM order_event_outbox
            WHERE delivered_at IS NULL AND locked_at < now() - interval '5 minutes'
            """
        )
        high_retry_outbox = await connection.fetchval(
            """
            SELECT COUNT(*) FROM order_event_outbox
            WHERE delivered_at IS NULL AND attempts >= 8
            """
        )
        fresh_drivers = await connection.fetchval(
            "SELECT COUNT(*) FROM driver_locations WHERE received_at >= now() - interval '120 seconds'"
        )
        stale_drivers = await connection.fetchval(
            """
            SELECT COUNT(*) FROM users u
            LEFT JOIN driver_locations l ON l.driver_id=u.id
            WHERE u.driver_status='APPROVED'
              AND (l.received_at IS NULL OR l.received_at < now() - interval '120 seconds')
            """
        )

    pool_size = getattr(db.pool, "get_size", lambda: 0)()
    pool_idle_size = getattr(db.pool, "get_idle_size", lambda: 0)()
    pool_max_size = getattr(db.pool, "get_max_size", lambda: 0)()
    redis_available = 0
    try:
        if db.redis is not None:
            await db.redis.ping()
            redis_available = 1
    except Exception:
        # The metrics endpoint should remain scrapeable during Redis outages.
        redis_available = 0

    body = "\n".join([
        "# HELP vcar_order_event_outbox_pending Undelivered order status events.",
        "# TYPE vcar_order_event_outbox_pending gauge",
        f"vcar_order_event_outbox_pending {int(pending_outbox or 0)}",
        "# HELP vcar_order_event_outbox_oldest_age_seconds Age of the oldest undelivered order event.",
        "# TYPE vcar_order_event_outbox_oldest_age_seconds gauge",
        f"vcar_order_event_outbox_oldest_age_seconds {float(oldest_outbox_age or 0):.3f}",
        "# HELP vcar_order_event_outbox_ready Events ready for immediate delivery or reclaim.",
        "# TYPE vcar_order_event_outbox_ready gauge",
        f"vcar_order_event_outbox_ready {int(ready_outbox or 0)}",
        "# HELP vcar_order_event_outbox_claimed Events currently claimed by a dispatcher.",
        "# TYPE vcar_order_event_outbox_claimed gauge",
        f"vcar_order_event_outbox_claimed {int(claimed_outbox or 0)}",
        "# HELP vcar_order_event_outbox_expired_claims Claims older than the five-minute worker lease.",
        "# TYPE vcar_order_event_outbox_expired_claims gauge",
        f"vcar_order_event_outbox_expired_claims {int(expired_claims_outbox or 0)}",
        "# HELP vcar_order_event_outbox_high_retry Events with at least eight delivery attempts.",
        "# TYPE vcar_order_event_outbox_high_retry gauge",
        f"vcar_order_event_outbox_high_retry {int(high_retry_outbox or 0)}",
        "# HELP vcar_driver_locations_fresh Approved drivers with a GPS sample received in the last 120 seconds.",
        "# TYPE vcar_driver_locations_fresh gauge",
        f"vcar_driver_locations_fresh {int(fresh_drivers or 0)}",
        "# HELP vcar_driver_locations_stale Approved drivers without a GPS sample received in the last 120 seconds.",
        "# TYPE vcar_driver_locations_stale gauge",
        f"vcar_driver_locations_stale {int(stale_drivers or 0)}",
        "# HELP vcar_database_pool_connections Current asyncpg pool connections.",
        "# TYPE vcar_database_pool_connections gauge",
        f"vcar_database_pool_connections {int(pool_size)}",
        "# HELP vcar_database_pool_idle_connections Idle asyncpg pool connections.",
        "# TYPE vcar_database_pool_idle_connections gauge",
        f"vcar_database_pool_idle_connections {int(pool_idle_size)}",
        "# HELP vcar_database_pool_max_connections Configured asyncpg pool maximum size.",
        "# TYPE vcar_database_pool_max_connections gauge",
        f"vcar_database_pool_max_connections {int(pool_max_size)}",
        "# HELP vcar_redis_available Whether Redis responds to PING.",
        "# TYPE vcar_redis_available gauge",
        f"vcar_redis_available {redis_available}",
        "# HELP vcar_driver_presence_reconciliation_last_success_timestamp Unix timestamp of the last successful PostgreSQL to Redis presence repair.",
        "# TYPE vcar_driver_presence_reconciliation_last_success_timestamp gauge",
        f"vcar_driver_presence_reconciliation_last_success_timestamp {PRESENCE_RECONCILIATION_STATE['last_success_timestamp']:.3f}",
        "# HELP vcar_driver_presence_reconciliation_last_failure_timestamp Unix timestamp of the last failed presence repair.",
        "# TYPE vcar_driver_presence_reconciliation_last_failure_timestamp gauge",
        f"vcar_driver_presence_reconciliation_last_failure_timestamp {PRESENCE_RECONCILIATION_STATE['last_failure_timestamp']:.3f}",
        "# HELP vcar_driver_presence_reconciliation_consecutive_failures Consecutive failed presence repair cycles.",
        "# TYPE vcar_driver_presence_reconciliation_consecutive_failures gauge",
        f"vcar_driver_presence_reconciliation_consecutive_failures {PRESENCE_RECONCILIATION_STATE['consecutive_failures']}",
        "# HELP vcar_driver_presence_reconciliation_last_duration_seconds Duration of the last presence repair cycle.",
        "# TYPE vcar_driver_presence_reconciliation_last_duration_seconds gauge",
        f"vcar_driver_presence_reconciliation_last_duration_seconds {PRESENCE_RECONCILIATION_STATE['last_duration_seconds']:.6f}",
        "# HELP vcar_driver_presence_reconciliation_database_fresh Fresh database driver rows in the last successful repair.",
        "# TYPE vcar_driver_presence_reconciliation_database_fresh gauge",
        f"vcar_driver_presence_reconciliation_database_fresh {PRESENCE_RECONCILIATION_STATE['database_fresh']}",
        "# HELP vcar_driver_presence_reconciliation_repaired Driver projections repaired in the last successful cycle.",
        "# TYPE vcar_driver_presence_reconciliation_repaired gauge",
        f"vcar_driver_presence_reconciliation_repaired {PRESENCE_RECONCILIATION_STATE['repaired']}",
        "# HELP vcar_driver_presence_reconciliation_stale_removed Stale Redis presence members removed in the last successful cycle.",
        "# TYPE vcar_driver_presence_reconciliation_stale_removed gauge",
        f"vcar_driver_presence_reconciliation_stale_removed {PRESENCE_RECONCILIATION_STATE['stale_removed']}",
        "",
    ])
    return Response(content=body, media_type="text/plain; version=0.0.4; charset=utf-8")


@app.get("/health/live")
async def health_live():
    return {"status": "alive"}


@app.get("/health/ready")
async def health_ready():
    try:
        async with db.pool.acquire() as connection:
            await connection.fetchval("SELECT 1")
            postgis = await connection.fetchval("SELECT PostGIS_Version()")
        await db.redis.ping()
        return {"status": "ready", "database": "ok", "postgis": postgis, "redis": "ok"}
    except Exception:
        raise HTTPException(status_code=503, detail="Required dependencies are not ready")


@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if full_path.startswith("api/"): return {"error": "API Not Found"}
    file_path = os.path.join(static_dir, full_path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    # 强制禁止缓存 index.html
    response = FileResponse(os.path.join(static_dir, "index.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response
