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

确认 OSRM 健康并完成 Route/Table 冒烟测试后，再在 Vcar 的 `.env` 中设置 `OSRM_ENABLED=true` 并重启 Bot/Web。未启用时保留旧版距离估算兼容模式；启用后价格使用 OSRM 道路距离，派单优先使用 OSRM Table ETA。OSRM 故障时派单排序可回退为 GEO 距离，但计价不会静默退回直线距离。\n\n检查路由服务：

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


## 平台能力扩展（本批次）

### PostGIS 与司机实时定位

- 新迁移 `20261112_0002` 安装 PostGIS 扩展，并创建 `driver_locations`、`trip_tracks`、钱包和交易账本表。
- 安装器会以 PostgreSQL 管理员身份启用 PostGIS；应用数据库账号仍为普通权限账号。
- 司机使用 Telegram WebApp 换取的短期 JWT 调用 `POST /api/v1/driver/location`。采样必须包含时区，坐标、精度、方向、速度和时间范围均会校验。
- 司机位置通过 Redis Pub/Sub 推送到 `/api/v1/driver/location/stream`。浏览器 WebSocket 目前通过短期 JWT query 参数认证；反向代理必须对该路径关闭 query-string 访问日志，避免记录令牌。
- 管理后台可通过 `GET /api/admin/fleet` 获取司机位置和 120 秒在线判定。派单会清理超时心跳，避免长期离线司机继续进入候选集。

### 乘客预约与司机导航

- 乘客 Telegram WebApp 使用 `POST /api/passenger/session` 建立会话，预约接口为 `POST /api/passenger/orders`。
- 司机导航接口为 `POST /api/v1/driver/navigation/route`，路线匹配接口为 `POST /api/v1/driver/navigation/match`。
- 这些接口依赖 OSRM。启用 OSRM 前必须完成柬埔寨地图预处理、健康检查和 Route/Table 冒烟测试。

### 钱包与支付边界

- `wallet_accounts` 与 `wallet_transactions` 是最小账本基础；金额使用整数最小货币单位，交易带唯一幂等键，余额禁止透支。
- 钱包账本服务尚未连接 ABA/Bakong 或其他支付渠道。不得把未验证的回调直接记账；必须先完成渠道签名/来源验证、金额与币种核对、订单状态核对，再调用账本服务。
- 当前不包含真实支付收款、退款、提现、分账或会计对账流程。

### 健康检查、备份与恢复

- `GET /health/live` 是进程存活检查；`GET /health/ready` 同时检查 PostgreSQL、PostGIS 和 Redis。
- 数据库备份：`bash scripts/backup_database.sh`。脚本验证 dump 可读后才原子移动为正式备份，默认保留 14 天。
- 数据库恢复：先验证备份文件，再执行 `CONFIRM_RESTORE=YES bash scripts/restore_database.sh /path/to/vcar-backup.dump`。恢复会停止 Bot/Web 服务并覆盖数据库对象，必须先另做当前库备份。
- 以上脚本只备份 PostgreSQL 数据；媒体文件、上传目录、OSRM 地图数据和服务器密钥需要单独纳入主机级备份策略。


## Runtime and dependency baseline (November 2026)

- Python support target: 3.12–3.14; CI validates 3.13 and 3.14. Python dependencies are resolved in `uv.lock`; `requirements.txt` is an exported, fully pinned runtime install manifest.
- Frontend build target: Node.js 26 LTS and npm 11; Vue 3.5, Vue Router 4, Element Plus 2.14, Vite 8 and `@vitejs/plugin-vue` 6. The npm lockfile is generated against the official npm registry.
- CI uses current major versions of the official checkout/setup actions and PostGIS 3.7 on PostgreSQL 16.
- PostgreSQL major-version upgrades are deliberately not performed automatically. Existing production databases require a separate backup/restore and compatibility rehearsal before any PostgreSQL or PostGIS major upgrade. Moving an existing PostGIS database from older Debian/glibc/PostGIS image families to PostGIS 3.7 requires a dump/restore plan; do not simply point a 3.7 image at an old data directory.
- OSRM remains pinned to upstream v5.27.1 until route/table/match integration tests prove compatibility with a newer engine.
- Production rollout requires staged dependency installation, database backup, migration dry-run, health checks and a rollback window. This repository change does not update any running host.
