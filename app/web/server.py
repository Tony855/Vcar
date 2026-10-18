from fastapi import FastAPI, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import jwt, datetime, sys
sys.path.append("/root/vcar")
from app.config import settings
from app.db.core import db
app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"])
security = HTTPBearer()
class LoginData(BaseModel): username: str; password: str
class PriceUpdate(BaseModel): base_fare: float; per_km: float
@app.on_event("startup")
async def startup(): await db.init()
def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try: return jwt.decode(credentials.credentials, settings.secret_key, algorithms=["HS256"])
    except: raise HTTPException(status_code=401)
@app.post("/api/login")
async def login(data: LoginData):
    if data.username == "admin" and data.password == "vcar888":
        return {"token": jwt.encode({"sub": "admin", "exp": datetime.datetime.utcnow() + datetime.timedelta(hours=24)}, settings.secret_key, algorithm="HS256")}
    raise HTTPException(status_code=401, detail="密码错误")
@app.get("/api/stats", dependencies=[Depends(verify_token)])
async def get_stats():
    async with db.pool.acquire() as c:
        return {"total_orders": await c.fetchval("SELECT COUNT(*) FROM orders"), "active_orders": await c.fetchval("SELECT COUNT(*) FROM orders WHERE status IN ('DISPATCHING','ACCEPTED','IN_PROGRESS')"), "total_users": await c.fetchval("SELECT COUNT(*) FROM users"), "drivers_online": await db.redis.zcard("driver:online")}
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
@app.get("/", response_class=HTMLResponse)
async def dashboard(): return open("/root/vcar/app/web/templates/index.html", "r", encoding="utf-8").read()
