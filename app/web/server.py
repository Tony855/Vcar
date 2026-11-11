import os
import sys
import aiohttp
from fastapi import FastAPI, Depends, HTTPException, Response, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 动态获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(BASE_DIR)

from app.core.config import settings
from app.db.core import db
from app.core.security import verify_token, verify_driver_token, create_access_token, verify_password
from app.modules.routing.router import router as routing_router
from app.modules.auth.router import router as auth_router

app = FastAPI()
app.include_router(routing_router, prefix="/api/v1")
app.include_router(auth_router, prefix="/api")
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(settings.cors_origins),
    allow_credentials=bool(settings.cors_origins),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

class LoginData(BaseModel): username: str; password: str
class PriceUpdate(BaseModel): base_fare: float; per_km: float
class ReviewData(BaseModel): tg_id: int; status: str

@app.on_event("startup")
async def startup(): 
    await db.init()

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
        return {
            "total_orders": await c.fetchval("SELECT COUNT(*) FROM orders"),
            "active_orders": await c.fetchval("SELECT COUNT(*) FROM orders WHERE status IN ('DISPATCHING','ACCEPTED','IN_PROGRESS')"),
            "total_users": await c.fetchval("SELECT COUNT(*) FROM users"),
            "drivers_online": await c.fetchval("SELECT COUNT(*) FROM users WHERE driver_status='APPROVED'") # 简化统计
        }

@app.get("/api/orders", dependencies=[Depends(verify_token)])
async def get_admin_orders():
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT o.id, o.status, o.distance_km, o.price_estimate, o.driver_id, du.plate_number as driver_plate FROM orders o LEFT JOIN users du ON o.driver_id=du.id ORDER BY o.id DESC LIMIT 50")
        return [dict(r) for r in rows]

@app.get("/api/pricing")
async def get_pricing():
    async with db.pool.acquire() as c:
        # 只查起步价和每公里，防止把客服字符串转 float 报错
        rows = await c.fetch("SELECT key, value FROM settings WHERE key IN ('base_fare', 'per_km_rate')")
        data = {}
        for r in rows:
            try: data[r['key']] = float(r['value'])
            except: data[r['key']] = 0.0
        return {
            "base_fare": data.get('base_fare', 1.0),
            "per_km": data.get('per_km_rate', 0.5)
        }

@app.post("/api/pricing", dependencies=[Depends(verify_token)])
async def set_pricing(price: PriceUpdate):
    async with db.pool.acquire() as c:
        await c.execute("UPDATE settings SET value=$1 WHERE key='base_fare'", str(price.base_fare))
        await c.execute("UPDATE settings SET value=$1 WHERE key='per_km_rate'", str(price.per_km))
    return {"status": "ok"}

@app.get("/api/config/support")
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
        is_online = await db.redis.zscore("driver:online", str(tg_id))
        real_status = "在线" if is_online is not None else "离线"
        
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
