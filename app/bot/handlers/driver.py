import time
import asyncio
from aiogram.filters import Command
from aiogram import Router, F
from aiogram.types import WebAppInfo, Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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
import io

router = Router()
class DrvStates(StatesGroup): 
    location = State()
    reg_phone = State()
    reg_plate = State()
    reg_car_photo = State()
    reg_license_photo = State()
    reg_drv_license_photo = State()

def t(key, user, **vars): 
    return translator.t(key, user.get('locale', 'zh'), **vars)

async def send_drv_menu(msg: Message, user):
    web_app_url = f"https://vcar.v2net.cc/mini/{msg.from_user.id}?v={int(time.time())}"
    items = [
        [InlineKeyboardButton(text=t("menu.webapp", user), web_app=WebAppInfo(url=web_app_url))],
        [InlineKeyboardButton(text=t('menu.driver', user), callback_data='menu:driver')],
        [InlineKeyboardButton(text=t('driver.offline_btn', user), callback_data='menu:offline')],
        [InlineKeyboardButton(text=t('menu.orders', user), callback_data='menu:orders'), InlineKeyboardButton(text=t('menu.lang', user), callback_data='menu:lang')]
    ]
    await msg.answer(f"<b>{t('menu.title', user)}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=items))


@router.message(Command("profile"))
async def cmd_profile(msg: Message):
    url = f"https://vcar.v2net.cc/mini/{msg.from_user.id}"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👤 打开我的主页", web_app=WebAppInfo(url=url))]
    ])
    await msg.answer("👇 点击下方按钮查看您的评分与评价：", reply_markup=kb)

@router.message(F.text.startswith('/start'))
async def cmd_start(msg: Message, state: FSMContext):
    # 🚨 强制清空状态：防止处于“等待输入手机号”时发 /start 导致界面卡死
    await state.clear()
    user = await UserRepository.get_or_create(msg.from_user.id, msg.from_user.first_name)
    await send_drv_menu(msg, user)

    # 🌟 新增：自动恢复进行中的订单
    active = await OrderRepository.get_active_order_by_user(user['id'])
    if active:
        await msg.answer(t("driver.restore_msg", user))
        status = active.get('status')
        
        if status == 'ACCEPTED':
            loc_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t('driver.arrive_btn', user), request_location=True)]], resize_keyboard=True, one_time_keyboard=True)
            await msg.answer(t("driver.accept_prompt", user), reply_markup=loc_kb)
            
        elif status == 'ARRIVED':
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t('driver.start_btn', user), callback_data=f"sta:{active['id']}")]])
            await msg.answer(t('driver.trip_ready_prompt', user), reply_markup=kb)
            
        elif status == 'IN_PROGRESS':
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t('driver.finish_btn', user), callback_data=f"fin:{active['id']}")]])
            await msg.answer(t('driver.start_msg', user), reply_markup=kb)

@router.callback_query(F.data == 'menu:orders')
async def menu_orders(call: CallbackQuery):
    user = await UserRepository.get_or_create(call.from_user.id)
    await call.answer('正在查询...')
    try:
        orders = await OrderRepository.get_driver_orders(user['id'])
        if not orders: return await call.message.answer(t('common.no_orders', user))
        lines_txt = [t('order.list_item', user, id=o['id'], status=t(f"status.{o['status']}", user), price=f"{float(o['price_estimate']):.2f}") for o in orders]
        await call.message.answer('📋 <b>历史接单</b>' + chr(10)*2 + chr(10).join(lines_txt))
    except Exception as e: logging.error(e)

@router.callback_query(F.data == 'menu:lang')
async def menu_lang(call: CallbackQuery):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='🇨🇳 中文', callback_data='lang:zh'), InlineKeyboardButton(text='🇬🇧 English', callback_data='lang:en')],
        [InlineKeyboardButton(text='🇰🇭 ខ្មែរ', callback_data='lang:km')]
    ])
    await call.message.edit_text('请选择语言 / Choose language', reply_markup=kb)
    await call.answer()

