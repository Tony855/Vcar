import os
import sys
import jwt
import datetime
import aiohttp
from fastapi import FastAPI, Depends, HTTPException, Response, Query
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel

# 动态获取项目根目录
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.append(BASE_DIR)

from app.core.config import settings
from app.db.core import db

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])
security = HTTPBearer()

class LoginData(BaseModel): username: str; password: str
class PriceUpdate(BaseModel): base_fare: float; per_km: float
class ReviewData(BaseModel): tg_id: int; status: str

@app.on_event("startup")
async def startup(): 
    await db.init()

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try: return jwt.decode(credentials.credentials, settings.secret_key, algorithms=["HS256"])
    except: raise HTTPException(status_code=401)

@app.post("/api/login")
async def login(data: LoginData):
    if data.username == "admin" and data.password == "vcar888":
        return {"token": jwt.encode({"sub": "admin", "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24)}, settings.secret_key, algorithm="HS256")}
    raise HTTPException(status_code=401, detail="密码错误")

@app.get("/api/drivers/all", dependencies=[Depends(verify_token)])
async def all_drivers(page: int = Query(1, ge=1), page_size: int = Query(10, ge=1), keyword: str = Query("")):
    offset = (page - 1) * page_size
    async with db.pool.acquire() as c:
        where_clause = "WHERE driver_status IS NOT NULL AND driver_status != 'NONE'"
        args = []
        if keyword:
            where_clause += " AND (phone ILIKE $1 OR plate_number ILIKE $1)"
            args.append(f"%{keyword}%")
        total = await c.fetchval(f"SELECT COUNT(*) FROM users {where_clause}", *args)
        query = f"SELECT telegram_id, first_name, username, phone, plate_number, car_photo, license_photo, driving_license_photo, driver_status FROM users {where_clause} ORDER BY id DESC LIMIT ${len(args)+1} OFFSET ${len(args)+2}"
        args.extend([page_size, offset])
        rows = await c.fetch(query, *args)
        return {"total": total, "items": [dict(r) for r in rows]}

@app.get("/api/drivers/pending", dependencies=[Depends(verify_token)])
async def pending_drivers():
    async with db.pool.acquire() as c:
        rows = await c.fetch("SELECT telegram_id, first_name, username, phone, plate_number, car_photo, license_photo, driving_license_photo FROM users WHERE driver_status='PENDING'")
        return [dict(r) for r in rows]

@app.post("/api/drivers/review", dependencies=[Depends(verify_token)])
async def review_driver(data: ReviewData):
    # 1. 更新状态
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET driver_status=$1, updated_at=now() WHERE telegram_id=$2", data.status, data.tg_id)
        # 查一下司机的语言偏好
        user_locale = await c.fetchval("SELECT locale FROM users WHERE telegram_id=$1", data.tg_id)
    
    # 2. 发送多语言通知
    if data.status in ("APPROVED", "REJECTED"):
        if data.status == "APPROVED":
            msg = "🎉 <b>恭喜！您的司机入驻资料已审核通过。</b>\n\n您现在可以点击 /start，选择『司机上线接单』开始赚钱了！"
            if user_locale == 'en': msg = "🎉 <b>Congratulations! Your driver profile has been approved.</b>\n\nYou can now click /start and select 'Driver Go Online' to start earning!"
            if user_locale == 'km': msg = "🎉 <b>សូមអបអរសាទរ! គណនីអ្នកបើកបររបស់អ្នកត្រូវបានអនុម័ត។</b>\n\nឥឡូវអ្នកអាចចុច /start ហើយជ្រើសរើស 'ចូលធ្វើការ' ដើម្បីចាប់ផ្តើមរកប្រាក់។"
        else:
            msg = "❌ <b>抱歉，您的司机资料未通过审核。</b>\n\n请点击 /start 重新提交。"
            if user_locale == 'en': msg = "❌ <b>Sorry, your driver profile was rejected.</b>\n\nPlease click /start to resubmit."
            if user_locale == 'km': msg = "❌ <b>សូមអភ័យទោស ពាក្យសុំរបស់អ្នកត្រូវបានបដិសេធ។</b>\n\nសូមចុច /start ដើម្បីដាក់ពាក្យម្តងទៀត។"
            
        url = f"https://api.telegram.org/bot{settings.driver_bot_token}/sendMessage"
        payload = {"chat_id": data.tg_id, "text": msg, "parse_mode": "HTML"}
        
        async with aiohttp.ClientSession() as session:
            try: await session.post(url, json=payload)
            except: pass
                
    return {"status": "ok"}

@app.get("/api/file/{file_id}")
async def get_tg_file(file_id: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(f"https://api.telegram.org/bot{settings.driver_bot_token}/getFile?file_id={file_id}") as resp:
            data = await resp.json()
            if not data.get("ok"): raise HTTPException(status_code=404)
            file_path = data["result"]["file_path"]
        async with session.get(f"https://api.telegram.org/file/bot{settings.driver_bot_token}/{file_path}") as resp:
            return Response(content=await resp.read(), media_type="image/jpeg")

@app.get("/api/stats", dependencies=[Depends(verify_token)])
async def get_stats():
    async with db.pool.acquire() as c:
        return {
            "total_orders": await c.fetchval("SELECT COUNT(*) FROM orders"), 
            "active_orders": await c.fetchval("SELECT COUNT(*) FROM orders WHERE status IN ('DISPATCHING','ACCEPTED','IN_PROGRESS')"), 
            "total_users": await c.fetchval("SELECT COUNT(*) FROM users"), 
            "drivers_online": await db.redis.zcard("driver:online")
        }

@app.get("/api/orders", dependencies=[Depends(verify_token)])
async def get_orders():
    async with db.pool.acquire() as c:
        return [dict(r) for r in await c.fetch("SELECT o.id, o.status, o.distance_km, o.price_estimate, cu.first_name as passenger_name, du.first_name as driver_name FROM orders o LEFT JOIN users cu ON o.customer_id=cu.id LEFT JOIN users du ON o.driver_id=du.id ORDER BY o.id DESC LIMIT 50")]

@app.get("/api/pricing", dependencies=[Depends(verify_token)])
async def get_pricing():
    async with db.pool.acquire() as c:
        data = {r['key']: float(r['value']) for r in await c.fetch("SELECT key, value FROM settings")}
        return {"base_fare": data.get('base_fare', 1.0), "per_km": data.get('per_km_rate', 0.5)}

@app.post("/api/pricing", dependencies=[Depends(verify_token)])
async def set_pricing(price: PriceUpdate):
    async with db.pool.acquire() as c:
        await c.execute("UPDATE settings SET value=$1 WHERE key='base_fare'", price.base_fare)
        await c.execute("UPDATE settings SET value=$1 WHERE key='per_km_rate'", price.per_km)
    return {"status": "ok"}

# 挂载静态文件
static_dir = os.path.join(BASE_DIR, "app", "web", "static")
os.makedirs(static_dir, exist_ok=True) # 确保目录存在
app.mount("/admin", StaticFiles(directory=static_dir, html=True), name="static")

@app.get("/")
async def root():
    index_file = os.path.join(static_dir, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return HTMLResponse("<h1>Vcar Web 正在初始化，请稍后...</h1>")
