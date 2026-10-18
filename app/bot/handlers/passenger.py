from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.order_repo import OrderRepository
from app.services.pricing import PricingService
from app.services.dispatch import DispatchService
from app.core.i18n import translator
import logging

router = Router()
class PaxStates(StatesGroup): pickup = State(); dropoff = State()
def t(key, user, **vars): return translator.t(key, user.get("locale", "zh"), **vars)

@router.message(F.text.startswith("/start"))
async def cmd_start(msg: Message):
    user = await UserRepository.get_or_create(msg.from_user.id, msg.from_user.first_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🚗 立即叫车", callback_data="menu:passenger")]])
    await msg.answer("Vcar 叫车系统\n请点击下方按钮：", reply_markup=kb)

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
    
    drivers = await DispatchService.get_nearby_drivers(data['plng'], data['plat'])
    if not drivers: return await msg.reply(t("passenger.no_driver", user, order_id=oid))
    
    for d in drivers:
        txt = t("order.card", user, order_id=oid, pickup_distance=round(float(d[1])*1.3,1), trip_distance=trip_dist, price=f"{price_val:.2f}")
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("order.accept_btn", user), callback_data=f"acc:{oid}")]])
        try:
            await driver_bot.send_venue(int(d[0]), latitude=data['plat'], longitude=data['plng'], title=t("common.pickup_venue", user), address=t("common.pickup_desc", user))
            await driver_bot.send_venue(int(d[0]), latitude=msg.location.latitude, longitude=msg.location.longitude, title=t("common.dropoff_venue", user), address=t("common.dropoff_desc", user))
            await driver_bot.send_message(int(d[0]), txt, reply_markup=kb, parse_mode="HTML")
        except: pass
        
    p_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("order.cancel_btn", user), callback_data=f"p_cancel:{oid}")]])
    await msg.reply(t("passenger.order_created", user, order_id=oid, distance=trip_dist, price=f"{price_val:.2f}"), reply_markup=p_kb)

@router.callback_query(F.data.startswith("p_cancel:"))
async def p_cancel(call: CallbackQuery, driver_bot):
    user = await UserRepository.get_or_create(call.from_user.id)
    oid = int(call.data.split(":")[1])
    order = await OrderRepository.get(oid)
    if not order: return await call.answer("订单不存在")
    
    fee = 0.0
    if order['status'] in ('ACCEPTED', 'ARRIVED'):
        fee = 1.0 # 司机已接单，取消扣违约金 $1
        
    if await OrderRepository.cancel_with_fee(oid, user['id'], 'PASSENGER', fee):
        await DispatchService.remove_order_from_pool(oid)
        if order['d_tg']:
            try: await driver_bot.send_message(order['d_tg'], f"⚠️ 乘客取消了订单 #{oid}" + (f"，您获得违约金 ${fee}" if fee > 0 else ""))
            except: pass
        await call.message.edit_text(f"✅ 订单已取消。" + (f"扣除违约金 ${fee}" if fee > 0 else ""))
    else:
        await call.answer("状态不允许取消", show_alert=True)

# ================= 新增：匿名聊天 & 位置转发 =================
@router.message(F.text | F.voice | F.photo)
async def pax_chat_proxy(msg: Message, driver_bot):
    if msg.text and msg.text.startswith("/"): return
    user = await UserRepository.get_or_create(msg.from_user.id)
    active_order = await OrderRepository.get_active_order_by_user(user['id'])
    if active_order and active_order.get('d_tg'):
        try:
            await driver_bot.send_message(active_order['d_tg'], f"💬 [来自乘客]:")
            await msg.copy_to(active_order['d_tg'])
        except: pass
