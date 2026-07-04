# 快速开始

这份文档面向第一次接触开源版的使用者，目标是尽快把平台跑起来并成功登录。

This guide is for first-time users of the open-source edition. Its goal is to help you get the platform running and sign in successfully as quickly as possible.

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

English quick steps:

1. Install Docker Engine and Docker Compose v2.
2. Copy `.env.example` to `.env` and `backend/.env.example` to `backend/.env`.
3. Run `docker compose -f docker-compose.prod.yml up -d --build`.
4. Open `http://localhost` and sign in with `admin` / `admin`.

默认凭据只用于降低本地首次启动门槛。平台接入共享网络前必须覆盖默认密码；已经创建的管理员不会因后续修改环境变量而自动重置密码。

首次启动时，如果数据库中还没有项目，平台会自动创建“示例项目”（用例前缀 `DEMO`）。它不包含需求、用例或执行结果，可在项目设置中直接改名或删除；已有项目不会被修改。

首次启动还会自动写入一套通用枚举，用于环境、端、模块等基础配置。开源版不会自动生成业务脚本、业务需求或业务缺陷数据。

## 建议体验路径

1. 登录后先确认首页、项目列表和系统设置可以正常打开。
2. 进入“示例项目”，确认项目基础信息、质量门禁和空白列表页均可正常访问。
3. 如需 AI 能力，再补充 `backend/.env` 中的 `AI_PROVIDER`、`AI_MODEL` 和对应 API Key。
4. 如需真机、Sonic 或外部系统，再按需打开对应集成配置。

如果你只是想快速确认“这个项目能不能跑起来”，做到第 2 步就够了；后续配置可以等验证通过后再补。

## 页面参考

### 登录页

![登录页预览](images/login-page.png)

### 首次进入后可重点查看

![需求列表预览](images/requirements-page.png)

![质量看板预览](images/dashboard-page.png)

## 默认演示数据说明

| 项目 | 默认内容 |
|---|---|
| 管理员账号 | `admin` / `admin` |
| 项目数据 | 自动创建一个“示例项目” |
| 枚举数据 | 自动写入通用枚举配置 |
| 需求/用例/执行记录 | 默认不生成 |
| AI 模型 | 默认留空，需要手工配置 |

这样设计是为了保证开源版不依赖任何业务资产，同时让首次体验仍然可以顺利进入各个页面。

## 生产或共享环境前必改

1. 修改根目录 `.env` 中的 `POSTGRES_PASSWORD`。
2. 修改 `backend/.env` 中的 `JWT_SECRET`。
3. 修改 `DEFAULT_ADMIN_PASSWORD`，不要继续使用 `admin`。
4. 按需配置 AI、Sonic、飞书或外部系统；不用的保持留空。
5. 为数据库、上传目录和执行产物目录配置备份。

这些变量的详细说明见 [配置说明](configuration.md)，部署基线见 [生产部署](deployment.md)。

English note:
Before using the platform in production or any shared environment, make sure you change `POSTGRES_PASSWORD`, `JWT_SECRET`, and `DEFAULT_ADMIN_PASSWORD`, and configure backups for the database and uploaded artifacts.

## 常见问题

### 1. 为什么输入 `admin` / `admin` 还是登录失败？

默认管理员只会在首次初始化且库里不存在管理员账号时创建。如果你之前已经启动过平台，数据库里可能已经有旧账号或旧密码。

### 2. 为什么登录后只有一个示例项目？

这是开源版的默认最小数据集，只提供进入系统和验证流程所需的基本上下文，不包含任何业务需求或测试资产。

### 3. 不配置 AI 可以先用吗？

可以。页面浏览、本地登录、项目和基础配置管理不依赖 AI；只有依赖 AI 的分析、生成或真实执行能力需要补充模型配置。

### 4. 修改了环境变量，为什么管理员密码没变？

默认管理员创建完成后，后续修改环境变量不会自动重置已有账号密码，这样可以避免覆盖真实使用中的管理员账号。

## 下一步看什么

- 想配 AI、Sonic 或外部系统：看 [配置说明](configuration.md)
- 想了解系统内部结构：看 [架构文档](architecture.md)
- 想部署到服务器：看 [生产部署](deployment.md)

For English readers:

- To configure AI, Sonic, or external systems, read [configuration.md](configuration.md)
- To understand the system structure, read [architecture.md](architecture.md)
- To deploy the platform to a server, read [deployment.md](deployment.md)

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
