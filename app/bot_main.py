import asyncio, logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.config import settings
from app.db.core import db
from app.handlers import passenger, driver

async def main():
    logging.basicConfig(level="INFO")
    await db.init()
    p_bot = Bot(token=settings.passenger_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    d_bot = Bot(token=settings.driver_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

    # 乘客端 Dispatcher，只挂载 passenger.router
    dp_pax = Dispatcher()
    dp_pax["driver_bot"] = d_bot
    dp_pax.include_router(passenger.router)

    # 司机端 Dispatcher，只挂载 driver.router
    dp_drv = Dispatcher()
    dp_drv["passenger_bot"] = p_bot
    dp_drv.include_router(driver.router)

    logging.info("✅ 双端独立路由启动成功！")
    await asyncio.gather(dp_pax.start_polling(p_bot), dp_drv.start_polling(d_bot))

if __name__ == "__main__":
    asyncio.run(main())
