from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from app.db.core import get_user, get_user_orders, db
from app.config import settings
from app.i18n.translator import translator

router = Router()

def t(key, user, **vars): return translator.t(key, user.get("locale", "zh"), **vars)

@router.message(F.text.startswith("/start"))
async def cmd_start(msg: Message):
    user = await get_user(msg.from_user.id, msg.from_user.first_name)
    is_driver = msg.bot.token == settings.driver_bot_token
    
    if is_driver:
        items = [[InlineKeyboardButton(text="🧑‍✈️ 司机上线接单", callback_data="menu:driver")]]
    else:
        items = [[InlineKeyboardButton(text="🚗 立即叫车", callback_data="menu:passenger")]]
        
    kb = InlineKeyboardMarkup(inline_keyboard=items)
    await msg.answer("Vcar 叫车系统\n请点击下方按钮：", reply_markup=kb)

@router.callback_query(F.data == "menu:orders")
async def menu_orders(call: CallbackQuery):
    user = await get_user(call.from_user.id)
    orders = await get_user_orders(user['id'])
    await call.answer()
    if not orders: 
        return await call.message.answer("📋 暂无历史订单")
    txt = "\n".join([f"订单 #{o['id']} | {o['status']} | ${float(o['price_estimate']):.2f}" for o in orders])
    await call.message.answer(f"📋 <b>最近订单</b>\n\n{txt}")
