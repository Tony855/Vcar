"""Bot worker with safe waiting and hot reload for admin-managed credentials."""
from __future__ import annotations
import asyncio
import logging
import signal
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.db.core import db
from app.services.bot_credentials import load_bot_tokens
from app.services.order_event_outbox import run_order_event_outbox_worker
from app.bot.handlers import passenger, driver

log = logging.getLogger("vcar.bot")

def _dispatchers(passenger_bot: Bot, driver_bot: Bot):
    passenger_dispatcher = Dispatcher()
    passenger_dispatcher["driver_bot"] = driver_bot
    passenger_dispatcher.include_router(passenger.router)
    driver_dispatcher = Dispatcher()
    driver_dispatcher["passenger_bot"] = passenger_bot
    driver_dispatcher.include_router(driver.router)
    return passenger_dispatcher, driver_dispatcher

async def _stop_pollers(tasks, dispatchers):
    for dispatcher in dispatchers:
        try:
            await dispatcher.stop_polling()
        except RuntimeError:
            pass
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

async def _run_bots(stop_event: asyncio.Event):
    fingerprint = None
    tasks, dispatchers, bots = [], [], []
    while not stop_event.is_set():
        try:
            tokens = await load_bot_tokens(db.pool)
        except Exception:
            log.exception("Cannot load encrypted Bot settings; retrying")
            tokens = ("", "")
        if tokens != fingerprint:
            if tasks:
                await _stop_pollers(tasks, dispatchers)
                for bot in bots:
                    await bot.session.close()
            tasks, dispatchers, bots = [], [], []
            fingerprint = tokens
            if all(tokens):
                passenger_bot = Bot(tokens[0], default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                driver_bot = Bot(tokens[1], default=DefaultBotProperties(parse_mode=ParseMode.HTML))
                dispatchers = list(_dispatchers(passenger_bot, driver_bot))
                bots = [passenger_bot, driver_bot]
                tasks = [
                    asyncio.create_task(dispatchers[0].start_polling(passenger_bot, handle_signals=False)),
                    asyncio.create_task(dispatchers[1].start_polling(driver_bot, handle_signals=False)),
                ]
                log.info("Both Telegram Bots configured; polling started")
            else:
                log.info("Telegram Bots are not fully configured; waiting for admin setup")
        if tasks and any(task.done() for task in tasks):
            for task in tasks:
                if task.done() and not task.cancelled():
                    error = task.exception()
                    if error:
                        log.error("Bot polling stopped: %s", type(error).__name__)
            await _stop_pollers(tasks, dispatchers)
            for bot in bots:
                await bot.session.close()
            tasks, dispatchers, bots = [], [], []
            fingerprint = None
            await asyncio.sleep(2)
            continue
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5)
        except asyncio.TimeoutError:
            pass
    if tasks:
        await _stop_pollers(tasks, dispatchers)
    for bot in bots:
        await bot.session.close()

async def main():
    logging.basicConfig(level=logging.INFO)
    await db.init()
    stop_event = asyncio.Event()
    worker = asyncio.create_task(run_order_event_outbox_worker(stop_event), name="order-event-outbox-worker")
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop_event.set)
        except NotImplementedError:
            pass
    try:
        await _run_bots(stop_event)
    finally:
        stop_event.set()
        await worker
        await db.close()

if __name__ == "__main__":
    asyncio.run(main())
