import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db

logger = logging.getLogger(__name__)
from app.routers import (
    projects_router,
    requirements_router,
    testcases_router,
    executions_router,
    pipeline_router,
    enums_router,
    dashboard_router,
    cicd_router,
    defects_router,
    page_cache_router,
    auth_router,
    users_router,
    system_settings_router,
    frameworks_router,
    worker_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 确保存在本地管理员（未配置外部 SSO 时可登录）
    try:
        from app.database import AsyncSessionLocal
        from app.services.auth import ensure_default_admin
        async with AsyncSessionLocal() as db:
            await ensure_default_admin(db)
    except Exception:
        pass
    # 空库自动创建通用项目，让所有页面始终有可用的项目上下文。
    try:
        from app.database import AsyncSessionLocal
        from app.seed_default_project import ensure_default_project
        async with AsyncSessionLocal() as db:
            if await ensure_default_project(db):
                logger.warning("已创建开源版示例项目，可在项目设置中改名或删除。")
    except Exception:
        logger.exception("示例项目初始化失败(忽略，不影响启动)")
    # 应用后台配置的 AI 模型设置(覆盖 .env)
    try:
        from app.database import AsyncSessionLocal
        from app.services.app_settings import apply_ai_settings_to_runtime
        async with AsyncSessionLocal() as db:
            await apply_ai_settings_to_runtime(db)
    except Exception:
        pass
    # 回收上次重启遗留的在途任务(分析中/生成中/执行中)，置失败让前端能重试，避免“卡死”
    try:
        from app.database import AsyncSessionLocal
        from app.services.startup_recovery import reset_orphaned_jobs
        async with AsyncSessionLocal() as db:
            await reset_orphaned_jobs(db)
    except Exception:
        logger.exception("启动回收孤儿任务失败(忽略，不影响启动)")
    yield


app = FastAPI(
    title="AI 自动化测试平台",
    description="基于Claude Agent的智能测试平台",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(users_router)
app.include_router(system_settings_router)
app.include_router(projects_router)
app.include_router(requirements_router)
app.include_router(testcases_router)
app.include_router(executions_router)
app.include_router(pipeline_router)
app.include_router(enums_router)
app.include_router(dashboard_router)
app.include_router(cicd_router)
app.include_router(defects_router)
app.include_router(page_cache_router)
app.include_router(frameworks_router)
app.include_router(worker_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "AI Test Platform"}
