from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from app.db.core import get_user, get_user_orders, db
from app.config import settings
from app.i18n.translator import translator

router = Router()

@router.message(F.text.startswith("/start"))
async def cmd_start(msg: Message):
    user = await get_user(msg.from_user.id, msg.from_user.first_name)
    is_driver = msg.bot.token == settings.driver_bot_token
    
    if is_driver:
        items = [
            [InlineKeyboardButton(text="🧑‍✈️ 司机上线接单", callback_data="menu:driver")],
            [InlineKeyboardButton(text="📋 我的订单", callback_data="menu:orders")],
            [InlineKeyboardButton(text="🌐 切换语言", callback_data="menu:lang")]
        ]
    else:
        items = [
            [InlineKeyboardButton(text="🚗 立即叫车", callback_data="menu:passenger")],
            [InlineKeyboardButton(text="📋 我的订单", callback_data="menu:orders")],
            [InlineKeyboardButton(text="🌐 切换语言", callback_data="menu:lang")]
        ]
        
    kb = InlineKeyboardMarkup(inline_keyboard=items)
    await msg.answer("Vcar · 主菜单\n请选择下方操作：", reply_markup=kb)

@router.callback_query(F.data == "menu:orders")
async def menu_orders(call: CallbackQuery):
    user = await get_user(call.from_user.id)
    orders = await get_user_orders(user['id'])
    await call.answer()
    if not orders: 
        return await call.message.answer("📋 暂无历史订单")
    txt = "\n".join([f"订单 #{o['id']} | {o['status']} | ${float(o['price_estimate']):.2f}" for o in orders])
    await call.message.answer(f"📋 <b>最近订单</b>\n\n{txt}")

@router.callback_query(F.data == "menu:lang")
async def menu_lang(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇨🇳 中文", callback_data="lang:zh"), InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en")], 
        [InlineKeyboardButton(text="🇰🇭 ខ្មែរ", callback_data="lang:km")]
    ])
    await call.message.answer("请选择语言 / Choose language", reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith("lang:"))
async def set_lang(call: CallbackQuery):
    loc = call.data.split(":")[1]
    async with db.pool.acquire() as c: 
        await c.execute("UPDATE users SET locale=$1 WHERE telegram_id=$2", loc, call.from_user.id)
    await call.answer("✅ 语言已切换", show_alert=True)
