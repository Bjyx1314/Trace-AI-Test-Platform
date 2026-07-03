# 架构说明

## 组件

- `frontend`：React、TypeScript、Ant Design，提供需求、用例、执行、缺陷与系统配置界面。
- `backend`：FastAPI 与 SQLAlchemy，负责业务 API、AI 编排、执行调度和结果归档。
- PostgreSQL：保存项目、需求、用例、执行结果、枚举和系统设置。
- Redis/RQ：承载异步执行任务；本地开发也可启用进程内执行。
- Runner：接口、Web、Android 和 Sonic 远程真机执行器，共用统一 `RunOutcome`。
- Worker：运行在连接 Android 设备的 Windows/macOS 主机，主动领取任务并回传结果。

## 执行链路

1. 用户选择用例、环境与可选设备。
2. 后端创建执行批次，根据用例类型选择 Runner。
3. 接口 Runner 发送真实 HTTP 请求；Web Runner 使用 Playwright；App Runner 派发至 worker 或 Sonic。
4. Runner 返回统一结果、请求轨迹、截图与失败类型。
5. 平台计算通过率和质量门禁，并进入缺陷人工复核。

## 扩展点

- `backend/app/agents`：AI Provider 与提示词。
- `backend/app/services/runners`：新增执行端。
- `backend/app/services/app_packages.py`：接入 Jenkins、Nexus、MinIO 或对象存储。
- `FRAMEWORK_*` 环境变量：挂载团队已有自动化框架。
- 枚举管理：扩展产品线、模块、端、环境和基础 URL。
