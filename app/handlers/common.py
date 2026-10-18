from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from app.i18n.translator import translator
from app.db.core import db, get_user, get_user_orders
from app.config import settings
router = Router()
def t(key, user, **vars): return translator.t(key, user.get("locale", "zh"), **vars)
@router.message(F.text.startswith("/start"))
async def cmd_start(msg: Message):
    user = await get_user(msg.from_user.id, msg.from_user.first_name)
    is_driver = msg.bot.token == settings.driver_bot_token
    items = [{"text": t("menu.driver" if is_driver else "menu.passenger", user), "cb": "menu:driver" if is_driver else "menu:passenger"}, {"text": t("menu.orders", user), "cb": "menu:orders"}, {"text": "🌐 语言", "cb": "menu:lang"}]
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=i["text"], callback_data=i["cb"])] for i in items])
    await msg.answer(t("menu.title", user), reply_markup=kb)
@router.callback_query(F.data == "menu:lang")
async def cb_lang(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🇨🇳 中文", callback_data="lang:zh"), InlineKeyboardButton(text="🇬🇧 English", callback_data="lang:en")], [InlineKeyboardButton(text="🇰🇭 ខ្មែរ", callback_data="lang:km")]])
    await call.message.edit_text("请选择语言", reply_markup=kb)
@router.callback_query(F.data.startswith("lang:"))
async def set_lang(call: CallbackQuery):
    loc = call.data.split(":")[1]
    async with db.pool.acquire() as c: await c.execute("UPDATE users SET locale=$1 WHERE telegram_id=$2", loc, call.from_user.id)
    await call.answer(translator.t("lang.changed", loc), show_alert=True)
@router.callback_query(F.data == "menu:orders")
async def cb_orders(call: CallbackQuery):
    user = await get_user(call.from_user.id)
    orders = await get_user_orders(user['id'])
    if not orders: return await call.message.answer(t("common.no_orders", user))
    txt = "\n".join([t("order.list_item", user, id=o["id"], status=t(f"status.{o['status']}", user), price=f"{float(o['price_estimate']):.2f}" if o['price_estimate'] else "0.00") for o in orders])
    await call.message.answer(f"📋 <b>历史订单</b>\n\n{txt}")
    await call.answer()
