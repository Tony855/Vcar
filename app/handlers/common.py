from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from app.i18n.translator import translator
from app.db.core import db, get_user, get_user_orders
from app.config import settings

router = Router()
def t(key, user, **vars): return translator.t(key, user.get("locale", "zh"), **vars)

# 统一发送主菜单（包含底部常驻键盘）
async def send_main_menu(msg: Message, user, is_driver=False):
    btn_passenger = KeyboardButton(text="🚗 叫车")
    btn_driver = KeyboardButton(text="🧑‍✈️ 司机端")
    btn_orders = KeyboardButton(text="📋 我的订单")
    btn_lang = KeyboardButton(text="🌐 语言")
    btn_main = KeyboardButton(text="🏠 主菜单")
    
    if is_driver:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[btn_driver], [btn_orders, btn_lang], [btn_main]],
            resize_keyboard=True, is_persistent=True
        )
    else:
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[btn_passenger], [btn_orders, btn_lang], [btn_main]],
            resize_keyboard=True, is_persistent=True
        )
        
    await msg.answer(t("menu.title", user), reply_markup=keyboard)

@router.message(F.text.startswith("/start") | (F.text == "🏠 主菜单"))
async def cmd_start(msg: Message):
    user = await get_user(msg.from_user.id, msg.from_user.first_name)
    is_driver = msg.bot.token == settings.driver_bot_token
    await send_main_menu(msg, user, is_driver)

@router.message(F.text.startswith("/orders") | (F.text == "📋 我的订单"))
async def menu_orders(msg: Message):
    user = await get_user(msg.from_user.id)
    orders = await get_user_orders(user['id'])
    if not orders: 
        return await msg.answer(t("common.no_orders", user))
    txt = "\n".join([t("order.list_item", user, id=o["id"], status=t(f"status.{o['status']}", user), price=f"{float(o['price_estimate']):.2f}" if o['price_estimate'] else "0.00") for o in orders])
    await msg.answer(f"📋 <b>历史订单</b>\n\n{txt}")

@router.message(F.text.startswith("/lang") | (F.text == "🌐 语言"))
async def menu_lang(msg: Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🇨🇳 中文", callback_data="lang:zh"), InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en")], 
        [InlineKeyboardButton(text="🇰🇭 ខ្មែរ", callback_data="lang:km")]
    ])
    await msg.answer("请选择语言 / Choose Language / សូមជ្រើសរើសភាសា", reply_markup=kb)

@router.callback_query(F.data.startswith("lang:"))
async def set_lang(call: CallbackQuery):
    loc = call.data.split(":")[1]
    async with db.pool.acquire() as c: await c.execute("UPDATE users SET locale=$1 WHERE telegram_id=$2", loc, call.from_user.id)
    await call.answer(translator.t("lang.changed", loc), show_alert=True)