@router.callback_query(F.data.startswith('lang:'))
async def set_lang(call: CallbackQuery):
    loc = call.data.split(':')[1]
    async with db.pool.acquire() as c:
        await c.execute('UPDATE users SET locale=$1 WHERE telegram_id=$2', loc, call.from_user.id)
    await call.answer()
    user = await UserRepository.get_or_create(call.from_user.id)
    await call.message.delete()
    await send_drv_menu(call.message, user)

@router.callback_query(F.data == 'menu:offline')
async def cb_offline(call: CallbackQuery):
    user = await UserRepository.get_or_create(call.from_user.id)
    # 🚨 强制身份校验：没认证通过绝不允许下线
    if user.get('driver_status') != 'APPROVED':
        await call.answer("⚠️ 您还未完成司机认证，无法下线！", show_alert=True)
        return
    await DispatchService.go_offline(str(call.from_user.id))
    await call.answer(t('driver.offline_success', user), show_alert=True)
    await call.message.edit_text(f"<b>{t('driver.offline_success', user)}</b>")

@router.callback_query(F.data == 'menu:driver')
async def cb_menu(call: CallbackQuery, state: FSMContext):
    user = await UserRepository.get_or_create(call.from_user.id)
    await call.answer()
    status = user.get('driver_status')
    if status == DriverStatus.PENDING.value: return await call.message.answer(t('driver.status_pending', user))
    if status == DriverStatus.APPROVED.value:
        is_online = await db.redis.geopos('driver:online', str(call.from_user.id))
        if is_online and len(is_online) > 0 and is_online[0] and is_online[0][0] is not None:
            return await call.message.answer(t('driver.already_online', user))
        await state.set_state(DrvStates.location)
        return await call.message.answer(t('driver.prompt_location', user))
    await state.set_state(DrvStates.reg_phone)
    await call.message.answer(t('driver.reg_step1', user))

@router.message(DrvStates.reg_phone)
async def s1(msg: Message, state: FSMContext):
    user = await UserRepository.get_or_create(msg.from_user.id)
    await state.update_data(phone=msg.text)
    await state.set_state(DrvStates.reg_plate)
    await msg.reply(t('driver.reg_step2', user))

@router.message(DrvStates.reg_plate)
async def s2(msg: Message, state: FSMContext):
    user = await UserRepository.get_or_create(msg.from_user.id)
    await state.update_data(plate=msg.text)
    await state.set_state(DrvStates.reg_car_photo)
    await msg.reply(t('driver.reg_step3', user))

@router.message(DrvStates.reg_car_photo, F.photo)
async def s3(msg: Message, state: FSMContext):
    user = await UserRepository.get_or_create(msg.from_user.id)
    await state.update_data(car_pic=msg.photo[-1].file_id)
    await state.set_state(DrvStates.reg_license_photo)
    await msg.reply(t('driver.reg_step4', user))

@router.message(DrvStates.reg_license_photo, F.photo)
async def s4(msg: Message, state: FSMContext):
    user = await UserRepository.get_or_create(msg.from_user.id)
    await state.update_data(lic_pic=msg.photo[-1].file_id)
    await state.set_state(DrvStates.reg_drv_license_photo)
    await msg.reply(t('driver.reg_step5', user))

@router.message(DrvStates.reg_drv_license_photo, F.photo)
async def s5(msg: Message, state: FSMContext):
    user = await UserRepository.get_or_create(msg.from_user.id)
    data = await state.get_data()
    await UserRepository.update_driver_info(msg.from_user.id, data['phone'], data['plate'], data['car_pic'], data['lic_pic'], msg.photo[-1].file_id)
    await state.clear()
    await msg.reply(t('driver.reg_success', user))

