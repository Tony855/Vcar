from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.order_repo import OrderRepository
from app.services.dispatch import DispatchService
from app.services.pricing import PricingService
from app.core.enums import DriverStatus, OrderStatus
from app.core.i18n import translator
from app.db.core import db
import logging

router = Router()
class DrvStates(StatesGroup): 
    location = State(); reg_phone = State(); reg_plate = State(); reg_car_photo = State(); reg_license_photo = State(); reg_drv_license_photo = State()
def t(key, user, **vars): return translator.t(key, user.get("locale", "zh"), **vars)

@router.message(F.text.startswith("/start"))
async def cmd_start(msg: Message):
    user = await UserRepository.get_or_create(msg.from_user.id, msg.from_user.first_name)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🧑‍✈️ 司机上线接单", callback_data="menu:driver")]])
    await msg.answer("Vcar 司机端系统", reply_markup=kb)

@router.callback_query(F.data == "menu:driver")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    user = await UserRepository.get_or_create(call.from_user.id)
    await call.answer()
    status = user.get('driver_status')
    if status == DriverStatus.PENDING.value: return await call.message.answer("⏳ 审核中。")
    if status == DriverStatus.APPROVED.value:
        await state.set_state(DrvStates.location)
        return await call.message.answer(t("driver.prompt_location", user))
    await state.set_state(DrvStates.reg_phone)
    await call.message.answer("📝 步骤 1/5：请输入【手机号码】：")

@router.message(DrvStates.reg_phone)
async def s1(msg: Message, state: FSMContext):
    await state.update_data(phone=msg.text); await state.set_state(DrvStates.reg_plate); await msg.reply("📝 步骤 2/5：请输入【车牌号码】：")
@router.message(DrvStates.reg_plate)
async def s2(msg: Message, state: FSMContext):
    await state.update_data(plate=msg.text); await state.set_state(DrvStates.reg_car_photo); await msg.reply("📸 步骤 3/5：请上传【汽车实拍图】：")
@router.message(DrvStates.reg_car_photo, F.photo)
async def s3(msg: Message, state: FSMContext):
    await state.update_data(car_pic=msg.photo[-1].file_id); await state.set_state(DrvStates.reg_license_photo); await msg.reply("📸 步骤 4/5：请上传【行驶证】：")
@router.message(DrvStates.reg_license_photo, F.photo)
async def s4(msg: Message, state: FSMContext):
    await state.update_data(lic_pic=msg.photo[-1].file_id); await state.set_state(DrvStates.reg_drv_license_photo); await msg.reply("📸 步骤 5/5：请上传【驾驶证】：")
@router.message(DrvStates.reg_drv_license_photo, F.photo)
async def s5(msg: Message, state: FSMContext):
    data = await state.get_data()
    await UserRepository.update_driver_info(msg.from_user.id, data['phone'], data['plate'], data['car_pic'], data['lic_pic'], msg.photo[-1].file_id)
    await state.clear(); await msg.reply("✅ 资料已提交，请等待审核。")

@router.message(DrvStates.location, F.location)
async def go_online(msg: Message, state: FSMContext):
    user = await UserRepository.get_or_create(msg.from_user.id)
    await state.clear(); await DispatchService.go_online(msg.from_user.id, msg.location.longitude, msg.location.latitude); await msg.reply(t("driver.online_success", user))

@router.callback_query(F.data.startswith("acc:"))
async def accept(call: CallbackQuery, passenger_bot):
    user = await UserRepository.get_or_create(call.from_user.id)
    if user.get('driver_status') != DriverStatus.APPROVED.value: return await call.answer("⚠️ 尚未审核通过", show_alert=True)
    oid = int(call.data.split(":")[1])
    if not await OrderRepository.accept(oid, user['id']): return await call.answer("手慢了", show_alert=True)
    await DispatchService.remove_order_from_pool(oid)
    
    # 司机操作面板加入“发送位置”
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📍 发送实时位置给乘客", callback_data=f"loc:{oid}")],
        [InlineKeyboardButton(text=t("driver.arrived_btn", user), callback_data=f"arr:{oid}")]
    ])
    await call.message.edit_text(t("driver.accept_success", user, order_id=oid), reply_markup=kb)
    
    order = await OrderRepository.get(oid)
    if order and order.get('c_tg'):
        d_pos = await db.redis.geopos("driver:online", str(call.from_user.id))
        d_km = round(PricingService.haversine(order['pickup_lat'], order['pickup_lng'], d_pos[0][1], d_pos[0][0]) * 1.3, 1) if d_pos and d_pos[0] else 0.0
        p_txt = t("passenger.driver_accepted", user, plate=order.get('d_plate') or '无', name=order.get('d_name') or '司机', phone=order.get('d_phone') or '无', dist=d_km)
        try:
            await passenger_bot.send_message(order['c_tg'], p_txt)
            if order.get('d_car_photo'): await passenger_bot.send_photo(order['c_tg'], photo=order['d_car_photo'], caption="📸 车辆实拍图")
        except Exception as e: logging.error(e)

