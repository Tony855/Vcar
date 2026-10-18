from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.db.core import db
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.order_repo import OrderRepository
from app.services.pricing import PricingService
from app.services.dispatch import DispatchService
from app.core.i18n import translator
import logging

router = Router()
class PaxStates(StatesGroup): pickup = State(); dropoff = State()
def t(key, user, **vars): return translator.t(key, user.get("locale", "zh"), **vars)

async def send_main_menu(msg: Message, user):
    items = [
        [InlineKeyboardButton(text=t("menu.passenger", user), callback_data="menu:passenger")],
        [InlineKeyboardButton(text=t("menu.orders", user), callback_data="menu:orders"), InlineKeyboardButton(text=t("menu.lang", user), callback_data="menu:lang")]
    ]
    kb = InlineKeyboardMarkup(inline_keyboard=items)
    await msg.answer(f"<b>{t('menu.title', user)}</b>", reply_markup=kb)

@router.message(F.text.startswith("/start"))
async def cmd_start(msg: Message):
    user = await UserRepository.get_or_create(msg.from_user.id, msg.from_user.first_name)
    await send_main_menu(msg, user)

@router.callback_query(F.data == "menu:orders")
async def menu_orders(call: CallbackQuery):
    user = await UserRepository.get_or_create(call.from_user.id)
    await call.answer("正在查询订单...") # 给用户即时反馈
    try:
        orders = await OrderRepository.get_customer_orders(user['id'])
        if not orders:
            return await call.message.answer(t("common.no_orders", user))
        
        txt = "\n".join([t("order.list_item", user, id=o["id"], status=t(f"status.{o['status']}", user), price=f"{float(o['price_estimate']):.2f}") for o in orders])
        await call.message.answer(f"📋 <b>历史订单 (最近5条)</b>\n\n{txt}")
    except Exception as e:
        logging.error(f"查询订单失败: {e}")
        await call.message.answer("❌ 查询订单出错，请稍后再试。")

@router.callback_query(F.data == "menu:lang")
async def menu_lang(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇨🇳 中文", callback_data="lang:zh"), InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en")],
        [InlineKeyboardButton(text="🇰🇭 ខ្មែរ", callback_data="lang:km")]
    ])
    await call.message.edit_text("请选择语言 / Choose language / សូមជ្រើសរើសភាសា", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("lang:"))
async def set_lang(call: CallbackQuery):
    loc = call.data.split(":")[1]
    async with db.pool.acquire() as c:
        await c.execute("UPDATE users SET locale=$1 WHERE telegram_id=$2", loc, call.from_user.id)
    await call.answer() # 静默关闭按钮 loading，无弹窗
    
    # 切换后直接刷新主菜单
    user = await UserRepository.get_or_create(call.from_user.id)
    await call.message.delete() # 删掉旧的选语言框
    await send_main_menu(call.message, user)

@router.callback_query(F.data == "menu:passenger")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    user = await UserRepository.get_or_create(call.from_user.id)
    await state.set_state(PaxStates.pickup)
    await call.message.answer(t("passenger.prompt_pickup", user))
    await call.answer()

@router.message(PaxStates.pickup, F.location)
async def step1(msg: Message, state: FSMContext):
    user = await UserRepository.get_or_create(msg.from_user.id)
    await state.update_data(plat=msg.location.latitude, plng=msg.location.longitude)
    await state.set_state(PaxStates.dropoff)
    await msg.reply(t("passenger.prompt_dropoff", user))

@router.message(PaxStates.dropoff, F.location)
async def step2(msg: Message, state: FSMContext, driver_bot):
    user = await UserRepository.get_or_create(msg.from_user.id)
    data = await state.get_data(); await state.clear()
    trip_dist, price_val = await PricingService.estimate(data['plat'], data['plng'], msg.location.latitude, msg.location.longitude)
    order = await OrderRepository.create(user['id'], data['plng'], data['plat'], msg.location.longitude, msg.location.latitude, trip_dist, price_val)
    oid = order['id']
    
    drivers = await DispatchService.smart_dispatch(data['plng'], data['plat'], top_n=3)
    if not drivers: 
        # 为了方便测试，把 Redis 里的真实情况告诉乘客
        raw = await DispatchService.get_nearby_drivers(data['plng'], data['plat'], radius_km=100)
        await msg.reply(f"⚠️ 派单失败。100km内实际有 {len(raw)} 位司机上线。请查看服务器后台日志排查原因。")
        return
    
    for d in drivers:
        txt = t("order.card", user, order_id=oid, pickup_distance=round(float(d["dist"])*1.3,1), trip_distance=trip_dist, price=f"{price_val:.2f}")
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("order.accept_btn", user), callback_data=f"acc:{oid}")]])
        try:
            await driver_bot.send_venue(d["tg_id"], latitude=data['plat'], longitude=data['plng'], title=t("common.pickup_venue", user), address=t("common.pickup_desc", user))
            await driver_bot.send_venue(d["tg_id"], latitude=msg.location.latitude, longitude=msg.location.longitude, title=t("common.dropoff_venue", user), address=t("common.dropoff_desc", user))
            await driver_bot.send_message(d["tg_id"], txt, reply_markup=kb, parse_mode="HTML")
        except: pass
        
    p_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("order.cancel_btn", user), callback_data=f"p_cancel:{oid}")]])
    await msg.reply(t("passenger.order_created", user, order_id=oid, distance=trip_dist, price=f"{price_val:.2f}"), reply_markup=p_kb)

@router.callback_query(F.data.startswith("p_cancel:"))
async def p_cancel(call: CallbackQuery, driver_bot):
    user = await UserRepository.get_or_create(call.from_user.id)
    oid = int(call.data.split(":")[1])
    order = await OrderRepository.get(oid)
    if not order: return await call.answer("订单不存在")
    fee = 1.0 if order['status'] in ('ACCEPTED', 'ARRIVED') else 0.0
    if await OrderRepository.cancel_with_fee(oid, user['id'], 'PASSENGER', fee):
        await DispatchService.remove_order_from_pool(oid)
        if order.get('d_tg'):
            try: await driver_bot.send_message(order['d_tg'], t("driver.passenger_cancelled_fee" if fee>0 else "driver.passenger_cancelled", user, order_id=oid, fee=fee))
            except: pass
        await call.message.edit_text(t("passenger.cancel_with_fee" if fee>0 else "passenger.cancel_free", user, fee=fee))
    else: await call.answer("状态不允许取消", show_alert=True)

@router.message(F.text | F.voice | F.photo)
async def pax_chat_proxy(msg: Message, driver_bot):
    if msg.text and msg.text.startswith("/"): return
    user = await UserRepository.get_or_create(msg.from_user.id)
    active_order = await OrderRepository.get_active_order_by_user(user['id'])
    if active_order and active_order.get('d_tg'):
        try:
            await driver_bot.send_message(active_order['d_tg'], t("common.from_passenger", user))
            await msg.copy_to(active_order['d_tg'])
        except: pass

@router.callback_query(F.data.startswith("rate_pax:"))
async def rate_pax_to_driver(call: CallbackQuery, driver_bot):
    _, oid, score = call.data.split(":")
    order = await OrderRepository.get(int(oid))
    if order and order.get('d_tg'):
        await UserRepository.update_user_rating(order['d_tg'], int(score))
        await call.message.edit_text(f"✅ 感谢您的评价！您给司机打了 {int(score)} 星。")
        try:
            await driver_bot.send_message(order['d_tg'], f"⭐ 乘客已为您打分：{int(score)} 星。")
        except: pass
    await call.answer()
