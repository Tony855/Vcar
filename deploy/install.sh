#!/bin/bash
set -e

if [ "$EUID" -ne 0 ]; then
  echo "❌ 请使用 root 权限运行此脚本"
  exit 1
fi

echo "========================================="
echo "🚀 开始全新安装 Vcar V2 系统"
echo "========================================="

export DEBIAN_FRONTEND=noninteractive
echo "📦 安装系统依赖 (PostgreSQL, Redis, Python, Node.js)..."
apt-get update -qq
curl -fsSL https://deb.nodesource.com/setup_20.x | bash - > /dev/null 2>&1
apt-get install -y -qq postgresql redis-server python3 python3-venv python3-pip curl nodejs > /dev/null 2>&1

systemctl enable --now postgresql redis-server

echo "🗄️ 配置 PostgreSQL 数据库..."
su - postgres -c "psql -c \"CREATE USER vcar WITH PASSWORD 'vcar';\"" > /dev/null 2>&1 || true
su - postgres -c "createdb -O vcar vcar" > /dev/null 2>&1 || true

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd $PROJECT_DIR

echo "🐍 初始化 Python 虚拟环境..."
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️ 警告：已生成 .env 模板。请务必先修改 $PROJECT_DIR/.env 填入真实的 Bot Token 和数据库密码！"
fi

echo "⚙️ 初始化数据表..."
PGPASSWORD=vcar psql -h 127.0.0.1 -U vcar -d vcar -f app/db/schema.sql > /dev/null
DATABASE_URL="postgresql://vcar:vcar@127.0.0.1:5432/vcar" .venv/bin/alembic upgrade head

echo "🎨 编译 Vue3 前端后台..."
if [ -d "web_frontend" ]; then
    cd web_frontend
    if [ ! -d "node_modules" ]; then
        npm install --silent
    fi
    export NODE_OPTIONS="--max-old-space-size=1024"
    npm run build > /dev/null 2>&1
    cd $PROJECT_DIR
    rm -rf app/web/static
    cp -r web_frontend/dist app/web/static
fi

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
echo "1. 修改配置： nano $PROJECT_DIR/.env"
echo "2. 启动服务： systemctl restart vcar-bot vcar-web"
echo "3. 访问后台： http://服务器IP:8080/admin/"
echo "========================================="
