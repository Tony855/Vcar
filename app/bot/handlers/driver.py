from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.db.repositories.user_repo import UserRepository
from app.db.repositories.order_repo import OrderRepository
from app.services.dispatch import DispatchService
from app.services.pricing import PricingService
from app.core.enums import DriverStatus
from app.core.i18n import translator
from app.db.core import db

router = Router()
class DrvStates(StatesGroup): location = State()
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
    if user.get('driver_status') != DriverStatus.APPROVED.value:
        return await call.message.answer("⚠️ 您尚未通过审核，请先在 Bot 提交资料或在后台通过审核。")
    await state.set_state(DrvStates.location)
    await call.message.answer(t("driver.prompt_location", user))

@router.message(DrvStates.location, F.location)
async def go_online(msg: Message, state: FSMContext):
    user = await UserRepository.get_or_create(msg.from_user.id)
    await state.clear()
    await DispatchService.go_online(msg.from_user.id, msg.location.longitude, msg.location.latitude)
    await msg.reply(t("driver.online_success", user))

@router.callback_query(F.data.startswith("acc:"))
async def accept(call: CallbackQuery, passenger_bot):
    user = await UserRepository.get_or_create(call.from_user.id)
    if user.get('driver_status') != DriverStatus.APPROVED.value:
        return await call.answer("⚠️ 您的资料尚未审核通过", show_alert=True)
    
    oid = int(call.data.split(":")[1])
    if not await OrderRepository.accept(oid, user['id']): return await call.answer("手慢了", show_alert=True)
    await DispatchService.remove_order_from_pool(oid)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("driver.arrived_btn", user), callback_data=f"arr:{oid}")]])
    await call.message.edit_text(t("driver.accept_success", user, order_id=oid), reply_markup=kb)
    
    order = await OrderRepository.get(oid)
    if order and order['c_tg']:
        driver_pos = await db.redis.geopos("driver:online", str(call.from_user.id))
        dist_km = 0.0
        if driver_pos and driver_pos[0]:
            d_lng, d_lat = driver_pos[0]
            dist_km = round(PricingService.haversine(order['pickup_lat'], order['pickup_lng'], d_lat, d_lng) * 1.3, 1)
        
        p_txt = t("passenger.driver_accepted", user,
                  plate=order['d_plate'] or '未提供',
                  name=order['d_name'] or '司机',
                  phone=order['d_phone'] or '未提供',
                  dist=dist_km)
        
        try:
            await passenger_bot.send_message(order['c_tg'], p_txt)
            if order['d_car_photo']:
                await passenger_bot.send_photo(order['c_tg'], photo=order['d_car_photo'], caption="📸 车辆实拍图")
        except Exception as e: 
            print(f"通知乘客失败: {e}")
