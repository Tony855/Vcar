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
        return [dict(r) for r in await c.fetch("SELECT o.id, o.status, o.distance_km, o.price_estimate, COALESCE(cu.username, cu.telegram_id::text) as passenger_name, COALESCE(du.username, du.telegram_id::text) as driver_name, du.plate_number as driver_plate FROM orders o LEFT JOIN users cu ON o.customer_id=cu.id LEFT JOIN users du ON o.driver_id=du.id ORDER BY o.id DESC LIMIT 50")]

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

# ================= 司机小程序独立模块开始 =================
from fastapi.responses import HTMLResponse





@app.get("/api/driver/{tg_id}/reviews")
async def get_driver_reviews(tg_id: int):
    async with db.pool.acquire() as c:
        driver = await c.fetchrow("SELECT id FROM users WHERE telegram_id=$1", tg_id)
        if not driver: return []
        rows = await c.fetch("SELECT rating, comment, created_at FROM reviews WHERE driver_id=$1 ORDER BY created_at DESC LIMIT 10", driver['id'])
        return [dict(r) for r in rows]


@app.get("/api/driver/{tg_id}/profile")
async def get_driver_profile(tg_id: int):
    async with db.pool.acquire() as c:
        driver = await c.fetchrow("SELECT id, rating, plate_number FROM users WHERE telegram_id=$1", tg_id)
        if not driver: return {"error": "Driver not found"}
        
        # 统计数据和总收入
        stats = await c.fetchrow("SELECT COALESCE(AVG(rating), 5.0) as avg_r, COUNT(id) as total_r FROM reviews WHERE driver_id=$1", driver['id'])
        order_stats = await c.fetchrow("SELECT COUNT(*) as total_orders, COALESCE(SUM(price_estimate), 0) as total_income FROM orders WHERE driver_id=$1 AND status='COMPLETED'", driver['id'])
        
        return {
            "plate": driver['plate_number'] or "未绑定车牌",
            "rating": round(float(stats['avg_r']), 1),
            "total_reviews": stats['total_r'],
            "completed_orders": order_stats['total_orders'],
            "total_income": round(float(order_stats['total_income']), 2)
        }


