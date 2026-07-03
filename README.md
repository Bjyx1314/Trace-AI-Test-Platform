# TraceAI Test Platform

一套面向团队的 AI 自动化测试平台，覆盖需求分析、测试用例生成、接口/Web/App 执行、缺陷复核与质量门禁。项目采用 FastAPI、React、PostgreSQL、Redis、Playwright 与 Android 真机执行助手构建。

> 当前项目处于早期阶段，建议先在隔离的测试环境中评估。不要将生产密钥、真实客户数据或登录态提交到仓库。

## 功能

- AI 辅助需求分析、测试点拆解和测试用例生成
- 测试用例库、评审、版本记录与执行历史
- 接口直连、PC Web、Android 真机与 Sonic 云真机执行能力
- App 测试包下载、卸载旧包与安装指定版本的扩展接口
- 缺陷诊断、人工复核和质量门禁
- 可选飞书、外部 SSO/任务系统和外部自动化框架集成
- 枚举驱动的产品线、模块、端和环境地址配置

## 技术架构

```text
Browser -> React/Nginx -> FastAPI -> PostgreSQL
                            |  |
                            |  +-> Redis/RQ
                            +----> AI provider / Playwright / Sonic
                            +----> Windows/macOS worker -> Android device
```

详细说明见 [架构文档](docs/architecture.md)。
版本变化见 [CHANGELOG](CHANGELOG.md)。

## 快速启动

要求：Docker Engine 24+、Docker Compose v2，建议至少 4 核 CPU、8 GB 内存。

```bash
cp .env.example .env
cp backend/.env.example backend/.env
```

首次本地体验可直接启动；默认登录账号和密码均为 `admin`：

```bash
docker compose -f docker-compose.prod.yml up -d --build
```

访问 `http://localhost`，使用 `admin` / `admin` 登录。首次启动会自动执行数据库迁移、写入通用枚举，并在项目表为空时创建“示例项目”。

示例项目只提供页面操作所需的项目上下文和质量门禁配置，不包含虚假需求、用例或执行结果；已有项目不会被覆盖。

> `admin` / `admin` 仅用于首次本地登录。部署到共享网络或生产环境前，必须在 `.env` 中修改 `DEFAULT_ADMIN_PASSWORD`，并同时替换数据库密码和 `JWT_SECRET`。

更多方式见 [快速开始](docs/quick-start.md) 和 [生产部署](docs/deployment.md)。

## 配置原则

- `.env`、登录态、上传文件、APK/IPA、数据库和构建产物均已加入 `.gitignore`。
- 仓库不包含任何真实 API Key、密码、内网地址、客户名称或业务包。
- 外部系统默认关闭，只有显式设置环境变量后才启用。
- AI 输出不能替代人工判断；高风险缺陷和发布门禁应保留人工复核。

完整变量见 [配置说明](docs/configuration.md)。

## 开发

```bash
# 后端
cd backend
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
python -m pytest -q

# 前端
cd frontend
npm ci
npm run build
```

提交代码前请阅读 [贡献指南](CONTRIBUTING.md) 和 [安全政策](SECURITY.md)。

## 许可证

项目采用 [Apache License 2.0](LICENSE)。第三方组件仍受各自许可证约束。
