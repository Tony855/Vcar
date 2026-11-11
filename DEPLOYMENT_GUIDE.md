# Vcar V2 部署与运维指南

系统采用 Python（FastAPI + Aiogram）、PostgreSQL、Redis 和 Vue 3。OSRM 路由服务作为独立基础设施部署，不随应用安装脚本自动下载地图数据。

## 首次安装

1. 克隆代码并进入仓库目录。
2. 执行 `sudo bash deploy/install.sh`。
3. 编辑项目目录中的 `.env`，配置两个 Telegram Bot Token、数据库连接和 Redis 连接。
4. 生成 JWT 密钥：

   ```bash
   openssl rand -hex 32
   ```

   将输出写入 `SECRET_KEY`。

5. 生成管理员密码哈希（密码至少 12 位）：

   ```bash
   .venv/bin/python -c "from app.core.security import hash_password; print(hash_password('替换为强密码'))"
   ```

   将输出写入 `ADMIN_PASSWORD_HASH`，并设置 `ADMIN_USERNAME`。不要把明文密码写入仓库或工单。

6. 如前后端使用不同源，设置 `CORS_ORIGINS` 为明确的可信来源列表；不要使用通配符。
7. 启动服务：`systemctl restart vcar-bot vcar-web`。

安装脚本会在导入现有基础 schema 后执行 Alembic 迁移，创建订单事件历史表。已有部署升级时，应先备份，再手动执行 `.venv/bin/alembic upgrade head`。\n\n旧版本的硬编码管理员密码已移除。未配置 `SECRET_KEY` 和 `ADMIN_PASSWORD_HASH` 时，管理后台登录会拒绝服务；必须先完成密钥初始化再切换新版本。

## OSRM（柬埔寨）部署

OSRM 使用 MLD 算法，监听端口默认只绑定到 127.0.0.1:5000。地图文件和预处理产物不会提交到 Git。

```bash
bash scripts/download_map_data.sh
docker compose -f docker-compose.osrm.yml --profile prepare run --rm osrm-prepare
docker compose -f docker-compose.osrm.yml up -d osrm
```

检查路由服务：

```bash
curl 'http://127.0.0.1:5000/route/v1/driving/104.9282,11.5564;104.9500,11.5700?overview=false'
```

地图数据更新前应先在独立目录下载并重新预处理，验证成功后再切换数据版本。OSRM 不提供实时交通拥堵信息；ETA 基于当前道路图和 profile 速度。

## 日常运维

- Bot 日志：`journalctl -u vcar-bot -f`
- Web 日志：`journalctl -u vcar-web -f`
- 前端构建：进入 `web_frontend` 执行 `npm ci && npm run build`，然后按发布流程更新静态文件。
- OSRM 日志：`docker compose -f docker-compose.osrm.yml logs -f osrm`

## 迁移安全要求

- 不要在没有备份和恢复演练的情况下执行生产数据库迁移。
- 旧的 `app/db/schema.sql` 在兼容迁移阶段继续保留。
- Alembic 的生产迁移脚本必须经过审查，并在数据库副本上验证 upgrade/downgrade。
- 司机 WebApp 私有资料接口需要由 Telegram WebApp initData 换取短期司机 JWT；不得仅凭 URL 中的 Telegram ID 访问。
