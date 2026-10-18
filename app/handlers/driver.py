from typing import Union
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.db.core import db, get_user, get_order, accept_order, update_status, update_driver_info
from app.handlers.common import t

router = Router()
class DrvStates(StatesGroup): 
    location = State()
    step_phone = State()
    step_plate = State()
    step_car_photo = State()
    step_license_photo = State()
    step_drv_license_photo = State()

@router.message(F.text.startswith("/online"))
@router.message(F.text == "🧑‍✈️ 司机端上线")
@router.callback_query(F.data == "menu:driver")
async def cb_menu(event: CallbackQuery, state: FSMContext):
    user = await get_user(event.from_user.id)
    msg = event.message
    await event.answer()
    
    status = user.get('driver_status')
    
    if status == 'PENDING':
        return await msg.answer("⏳ 您的资料正在审核中，请耐心等待。")
    if status == 'APPROVED':
        await state.set_state(DrvStates.location)
        return await msg.answer(t("driver.prompt_location", user))
        
    await state.set_state(DrvStates.step_phone)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ 取消", callback_data="cancel_verify")]])
    await msg.answer("📝 司机入驻步骤 1/5：\n请输入您的【手机号码】：", reply_markup=kb)

@router.callback_query(F.data == "cancel_verify")
async def cancel_verify(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("已取消认证流程。")

@router.message(DrvStates.step_phone)
async def s1_phone(msg: Message, state: FSMContext):
    await state.update_data(phone=msg.text)
    await state.set_state(DrvStates.step_plate)
    await msg.reply("📝 司机入驻步骤 2/5：\n请输入您的【车牌号码】（如：金边 2A-1234）：")

@router.message(DrvStates.step_plate)
async def s2_plate(msg: Message, state: FSMContext):
    await state.update_data(plate=msg.text)
    await state.set_state(DrvStates.step_car_photo)
    await msg.reply("📸 司机入驻步骤 3/5：\n请上传【汽车带牌照的实拍照片】：")

@router.message(DrvStates.step_car_photo, F.photo)
async def s3_car(msg: Message, state: FSMContext):
    await state.update_data(car_pic=msg.photo[-1].file_id)
    await state.set_state(DrvStates.step_license_photo)
    await msg.reply("📸 司机入驻步骤 4/5：\n请上传【车辆行驶证照片】：")

@router.message(DrvStates.step_license_photo, F.photo)
async def s4_license(msg: Message, state: FSMContext):
    await state.update_data(lic_pic=msg.photo[-1].file_id)
    await state.set_state(DrvStates.step_drv_license_photo)
    await msg.reply("📸 司机入驻步骤 5/5：\n请上传【个人驾驶证照片】：")

@router.message(DrvStates.step_drv_license_photo, F.photo)
async def s5_drv_license(msg: Message, state: FSMContext):
    data = await state.get_data()
    await update_driver_info(msg.from_user.id, data['phone'], data['plate'], data['car_pic'], data['lic_pic'], msg.photo[-1].file_id)
    await state.clear()
    await msg.reply("✅ 您的所有资料已提交成功！\n请耐心等待管理员审核。")

@router.message(DrvStates.location, F.location)
async def go_online(msg: Message, state: FSMContext):
    user = await get_user(msg.from_user.id)
    await state.clear()
    await db.redis.geoadd("driver:online", (msg.location.longitude, msg.location.latitude, msg.from_user.id))
    await msg.reply(t("driver.online_success", user))

@router.callback_query(F.data.startswith("acc:"))
async def accept(call: CallbackQuery, passenger_bot):
    user = await get_user(call.from_user.id)
    oid = int(call.data.split(":")[1])
    if not await accept_order(oid, user['id']): return await call.answer("手慢了", show_alert=True)
    await db.redis.zrem("order:pool", str(oid))
    order = await get_order(oid)
    
    contact_info = f"\n\n📞 <b>联系乘客：</b>\n姓名：{order['c_name']}\n"
    contact_info += f"TG：@{order['c_user']}" if order['c_user'] else f"TG ID：<code>{order['c_tg']}</code>"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("driver.arrived_btn", user), callback_data=f"arr:{oid}")]])
    await call.message.edit_text(t("driver.accept_success", user, order_id=oid) + contact_info, reply_markup=kb)
    
    if order and order['c_tg']:
        p_txt = t("passenger.driver_accepted", user, tg_id=call.from_user.id) + f"\n\n🚗 <b>司机信息：</b>\n姓名：{user['first_name']}\n车牌：{user.get('plate_number','-')}\n"
        p_txt += f"TG：@{user['username']}" if user.get('username') else f"TG ID：<code>{call.from_user.id}</code>"
        try: await passenger_bot.send_message(order['c_tg'], p_txt)
        except: pass

@router.callback_query(F.data.startswith("arr:"))
async def arrived(call: CallbackQuery, passenger_bot):
    user = await get_user(call.from_user.id)
    oid = int(call.data.split(":")[1])
    if await update_status(oid, 'ARRIVED', 'ACCEPTED'):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("driver.start_btn", user), callback_data=f"sta:{oid}")]])
        await call.message.edit_text(t("driver.arrived_msg", user), reply_markup=kb)
        order = await get_order(oid)
        if order and order['c_tg']:
            try: await passenger_bot.send_message(order['c_tg'], t("passenger.driver_arrived", user))
            except: pass

@router.callback_query(F.data.startswith("sta:"))
async def start(call: CallbackQuery, passenger_bot):
    user = await get_user(call.from_user.id)
    oid = int(call.data.split(":")[1])
    if await update_status(oid, 'IN_PROGRESS', 'ARRIVED'):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("driver.finish_btn", user), callback_data=f"fin:{oid}")]])
        await call.message.edit_text(t("driver.start_msg", user), reply_markup=kb)
        order = await get_order(oid)
        if order and order['c_tg']:
            try: await passenger_bot.send_message(order['c_tg'], t("passenger.trip_started", user))
            except: pass

@router.callback_query(F.data.startswith("fin:"))
async def finish(call: CallbackQuery, passenger_bot):
    user = await get_user(call.from_user.id)
    oid = int(call.data.split(":")[1])
    if await update_status(oid, 'COMPLETED', 'IN_PROGRESS'):
        await call.message.edit_text(t("driver.finish_msg", user, order_id=oid))
        order = await get_order(oid)
        if order and order['c_tg']:
            price_str = f"{float(order['price_estimate']):.2f}"
            try: await passenger_bot.send_message(order['c_tg'], t("passenger.trip_completed", user, price=price_str))
            except: pass