# ================= 新增：发送位置指令 =================
@router.callback_query(F.data.startswith("loc:"))
async def send_location(call: CallbackQuery, state: FSMContext):
    oid = int(call.data.split(":")[1])
    await call.message.answer("👇 请点击输入框左侧的【📎 附件】-> 选择【位置】，将当前位置发给乘客。")
    
@router.message(F.location)
async def handle_driver_location_update(msg: Message, passenger_bot):
    user = await UserRepository.get_or_create(msg.from_user.id)
    # 更新 Redis 位置
    await DispatchService.go_online(msg.from_user.id, msg.location.longitude, msg.location.latitude)
    # 查活跃订单
    active = await OrderRepository.get_active_order_by_user(user['id'])
    if active and active.get('c_tg'):
        await passenger_bot.send_location(active['c_tg'], latitude=msg.location.latitude, longitude=msg.location.longitude)
        await passenger_bot.send_message(active['c_tg'], "🚗 司机更新了实时位置。")

@router.callback_query(F.data.startswith("arr:"))
async def arrived(call: CallbackQuery, passenger_bot):
    user = await UserRepository.get_or_create(call.from_user.id); oid = int(call.data.split(":")[1])
    if await OrderRepository.update_status(oid, OrderStatus.ARRIVED.value, OrderStatus.ACCEPTED.value):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("driver.start_btn", user), callback_data=f"sta:{oid}")]])
        await call.message.edit_text(t("driver.arrived_msg", user), reply_markup=kb)
        order = await OrderRepository.get(oid)
        if order and order.get('c_tg'):
            try: await passenger_bot.send_message(order['c_tg'], t("passenger.driver_arrived", user))
            except: pass

@router.callback_query(F.data.startswith("sta:"))
async def start(call: CallbackQuery, passenger_bot):
    user = await UserRepository.get_or_create(call.from_user.id); oid = int(call.data.split(":")[1])
    if await OrderRepository.update_status(oid, OrderStatus.IN_PROGRESS.value, OrderStatus.ARRIVED.value):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("driver.finish_btn", user), callback_data=f"fin:{oid}")]])
        await call.message.edit_text(t("driver.start_msg", user), reply_markup=kb)
        order = await OrderRepository.get(oid)
        if order and order.get('c_tg'):
            try: await passenger_bot.send_message(order['c_tg'], t("passenger.trip_started", user))
            except: pass

@router.callback_query(F.data.startswith("fin:"))
async def finish(call: CallbackQuery, passenger_bot):
    user = await UserRepository.get_or_create(call.from_user.id); oid = int(call.data.split(":")[1])
    if await OrderRepository.update_status(oid, OrderStatus.COMPLETED.value, OrderStatus.IN_PROGRESS.value):
        await call.message.edit_text(t("driver.finish_msg", user, order_id=oid))
        order = await OrderRepository.get(oid)
        if order and order.get('c_tg'):
            price_str = f"{float(order['price_estimate']):.2f}"
            try: await passenger_bot.send_message(order['c_tg'], t("passenger.trip_completed", user, price=price_str))
            except: pass

# ================= 新增：司机聊天代理 =================
@router.message(F.text | F.voice | F.photo)
async def drv_chat_proxy(msg: Message, passenger_bot):
    if msg.text and msg.text.startswith("/"): return
    user = await UserRepository.get_or_create(msg.from_user.id)
    active_order = await OrderRepository.get_active_order_by_user(user['id'])
    if active_order and active_order.get('c_tg'):
        try:
            await passenger_bot.send_message(active_order['c_tg'], f"💬 [来自司机]:")
            await msg.copy_to(active_order['c_tg'])
        except: pass
