#!/bin/bash
set -e

# 必须 root 权限
if [ "$EUID" -ne 0 ]; then
  echo "❌ 请使用 root 权限运行此脚本 (sudo bash deploy/install.sh)"
  exit 1
fi

echo "========================================="
echo "🚀 开始全新安装 Vcar V2 终极商业版系统"
echo "========================================="

# 1. 安装系统依赖
export DEBIAN_FRONTEND=noninteractive
echo "📦 安装系统依赖 (PostgreSQL, Redis, Python, Node.js)..."
apt-get update -qq

# 安装 Node.js 20 用于编译 Vue3 前端
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null 2>&1
apt-get install -y -qq postgresql redis-server python3 python3-venv python3-pip curl nodejs > /dev/null 2>&1

systemctl enable --now postgresql redis-server

# 2. 配置数据库
echo "🗄️ 配置 PostgreSQL 数据库..."
su - postgres -c "psql -c \"CREATE USER vcar WITH PASSWORD 'vcar';\"" > /dev/null 2>&1 || true
su - postgres -c "createdb -O vcar vcar" > /dev/null 2>&1 || true

# 获取项目根目录
PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd $PROJECT_DIR

# 3. 初始化 Python 环境
echo "🐍 初始化 Python 虚拟环境..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

# 4. 生成环境变量配置文件
if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️ 警告：已生成 $PROJECT_DIR/.env 模板。"
    echo "👉 请务必先修改 $PROJECT_DIR/.env 填入真实的 Bot Token 和数据库密码！"
fi

# 5. 初始化数据库表结构
echo "⚙️ 初始化数据表..."
PGPASSWORD=vcar psql -h 127.0.0.1 -U vcar -d vcar -f app/db/schema.sql > /dev/null

# 6. 编译前端 Vue3 后台 (增加内存限制，防止低配服务器 OOM)
echo "🎨 编译 Vue3 前端后台..."
if [ -d "web_frontend" ]; then
    cd web_frontend
    if [ ! -d "node_modules" ]; then
        npm install --silent
    fi
    # 限制 Node 内存，防止 1G 小内存服务器卡死
    export NODE_OPTIONS="--max-old-space-size=1024"
    npm run build > /dev/null 2>&1
    cd $PROJECT_DIR
    rm -rf app/web/static
    cp -r web_frontend/dist app/web/static
else
    echo "⚠️ 未找到 web_frontend 目录，跳过前端编译。"
fi

# 7. 配置 Systemd 守护服务 (带优雅退出超时保护，防卡死)
echo "🚀 配置 Systemd 守护服务..."
cat > /etc/systemd/system/vcar-bot.service << EOS
[Unit]
Description=Vcar Telegram Bot
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/.venv/bin/python -m app.bot_main
Restart=always
RestartSec=5
TimeoutStopSec=5
KillMode=mixed
SendSIGKILL=yes

[Install]
WantedBy=multi-user.target
EOS

cat > /etc/systemd/system/vcar-web.service << EOS
[Unit]
Description=Vcar Web Dashboard
After=network.target postgresql.service redis-server.service

[Service]
Type=simple
User=root
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/.venv/bin/uvicorn app.web.server:app --host 0.0.0.0 --port 8080
Restart=always
RestartSec=5
TimeoutStopSec=5
KillMode=mixed
SendSIGKILL=yes

[Install]
WantedBy=multi-user.target
EOS

systemctl daemon-reload
systemctl enable vcar-bot vcar-web

echo "========================================="
echo "✅ 安装完成！"
echo "接下来的 3 步必须执行："
echo "1. 修改配置： nano $PROJECT_DIR/.env"
echo "2. 启动服务： systemctl restart vcar-bot vcar-web"
echo "3. 访问后台： http://服务器IP:8080/admin/ (默认账号 admin / 密码 vcar888)"
echo "========================================="
