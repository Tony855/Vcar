from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from app.db.core import db, get_user, get_order, accept_order, update_status, update_driver_status
from app.handlers.common import t

router = Router()
class DrvStates(StatesGroup): 
    location = State()
    waiting_for_verify = State() # 等待上传证件

@router.callback_query(F.data == "menu:driver")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    user = await get_user(call.from_user.id)
    
    # 如果未通过审核，进入审核流程
    if user.get('driver_status') != 'APPROVED':
        await state.set_state(DrvStates.waiting_for_verify)
        txt = "⚠️ 您尚未通过司机认证。\n请发送您的【身份证或驾驶证照片】进行审核。"
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="❌ 取消", callback_data="cancel_verify")]])
        await call.message.answer(txt, reply_markup=kb)
        await call.answer()
        return
        
    await state.set_state(DrvStates.location)
    await call.message.answer(t("driver.prompt_location", user)); await call.answer()

@router.callback_query(F.data == "cancel_verify")
async def cancel_verify(call: CallbackQuery, state: FSMContext):
    await state.clear()
    await call.message.edit_text("已取消认证流程。")

@router.message(DrvStates.waiting_for_verify, F.photo)
async def handle_verify_photo(msg: Message, state: FSMContext):
    await state.clear()
    await update_driver_status(msg.from_user.id, 'PENDING')
    await msg.reply("✅ 您的证件已提交成功！请耐心等待管理员审核。审核结果将通过消息通知您。")

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
    
    # 生成联系方式
    contact_info = f"\n\n📞 <b>联系乘客：</b>\n姓名：{order['c_name']}\n"
    if order['c_user']:
        contact_info += f"TG：@{order['c_user']}"
    else:
        contact_info += f"TG ID：<code>{order['c_tg']}</code>"

    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t("driver.arrived_btn", user), callback_data=f"arr:{oid}")]])
    await call.message.edit_text(t("driver.accept_success", user, order_id=oid) + contact_info, reply_markup=kb)
    
    if order and order['c_tg']:
        # 通知乘客
        p_txt = t("passenger.driver_accepted", user, tg_id=call.from_user.id) + f"\n\n🚗 <b>司机信息：</b>\n姓名：{user['first_name']}\n"
        if user['username']:
            p_txt += f"TG：@{user['username']}"
        else:
            p_txt += f"TG ID：<code>{call.from_user.id}</code>"
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
