import asyncio
import logging
import signal

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.core.config import settings
from app.db.core import db
from app.services.order_event_outbox import run_order_event_outbox_worker
from app.bot.handlers import passenger, driver


async def main():
    logging.basicConfig(level="INFO")
    await db.init()

    p_bot = Bot(
        token=settings.passenger_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    d_bot = Bot(
        token=settings.driver_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

    dp_pax = Dispatcher()
    dp_pax["driver_bot"] = d_bot
    dp_pax.include_router(passenger.router)

    dp_drv = Dispatcher()
    dp_drv["passenger_bot"] = p_bot
    dp_drv.include_router(driver.router)

    logging.info("✅ Vcar V2 DDD架构启动成功！")

    stop_event = asyncio.Event()
    outbox_worker_task = asyncio.create_task(
        run_order_event_outbox_worker(
            stop_event,
            poll_interval=settings.order_event_outbox_poll_interval,
        ),
        name="order-event-outbox-worker",
    )

    def _shutdown(*_):
        logging.info("🛑 收到停止信号，开始优雅关闭...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _shutdown)
        except NotImplementedError:
            pass

    async def _wait_and_stop():
        await stop_event.wait()
        logging.info("🛑 正在停止两个 dispatcher...")
        await asyncio.gather(
            dp_pax.stop_polling(),
            dp_drv.stop_polling(),
        )

    try:
        await asyncio.gather(
            dp_pax.start_polling(p_bot, handle_signals=False),
            dp_drv.start_polling(d_bot, handle_signals=False),
            _wait_and_stop(),
        )
    finally:
        stop_event.set()
        await outbox_worker_task
        logging.info("🧹 清理资源...")
        try:
            await db.pool.close()
        except Exception as e:
            logging.warning(f"关闭 DB pool 失败: {e}")
        try:
            await db.redis.aclose()
        except AttributeError:
            try:
                await db.redis.close()
            except Exception as e:
                logging.warning(f"关闭 Redis 失败: {e}")
        except Exception as e:
            logging.warning(f"关闭 Redis 失败: {e}")
        await p_bot.session.close()
        await d_bot.session.close()
        logging.info("✅ 已优雅关闭")


if __name__ == "__main__":
    asyncio.run(main())