@app.get("/profile/{tg_id}", response_class=HTMLResponse)
async def profile_page(tg_id: int):
    html = """
    <!DOCTYPE html>
    <html lang="zh">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
        <title>Vcar 司机中心</title>
        <script src="https://telegram.org/js/telegram-web-app.js"></script>
        <script src="https://cdn.tailwindcss.com"></script>
        <style>
            body { background-color: #f8fafc; font-family: -apple-system, sans-serif; }
            .glass-card { background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); }
            /* 按钮按下效果 */
            .menu-btn:active { transform: scale(0.95); transition: transform 0.1s; }
        </style>
    </head>
    <body class="pb-20">
        <div class="h-40 bg-gradient-to-br from-blue-500 to-indigo-600 rounded-b-[40px] shadow-lg absolute w-full top-0 z-0"></div>

        <div class="relative z-10 px-4 pt-10">
            <div class="glass-card rounded-3xl p-6 shadow-xl border border-white/20 flex items-center space-x-4">
                <div class="w-20 h-20 bg-blue-100 rounded-full flex items-center justify-center text-4xl shadow-inner border-4 border-white">🚗</div>
                <div class="flex-1">
                    <div class="flex items-center space-x-2">
                        <h2 class="text-2xl font-black text-gray-800" id="plate">加载中...</h2>
                        <span class="px-2 py-1 bg-green-100 text-green-700 text-xs font-bold rounded-lg border border-green-200" id="status-tag">在线</span>
                    </div>
                    <div class="flex items-center text-yellow-500 font-bold mt-1 text-sm">
                        <span id="rating">⭐ 5.0</span>
                        <span class="text-gray-400 font-normal ml-2">(<span id="reviews">0</span> 条评价)</span>
                    </div>
                </div>
            </div>

            <div class="grid grid-cols-2 gap-4 mt-6">
                <div class="bg-white rounded-2xl p-4 shadow-sm border border-gray-100">
                    <div class="text-gray-500 text-xs font-bold mb-1">累计收入</div>
                    <div class="text-2xl font-black text-gray-800">$ <span id="income">0.00</span></div>
                </div>
                <div class="bg-white rounded-2xl p-4 shadow-sm border border-gray-100">
                    <div class="text-gray-500 text-xs font-bold mb-1">完成单量</div>
                    <div class="text-2xl font-black text-gray-800"><span id="orders">0</span> 单</div>
                </div>
            </div>

            <!-- 快捷功能 (加上了 onclick 事件) -->
            <div class="grid grid-cols-4 gap-2 mt-6">
                <div onclick="showToast('正在开发中...')" class="menu-btn cursor-pointer flex flex-col items-center justify-center bg-white rounded-2xl py-3 shadow-sm">
                    <span class="text-2xl mb-1">📋</span>
                    <span class="text-[10px] text-gray-600 font-bold">我的订单</span>
                </div>
                <div onclick="showToast('正在开发中...')" class="menu-btn cursor-pointer flex flex-col items-center justify-center bg-white rounded-2xl py-3 shadow-sm">
                    <span class="text-2xl mb-1">💰</span>
                    <span class="text-[10px] text-gray-600 font-bold">收入明细</span>
                </div>
                <div onclick="showToast('正在开发中...')" class="menu-btn cursor-pointer flex flex-col items-center justify-center bg-white rounded-2xl py-3 shadow-sm">
                    <span class="text-2xl mb-1">⚙️</span>
                    <span class="text-[10px] text-gray-600 font-bold">账号设置</span>
                </div>
                <div onclick="contactSupport()" class="menu-btn cursor-pointer flex flex-col items-center justify-center bg-white rounded-2xl py-3 shadow-sm">
                    <span class="text-2xl mb-1">🎧</span>
                    <span class="text-[10px] text-gray-600 font-bold">联系客服</span>
                </div>
            </div>

            <div class="mt-8">
                <div class="flex justify-between items-end mb-3">
                    <h3 class="text-lg font-black text-gray-800">📝 乘客评价</h3>
                    <!-- 点击查看全部 -->
                    <span onclick="showToast('正在加载全部评价...')" class="cursor-pointer text-xs text-blue-500 font-bold">查看全部 ></span>
                </div>
                <div id="review-list" class="space-y-3">
                    <div class="text-center text-gray-400 py-4 text-sm">正在加载评价...</div>
                </div>
            </div>
        </div>

        <script>
            const tgId = TG_ID_PLACEHOLDER;
            const tg = window.Telegram?.WebApp;
            
            if (tg) {
                tg.ready();
                tg.expand();
            }

            // 弹窗提示功能（调用 TG 原生弹窗）
            function showToast(msg) {
                if (tg && tg.showAlert) {
                    tg.showAlert(msg);
                } else {
                    alert(msg);
                }
            }

            // 联系客服功能（直接拉起 TG 会话）
            function contactSupport() {
                if (tg) {
                    tg.showPopup({
                        title: '联系客服',
                        message: '是否联系 Vcar 客服？',
                        buttons: [
                            {id: 'cancel', type: 'cancel'},
                            {id: 'ok', type: 'default', text: '联系'}
                        ]
                    }, function(buttonId) {
                        if (buttonId === 'ok') {
                            tg.openTelegramLink("https://t.me/你的客服TG账号");
                        }
                    });
                }
            }

            async function loadData() {
                try {
                    const infoRes = await fetch('/api/driver/' + tgId + '/profile');
                    const info = await infoRes.json();
                    document.getElementById('plate').innerText = info.plate;
                    document.getElementById('rating').innerText = '⭐ ' + info.rating;
                    document.getElementById('orders').innerText = info.completed_orders;
                    document.getElementById('income').innerText = info.total_income;
                    document.getElementById('reviews').innerText = info.total_reviews;

                    const revRes = await fetch('/api/driver/' + tgId + '/reviews');
                    const reviews = await revRes.json();
                    const list = document.getElementById('review-list');
                    
                    const displayReviews = reviews.length > 0 ? reviews : [
                        { rating: 5, comment: '司机师傅非常准时，车开得很稳，服务态度超级好！', date: '今天' },
                        { rating: 5, comment: '车内很干净，没有异味，下次还坐您的车。', date: '昨天' }
                    ];

                    let htmlStr = '';
                    for (let r of displayReviews) {
                        let stars = '⭐'.repeat(r.rating);
                        let comment = r.comment || '该乘客没有留下文字评价';
                        htmlStr += '<div class="bg-white p-4 rounded-2xl shadow-sm border border-gray-50">';
                        htmlStr += '<div class="flex justify-between items-center mb-2"><span class="font-bold text-gray-700 text-sm">匿名乘客</span><span class="text-yellow-500 text-xs">' + stars + '</span></div>';
                        htmlStr += '<p class="text-gray-600 text-sm leading-relaxed">' + comment + '</p></div>';
                    }
                    list.innerHTML = htmlStr;
                } catch (e) {
                    console.error(e);
                }
            }
            loadData();
        </script>
    </body>
    </html>
    """
    return html.replace("TG_ID_PLACEHOLDER", str(tg_id))

# ================= 前端 SPA 路由配置 =================
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
static_dir = os.path.join(BASE_DIR, "app", "web", "static")

# 1. 挂载静态资源目录
if os.path.exists(os.path.join(static_dir, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(static_dir, "assets")), name="assets")

# 2. 挂载后台管理页面
app.mount("/admin", StaticFiles(directory=static_dir, html=True), name="admin")

# 3. 处理小程序路由及所有前端路由回退


# ================= 司机订单 API =================

@app.get("/api/driver/{tg_id}/orders")
async def get_driver_orders(tg_id: int):
    async with db.pool.acquire() as c:
        driver = await c.fetchrow("SELECT id FROM users WHERE telegram_id=$1", tg_id)
        if not driver: return []
        # 移除 created_at，只查存在的字段
        orders = await c.fetch("""
            SELECT id, status, price_estimate as price
            FROM orders 
            WHERE driver_id=$1 
            ORDER BY id DESC LIMIT 20
        """, driver['id'])
        
        res = []
        for o in orders:
            d = dict(o)
            d['time_str'] = f"订单号: #{d['id']}"
            res.append(d)
        return res

@app.get("/{full_path:path}")
async def serve_spa(full_path: str):
    if full_path.startswith("api/"):
        return {"error": "API Not Found"}
    file_path = os.path.join(static_dir, full_path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    return FileResponse(os.path.join(static_dir, "index.html"))
