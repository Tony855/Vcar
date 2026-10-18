import asyncio
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.db.core import db, get_user, create_order, get_order, cancel_order, get_user_orders
from app.services.pricing import calculate_fixed_price
from app.i18n.translator import translator

router = Router()
class PaxStates(StatesGroup): 
    pickup = State()
    dropoff = State()

def t(key, user, **vars): return translator.t(key, user.get("locale", "zh"), **vars)

@router.message(F.text.startswith("/start"))
async def cmd_start(msg: Message):
    user = await get_user(msg.from_user.id, msg.from_user.first_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚗 立即叫车", callback_data="menu:passenger")]])
    await msg.answer("Vcar 叫车系统\n请点击下方按钮：", reply_markup=kb)

@router.callback_query(F.data == "menu:passenger")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    user = await get_user(call.from_user.id)
    await state.set_state(PaxStates.pickup)
    await call.message.answer(t("passenger.prompt_pickup", user))
    await call.answer()

@router.message(PaxStates.pickup, F.location)
async def step1(msg: Message, state: FSMContext):
    user = await get_user(msg.from_user.id)
    await state.update_data(plat=msg.location.latitude, plng=msg.location.longitude)
    await state.set_state(PaxStates.dropoff)
    await msg.reply(t("passenger.prompt_dropoff", user))

@router.message(PaxStates.dropoff, F.location)
async def step2(msg: Message, state: FSMContext, driver_bot):
    user = await get_user(msg.from_user.id)
    data = await state.get_data()
    await state.clear()
    
    plat, plng = data['plat'], data['plng']
    dlat, dlng = msg.location.latitude, msg.location.longitude
    
    trip_dist, price_val = await calculate_fixed_price(plat, plng, dlat, dlng)
    price = f"{price_val:.2f}"
    
    order = await create_order(user['id'], plng, plat, dlng, dlat, trip_dist, price_val)
    oid = order['id']
    await db.redis.geoadd("order:pool", (plng, plat, oid))
    
    drivers = await db.redis.geosearch("driver:online", longitude=plng, latitude=plat, radius=20, unit="km", sort="ASC", withdist=True)
    
    if not drivers:
        return await msg.reply(t("passenger.no_driver", user, order_id=oid))
        
    for d_data in drivers:
        driver_id = int(d_data[0])
        pickup_dist = round(float(d_data[1]), 1)
        
        txt = t("order.card", user, order_id=oid, pickup_distance=pickup_dist, trip_distance=trip_dist, price=price)
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("order.accept_btn", user), callback_data=f"acc:{oid}")]])
        
        try:
            await driver_bot.send_venue(driver_id, latitude=plat, longitude=plng, title=t("common.pickup_venue", user), address=t("common.pickup_desc", user))
            await driver_bot.send_venue(driver_id, latitude=dlat, longitude=dlng, title=t("common.dropoff_venue", user), address=t("common.dropoff_desc", user))
            await driver_bot.send_message(driver_id, txt, reply_markup=kb, parse_mode="HTML")
        except Exception: 
            pass
    
    p_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("order.cancel_btn", user), callback_data=f"p_cancel:{oid}")]])
    await msg.reply(t("passenger.order_created", user, order_id=oid, distance=trip_dist, price=price), reply_markup=p_kb)

@router.callback_query(F.data.startswith("p_cancel:"))
async def p_cancel(call: CallbackQuery, driver_bot):
    user = await get_user(call.from_user.id)
    oid = int(call.data.split(":")[1])
    if await cancel_order(oid, user['id']):
        await db.redis.zrem("order:pool", str(oid))
        await call.message.edit_text(t("passenger.cancel_success", user))
        order = await get_order(oid)
        if order and order['d_tg']:
            try: await driver_bot.send_message(order['d_tg'], t("passenger.cancel_notice", user, order_id=oid))
            except: pass
    else: 
        await call.answer("无法取消", show_alert=True)
