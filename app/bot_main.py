import asyncio, logging
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import BotCommand
from app.config import settings
from app.db.core import db
from app.handlers import common, passenger, driver

async def main():
    logging.basicConfig(level="INFO")
    await db.init()
    p_bot = Bot(token=settings.passenger_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    d_bot = Bot(token=settings.driver_bot_token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    
    # 乘客端底部指令菜单
    pax_commands = [
        BotCommand(command="ride", description="🚗 叫车 (Book Ride)"),
        BotCommand(command="start", description="🏠 主菜单 (Main Menu)"),
        BotCommand(command="orders", description="📋 我的订单 (My Orders)"),
        BotCommand(command="lang", description="🌐 语言 (Language)"),
    ]
    # 司机端底部指令菜单
    drv_commands = [
        BotCommand(command="start", description="🏠 主菜单 (Main Menu)"),
        BotCommand(command="orders", description="📋 我的订单 (My Orders)"),
        BotCommand(command="lang", description="🌐 语言 (Language)"),
    ]
    
    await p_bot.set_my_commands(pax_commands)
    await d_bot.set_my_commands(drv_commands)

    dp_pax = Dispatcher(); dp_pax["driver_bot"] = d_bot; dp_pax.include_routers(common.router, passenger.router)
    dp_drv = Dispatcher(); dp_drv["passenger_bot"] = p_bot; dp_drv.include_routers(common.router, driver.router)
    
    logging.info("✅ 双端启动成功！")
    await asyncio.gather(dp_pax.start_polling(p_bot), dp_drv.start_polling(d_bot))

if __name__ == "__main__": 
    asyncio.run(main())