@router.message(DrvStates.location, F.location)
async def go_online(msg: Message, state: FSMContext):
    user = await UserRepository.get_or_create(msg.from_user.id)
    await state.clear()
    await DispatchService.go_online(str(msg.from_user.id), msg.location.longitude, msg.location.latitude)
    await msg.reply(t('driver.online_success', user))

@router.callback_query(F.data.startswith('acc:'))
async def accept(call: CallbackQuery, state: FSMContext, passenger_bot):
    user = await UserRepository.get_or_create(call.from_user.id)
    if user.get('driver_status') != DriverStatus.APPROVED.value: return await call.answer(t('driver.not_approved', user), show_alert=True)
    oid = int(call.data.split(':')[1])
    if not await OrderRepository.accept(oid, user['id']): return await call.answer(t('common.slow_hand', user), show_alert=True)
    await DispatchService.remove_order_from_pool(oid)
    await state.clear()
    loc_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text=t('driver.arrive_btn', user), request_location=True)]], resize_keyboard=True, one_time_keyboard=True)
    await call.message.edit_text(t('driver.accept_success', user, order_id=oid))
    await call.message.answer(t('driver.accept_prompt', user), reply_markup=loc_kb)
    order = await OrderRepository.get(oid)
    if order and order.get('c_tg'):
        d_pos = await db.redis.geopos('driver:online', str(call.from_user.id))
        d_km = round(PricingService.haversine(order['pickup_lat'], order['pickup_lng'], d_pos[0][1], d_pos[0][0]) * 1.3, 1) if d_pos and d_pos[0] else 0.0
        p_txt = t('passenger.driver_accepted', order.get('c_locale', 'en'), plate=order.get('d_plate') or '无', name=order.get('d_name') or '司机', phone=order.get('d_phone') or '无', dist=d_km)
        try:
            await passenger_bot.send_message(order['c_tg'], p_txt)
            if order.get('d_car_photo'):
                file_io = io.BytesIO()
                await call.bot.download(order['d_car_photo'], destination=file_io)
                photo_file = BufferedInputFile(file_io.getvalue(), filename='car.jpg')
                await passenger_bot.send_photo(order['c_tg'], photo=photo_file, caption=t("common.car_photo", order.get("c_locale", "en")))
            else:
                await passenger_bot.send_message(order['c_tg'], t('passenger.no_car_photo', order.get('c_locale', 'en')))
        except Exception as e: logging.error(e)

@router.message(F.location)
async def handle_driver_location(msg: Message, passenger_bot):
    user = await UserRepository.get_or_create(msg.from_user.id)
    active = await OrderRepository.get_active_order_by_user(user['id'])
    
    if not active or not active.get('c_tg'):
        return await msg.answer("❌ 您当前没有进行中的订单。")

    # 1. 把司机当前的位置实时转发给乘客
    try:
        asyncio.create_task(passenger_bot.send_location(active['c_tg'], latitude=msg.location.latitude, longitude=msg.location.longitude))
        asyncio.create_task(passenger_bot.send_message(active['c_tg'], t("common.driver_location_update", {"locale": active.get("c_locale")})))
    except Exception as e:
        import logging
        logging.error(f"发送位置给乘客失败: {e}")

    # 2. 如果状态是已接单(ACCEPTED)，说明司机刚到，发“开始”按钮
    if active['status'] == 'ACCEPTED':
        if await OrderRepository.update_status(active['id'], OrderStatus.ARRIVED.value, OrderStatus.ACCEPTED.value):
            # 强制清理底部长条键盘
            await msg.answer(t("driver.arrived_success", user), reply_markup=ReplyKeyboardRemove())
            
            kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t('driver.start_btn', user), callback_data=f"sta:{active['id']}")]])
            await msg.answer(t('driver.trip_ready_prompt', user), reply_markup=kb)
    else:
        # 如果已经在上车等待，只是随便发个位置，就只回复已更新
        await msg.answer("✅ 位置已更新，已同步给乘客。")


