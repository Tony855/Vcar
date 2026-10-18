from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
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
    items = [
        [InlineKeyboardButton(text=t('menu.driver', user), callback_data='menu:driver')],
        [InlineKeyboardButton(text=t('driver.offline_btn', user), callback_data='menu:offline')],
        [InlineKeyboardButton(text=t('menu.orders', user), callback_data='menu:orders'), InlineKeyboardButton(text=t('menu.lang', user), callback_data='menu:lang')]
    ]
    await msg.answer(f"<b>{t('menu.title', user)}</b>", reply_markup=InlineKeyboardMarkup(inline_keyboard=items))

@router.message(F.text.startswith('/start'))
async def cmd_start(msg: Message):
    user = await UserRepository.get_or_create(msg.from_user.id, msg.from_user.first_name)
    await send_drv_menu(msg, user)

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
    loc_kb = ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text='📍 确认到达接客点', request_location=True)]], resize_keyboard=True, one_time_keyboard=True)
    await call.message.edit_text(t('driver.accept_success', user, order_id=oid))
    await call.message.answer('👇 请前往接客点。到达后，请点击下方的【确认到达】按钮：', reply_markup=loc_kb)
    order = await OrderRepository.get(oid)
    if order and order.get('c_tg'):
        d_pos = await db.redis.geopos('driver:online', str(call.from_user.id))
        d_km = round(PricingService.haversine(order['pickup_lat'], order['pickup_lng'], d_pos[0][1], d_pos[0][0]) * 1.3, 1) if d_pos and d_pos[0] else 0.0
        p_txt = t('passenger.driver_accepted', user, plate=order.get('d_plate') or '无', name=order.get('d_name') or '司机', phone=order.get('d_phone') or '无', dist=d_km)
        try:
            await passenger_bot.send_message(order['c_tg'], p_txt)
            if order.get('d_car_photo'):
                file_io = io.BytesIO()
                await call.bot.download(order['d_car_photo'], destination=file_io)
                photo_file = BufferedInputFile(file_io.getvalue(), filename='car.jpg')
                await passenger_bot.send_photo(order['c_tg'], photo=photo_file, caption='📸 车辆实拍图')
            else:
                await passenger_bot.send_message(order['c_tg'], t('passenger.no_car_photo', user))
        except Exception as e: logging.error(e)

@router.message(F.location)
async def handle_driver_location_update(msg: Message, passenger_bot):
    user = await UserRepository.get_or_create(msg.from_user.id)
    active = await OrderRepository.get_active_order_by_user(user['id'])
    if active and active.get('c_tg'):
        if active['status'] == 'ACCEPTED':
            if await OrderRepository.update_status(active['id'], OrderStatus.ARRIVED.value, OrderStatus.ACCEPTED.value):
                await passenger_bot.send_location(active['c_tg'], latitude=msg.location.latitude, longitude=msg.location.longitude)
                await passenger_bot.send_message(active['c_tg'], t('passenger.driver_arrived', user))
                await msg.answer(t('driver.arrived_msg', user), reply_markup=ReplyKeyboardRemove())
                kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t('driver.start_btn', user), callback_data=f"sta:{active['id']}")]])
                await msg.answer(t('driver.trip_ready_prompt', user), reply_markup=kb)
                return
        await passenger_bot.send_location(active['c_tg'], latitude=msg.location.latitude, longitude=msg.location.longitude)
        await passenger_bot.send_message(active['c_tg'], t('common.driver_location_update', user))

@router.callback_query(F.data.startswith('sta:'))
async def start(call: CallbackQuery, passenger_bot):
    user = await UserRepository.get_or_create(call.from_user.id)
    oid = int(call.data.split(':')[1])
    if await OrderRepository.update_status(oid, OrderStatus.IN_PROGRESS.value, OrderStatus.ARRIVED.value):
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=t('driver.finish_btn', user), callback_data=f"fin:{oid}")]])
        await call.message.edit_text(t('driver.start_msg', user), reply_markup=kb)
        order = await OrderRepository.get(oid)
        if order and order.get('c_tg'):
            try: await passenger_bot.send_message(order['c_tg'], t('passenger.trip_started', user))
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
                await passenger_bot.send_message(order['c_tg'], t('passenger.trip_completed', user, price=price_str))
                stars = [[InlineKeyboardButton(text=f"{'⭐'*i} {i}星", callback_data=f"rate_pax:{oid}:{i}")] for i in range(1, 6)]
                await passenger_bot.send_message(order['c_tg'], '⭐ <b>请评价本次行程的司机：</b>', reply_markup=InlineKeyboardMarkup(inline_keyboard=stars))
            except Exception: pass
        stars_drv = [[InlineKeyboardButton(text=f"{'⭐'*i} {i}星", callback_data=f"rate_drv:{oid}:{i}")] for i in range(1, 6)]
        await call.message.answer('⭐ <b>请评价乘客：</b>', reply_markup=InlineKeyboardMarkup(inline_keyboard=stars_drv))

@router.callback_query(F.data.startswith('rate_drv:'))
async def rate_driver_to_pax(call: CallbackQuery):
    _, oid, score = call.data.split(':')
    order = await OrderRepository.get(int(oid))
    if order and order.get('c_tg'):
        await UserRepository.update_user_rating(order['c_tg'], int(score))
        await call.message.edit_text(f'✅ 您已对乘客评分：{int(score)} 星。')
    await call.answer()

@router.message(F.text | F.voice | F.photo)
async def drv_chat_proxy(msg: Message, passenger_bot):
    if msg.text and msg.text.startswith('/'): return
    user = await UserRepository.get_or_create(msg.from_user.id)
    active = await OrderRepository.get_active_order_by_user(user['id'])
    if active and active.get('c_tg'):
        try:
            await passenger_bot.send_message(active['c_tg'], t('common.from_driver', user))
            await msg.copy_to(active['c_tg'])
        except Exception: pass