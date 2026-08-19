import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import init_db

logger = logging.getLogger(__name__)


class _PollNoiseLogFilter(logging.Filter):
    """屏蔽高频轮询端点的 uvicorn 访问日志：worker claim/heartbeat(每 3~10s) + 前端执行状态轮询
    (GET /api/executions/{id} 每 0.5s)。这些无诊断价值却把真实日志(App 执行/自动登录逐步)冲没。
    仅静音日志、不改行为。注意只匹配带 id 的单条查询(带斜杠)，不影响 GET /api/executions 列表。"""
    _NOISY = ("/api/worker/claim", "/api/worker/heartbeat", "GET /api/executions/")

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            msg = record.getMessage()
        except Exception:
            return True
        return not any(p in msg for p in self._NOISY)


logging.getLogger("uvicorn.access").addFilter(_PollNoiseLogFilter())

# App 执行/自动登录的逐步轨迹、换包(app.services.apk)、执行调度(execution_runner/routers)都是 INFO 级——
# 默认 root=WARNING 会把它们吞掉，排障时看不到。注意这些 logger 分属 app.services.runners / app.services.apk /
# app.routers.* 等【兄弟】命名空间，只给 runners 挂 handler 覆盖不到 apk/换包决策。故直接挂在父级「app」上，
# 一个 INFO handler 覆盖整个 app.* 家族（换包/登录/调度轨迹全可见）。
_app_log = logging.getLogger("app")
_app_log.setLevel(logging.INFO)
if not any(isinstance(h, logging.StreamHandler) for h in _app_log.handlers):
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(levelname)s [%(name)s] %(message)s"))
    _app_log.addHandler(_h)
    _app_log.propagate = False  # 自带 handler 输出，避免再冒泡到 root 造成重复
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
    code_impact_router,
    business_repos_router,
    app_login_recipes_router,
    coverage_router,
    experiences_router,
    graph_router,
    quality_rules_router,
    metrics_router,
    fav_phones_router,
    data_requirements_router,
    data_registries_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 确保存在本地管理员（external task system 不可用时可登录）
    try:
        from app.database import AsyncSessionLocal
        from app.services.auth import ensure_default_admin
        async with AsyncSessionLocal() as db:
            await ensure_default_admin(db)
    except Exception:
        pass
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
    # 启动回收：释放上次被杀/部署打断留下的残留 Sonic 占用(本账号占用却卡 DEBUGGING 的)，避免真机卡死需人工放
    try:
        from app.services.sonic_client import release_self_stale
        _released = await release_self_stale()
        if _released:
            logger.info("启动回收：已释放 %d 台残留占用的 Sonic 真机", _released)
    except Exception:
        logger.warning("启动回收 Sonic 占用失败(忽略)")
    yield
    # 优雅关闭(部署/重启发 SIGTERM，docker 默认等 10s 才 SIGKILL)：释放所有登记在案的 Sonic 占用，
    # 避免执行被打断后 finally 未执行、远程真机卡在 DEBUGGING 需人工释放。
    try:
        from app.services.sonic_client import release_all_occupied
        n = await release_all_occupied()
        if n:
            logger.info("优雅关闭：已释放 %d 台残留占用的 Sonic 真机", n)
    except Exception:
        logger.warning("优雅关闭释放 Sonic 占用失败(忽略)")


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
app.include_router(code_impact_router)
app.include_router(business_repos_router)
app.include_router(app_login_recipes_router)
app.include_router(coverage_router)
app.include_router(experiences_router)
app.include_router(graph_router)
app.include_router(quality_rules_router)
app.include_router(metrics_router)
app.include_router(fav_phones_router)
app.include_router(data_requirements_router)
app.include_router(data_registries_router)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "AI Test Platform"}