@router.callback_query(F.data.startswith('sta:'))
async def start(call: CallbackQuery, passenger_bot):
    user = await UserRepository.get_or_create(call.from_user.id)
    oid = int(call.data.split(':')[1])
    if await OrderRepository.update_status(oid, OrderStatus.IN_PROGRESS.value, OrderStatus.ARRIVED.value):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t('driver.finish_btn', user), callback_data=f"fin:{oid}")]])
        await call.message.edit_text(t('driver.start_msg', user), reply_markup=kb)
        order = await OrderRepository.get(oid)
        if order and order.get('c_tg'):
            try: asyncio.create_task(passenger_bot.send_message(order['c_tg'], t("passenger.trip_started", {"locale": order.get("c_locale")})))
            except Exception: pass

@router.callback_query(F.data.startswith('fin:'))
async def finish(call: CallbackQuery, passenger_bot):
    user = await UserRepository.get_or_create(call.from_user.id)
    oid = int(call.data.split(':')[1])
    if await OrderRepository.update_status(oid, OrderStatus.COMPLETED.value, OrderStatus.IN_PROGRESS.value):
        await call.message.edit_text(t('driver.finish_msg', user, order_id=oid))
        order = await OrderRepository.get(oid)
        if order and order.get('c_tg'):
            price_str = f"{float(order['price_estimate']):.2f}"
            try: 
                asyncio.create_task(passenger_bot.send_message(order['c_tg'], t("passenger.trip_completed", {"locale": order.get("c_locale")}, price=price_str)))
                stars = [[InlineKeyboardButton(text=f"{'⭐'*i} {i}星", callback_data=f"rate_pax:{oid}:{i}")] for i in range(1, 6)]
                await passenger_bot.send_message(order["c_tg"], t("passenger.rate_drv_prompt", {"locale": order.get("c_locale", "en")}), reply_markup=InlineKeyboardMarkup(inline_keyboard=stars))
            except Exception: pass
        stars_drv = [[InlineKeyboardButton(text=f"{'⭐'*i} {i}星", callback_data=f"rate_drv:{oid}:{i}")] for i in range(1, 6)]
        await call.message.answer(t("driver.rate_pax_prompt", user), reply_markup=InlineKeyboardMarkup(inline_keyboard=stars_drv))

@router.callback_query(F.data.startswith('rate_drv:'))
async def rate_driver_to_pax(call: CallbackQuery):
    _, oid, score = call.data.split(':')
    order = await OrderRepository.get(int(oid))
    if order and order.get('c_tg'):
        await UserRepository.update_user_rating(order['c_tg'], int(score))
        
        # 👇 新增：将评价写入 reviews 表 (为了数据库完整性，顺便把司机的评分也存一下)
        async with db.pool.acquire() as c:
            exists = await c.fetchval("SELECT id FROM reviews WHERE order_id=$1 AND driver_id=$2", int(oid), order['driver_id'])
            if not exists:
                await c.execute(
                    "INSERT INTO reviews (order_id, driver_id, passenger_id, rating) VALUES ($1, $2, $3, $4)",
                    int(oid), order['driver_id'], order['customer_id'], int(score)
                )
        # 👆
        
        await call.message.edit_text(t("driver.rated_pax", user, score=int(score)))
    await call.answer()

@router.message(F.text | F.voice | F.photo)
async def drv_chat_proxy(msg: Message, passenger_bot):
    if msg.text and msg.text.startswith('/'): return
    user = await UserRepository.get_or_create(msg.from_user.id)
    active = await OrderRepository.get_active_order_by_user(user['id'])
    if active and active.get('c_tg'):
        try:
            await passenger_bot.send_message(active['c_tg'], t("common.from_driver", {"locale": active.get("c_locale")}))
            await msg.copy_to(active['c_tg'])
        except Exception as e:
            import logging
            logging.error(f"❌ [跨Bot转发失败] 乘客TG:{active['c_tg']} 报错: {e}")
# ================= 独立的司机主页入口 =================

