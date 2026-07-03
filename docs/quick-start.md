# 快速开始

## Docker Compose

1. 安装 Docker Engine 与 Docker Compose v2。
2. 复制配置模板：

```bash
cp .env.example .env
cp backend/.env.example backend/.env
```

3. 本地首次体验可保留默认管理员账号和密码 `admin` / `admin`；共享或生产部署必须修改 `POSTGRES_PASSWORD`、`JWT_SECRET` 和 `DEFAULT_ADMIN_PASSWORD`。
4. 如需 AI 能力，在 `backend/.env` 设置 `AI_PROVIDER` 和对应 API Key。
5. 启动：

```bash
docker compose -f docker-compose.prod.yml up -d --build
docker compose -f docker-compose.prod.yml ps
```

6. 打开 `http://localhost`，首次使用 `admin` / `admin` 登录。

默认凭据只用于降低本地首次启动门槛。平台接入共享网络前必须覆盖默认密码；已经创建的管理员不会因后续修改环境变量而自动重置密码。

首次启动时，如果数据库中还没有项目，平台会自动创建“示例项目”（用例前缀 `DEMO`）。它不包含需求、用例或执行结果，可在项目设置中直接改名或删除；已有项目不会被修改。

## 本地开发

先启动 PostgreSQL 与 Redis：

```bash
docker compose up -d
```

后端：

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

前端：

```bash
cd frontend
npm ci
npm run dev
```

开发地址为 `http://localhost:5173`，API 默认通过 Vite/Nginx 配置访问后端。
