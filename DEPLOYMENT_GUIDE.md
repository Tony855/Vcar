# Vcar V2 部署与运维指南

本系统采用 Python (FastAPI + Aiogram) 作为后端，PostgreSQL 作为主数据库，Redis 作为缓存，Vue3 作为前端。

## 🚀 一键安装
1. 克隆代码：`git clone git@github.com:Tony855/Vcar.git && cd Vcar`
2. 运行安装：`sudo bash deploy/install.sh`
3. 配置 Token：编辑 `.env` 填入 `DRIVER_BOT_TOKEN` 和 `PASSENGER_BOT_TOKEN`
4. 启动服务：`systemctl restart vcar-bot vcar-web`

## 🛠️ 日常运维
- 查看 Bot 日志：`journalctl -u vcar-bot -f`
- 查看 Web 日志：`journalctl -u vcar-web -f`
- 更新代码后必做：进入 `web_frontend` 执行 `npm run build`，然后 `cp -r dist ../app/web/static`，最后重启服务。

## 📱 小程序配置
需配置 Nginx 反向代理到 8080 端口并申请 SSL 证书，然后在 BotFather 设置菜单 URL 为 `https://你的域名/mini/`。
