import asyncio, logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from app.config import settings
from app.db.core import db
from app.handlers import common, passenger, driver

async def main():
    logging.basicConfig(level="INFO")
    await db.init()
    p_bot = Bot(token=settings.passenger_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    d_bot = Bot(token=settings.driver_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    
    # 【终极清除】把左下角菜单彻底删干净，什么都不留
    try:
        await p_bot.delete_my_commands()
        await d_bot.delete_my_commands()
        logging.info("✅ 左下角系统菜单已彻底清空！")
    except Exception as e:
        logging.error(f"清空菜单失败: {e}")

    dp_pax = Dispatcher(); dp_pax["driver_bot"] = d_bot; dp_pax.include_routers(common.router, passenger.router)
    dp_drv = Dispatcher(); dp_drv["passenger_bot"] = p_bot; dp_drv.include_routers(common.router, driver.router)
    
    await asyncio.gather(dp_pax.start_polling(p_bot), dp_drv.start_polling(d_bot))

if __name__ == "__main__": asyncio.run(main())
