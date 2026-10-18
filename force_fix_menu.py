import re

filepath = "app/bot/handlers/driver.py"
with open(filepath, "r", encoding="utf-8") as f:
    code = f.read()

# 1. 暴力删除所有包含 "Vcar 主菜单" 的 handler 函数块
# 这会匹配 @router.message... 开始，直到遇到下一个 @router 或者文件结束
pattern = re.compile(r'@router\.message.*?async def .*?[\s\S]*?Vcar 主菜单.*?(?=\n@router|\Z)', re.DOTALL)
code = pattern.sub('', code)

# 顺便清理掉之前可能残留的其他 start
code = re.sub(r'@router\.message\(Command\("start"\)\)\nasync def .*?[\s\S]*?(?=\n@router|\Z)', '', code, flags=re.DOTALL)
code = re.sub(r'@router\.message\(F\.text == "/start"\)\nasync def .*?[\s\S]*?(?=\n@router|\Z)', '', code, flags=re.DOTALL)

# 2. 注入最终的完美面板
perfect_start = """

@router.message(Command("start"))
async def cmd_start(msg: Message):
    web_app_url = "https://vcar.v2net.cc/mini/"
    
    # 先发送一条消息，强制清空底部的回复键盘 (ReplyKeyboardRemove)
    await msg.answer("🔄 正在重置菜单...", reply_markup=ReplyKeyboardRemove())
    
    # 构建内联键盘 (InlineKeyboard)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        # 第一排：WebApp 主页按钮
        [InlineKeyboardButton(text="👤 进入我的主页 (小程序)", web_app=WebAppInfo(url=web_app_url))],
        
        # 第二排：上线/下线状态管理
        [
            InlineKeyboardButton(text="🟢 上线接单", callback_data="driver_go_online"),
            InlineKeyboardButton(text="🔴 下线休息", callback_data="driver_go_offline")
        ],
        
        # 第三排：订单与语言
        [
            InlineKeyboardButton(text="📋 订单", callback_data="driver_current_order"),
            InlineKeyboardButton(text="🌐 语言", callback_data="driver_language")
        ]
    ])
    
    welcome_text = (
        "🚖 **欢迎来到 Vcar 司机端！**\\n\\n"
        "请点击下方按钮操作："
    )
    
    await msg.answer(welcome_text, reply_markup=kb, parse_mode="Markdown")
"""

if not code.endswith("\n"):
    code += "\n"
code += perfect_start

with open(filepath, "w", encoding="utf-8") as f:
    f.write(code)
print("✅ 已彻底清除旧代码，并注入全新控制面板！")
