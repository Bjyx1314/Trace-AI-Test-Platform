# 生产部署

## 建议拓扑

将 Nginx/前端、FastAPI、PostgreSQL 和 Redis 部署在受控网络；Android worker 或 Sonic Agent 通过内网访问平台。公网入口只暴露 HTTPS。

## 部署步骤

1. 创建专用部署用户与目录，例如 `/opt/ai-test-platform`。
2. 复制 `.env.example` 和 `backend/.env.example`，生成独立随机密钥。
3. 配置域名、TLS 和反向代理；限制 `/api/worker` 的来源网络。
4. 运行 `docker compose -f docker-compose.prod.yml up -d --build`。
5. 检查 `/api/health` 或 `/health`、容器状态和迁移日志。
6. 登录后立即验证管理员账号，并按需配置 AI、枚举和外部系统。

## 更新

更新前备份 PostgreSQL 和上传卷。可以使用 `deploy/update.py`，但必须显式设置 `DEPLOY_HOST`；脚本不会上传本地 `.env`。

## 备份

至少备份 PostgreSQL、`backend_uploads`、登录态卷和 worker 制品目录。登录态含 Cookie，应加密存储并限制访问。
