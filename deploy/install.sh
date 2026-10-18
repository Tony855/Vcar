#!/bin/bash
set -e
if [ "$EUID" -ne 0 ]; then echo "请使用 root 权限运行"; exit; fi
echo "🚀 开始安装 Vcar 系统环境..."

export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq postgresql redis-server python3 python3-venv python3-pip curl git

systemctl enable --now postgresql redis-server
su - postgres -c "psql -c \"CREATE USER vcar WITH PASSWORD 'vcar';\"" > /dev/null 2>&1 || true
su - postgres -c "createdb -O vcar vcar" > /dev/null 2>&1 || true

PROJECT_DIR=$(cd "$(dirname "$0")/.." && pwd)
cd $PROJECT_DIR

echo "🐍 初始化 Python 环境..."
python3 -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt

if [ ! -f .env ]; then
    cp .env.example .env
    echo "⚠️ 请先修改 $PROJECT_DIR/.env 填入真实的 Token！"
fi

echo "⚙️ 初始化数据表..."
PGPASSWORD=vcar psql -h 127.0.0.1 -U vcar -d vcar -f app/db/schema.sql > /dev/null

echo "🚀 配置 Systemd 服务..."
cat > /etc/systemd/system/vcar-bot.service << EOS
[Unit]
Description=Vcar Telegram Bot
After=network.target postgresql.service redis-server.service
[Service]
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/.venv/bin/python -m app.bot_main
Restart=always
[Install]
WantedBy=multi-user.target
EOS

cat > /etc/systemd/system/vcar-web.service << EOS
[Unit]
Description=Vcar Web Dashboard
After=network.target postgresql.service redis-server.service
[Service]
WorkingDirectory=$PROJECT_DIR
EnvironmentFile=$PROJECT_DIR/.env
ExecStart=$PROJECT_DIR/.venv/bin/uvicorn app.web.server:app --host 0.0.0.0 --port 8080
Restart=always
[Install]
WantedBy=multi-user.target
EOS

systemctl daemon-reload
systemctl enable vcar-bot vcar-web

echo "========================================="
echo "✅ 安装完成！"
echo "1. 修改配置： nano $PROJECT_DIR/.env"
echo "2. 启动服务： systemctl restart vcar-bot vcar-web"
echo "3. 访问后台： http://服务器IP:8080"
echo "========================================="
