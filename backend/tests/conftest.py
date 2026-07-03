"""测试环境引导。

纯逻辑测试（page_cache_service / dashboard_metrics）只依赖标准库与 ORM 模型类，
不需要数据库连接。但 `app.services.__init__` 会拉起 mock_runner→database→config，
在仅安装最小依赖的环境下可能因缺少 pydantic-settings / asyncpg 失败。

这里在 import 期做一次轻量探测：若重型可选依赖缺失，则为受影响的模块提供占位，
使不触达数据库的纯逻辑测试仍可运行；完整环境（含全部 requirements）下不生效。
"""
import sys
import types
from pathlib import Path

# 确保 backend 根目录在 sys.path，使 `import app.*` 可用
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _ensure_pydantic_settings():
    try:
        import pydantic_settings  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    # 最小占位：提供 BaseSettings，仅供 config 模块在无依赖环境下成功导入
    from pydantic import BaseModel

    mod = types.ModuleType("pydantic_settings")

    class BaseSettings(BaseModel):
        model_config = {"extra": "ignore"}

    mod.BaseSettings = BaseSettings
    sys.modules["pydantic_settings"] = mod


def _stub_db_heavy_submodules():
    """纯逻辑测试不触达数据库；当 DB 驱动（asyncpg 等）缺失时，
    为 app.services 包的重型子模块提供占位，使包的 __init__ 可成功导入。

    完整环境（已安装全部 requirements）下 asyncpg 可用，本兜底不触发。
    """
    try:
        import asyncpg  # noqa: F401
        return  # 真实环境，无需 stub
    except ModuleNotFoundError:
        pass

    # 占位 app.database：提供 __init__ 引用到的符号，不建立真实连接
    db_mod = types.ModuleType("app.database")
    db_mod.AsyncSessionLocal = None
    db_mod.engine = None

    async def _get_db():  # pragma: no cover - 占位
        raise RuntimeError("DB not available in minimal test env")

    db_mod.get_db = _get_db
    db_mod.init_db = lambda: None

    # Base 必须是真正的 DeclarativeBase，否则 app.models 中 ORM 类定义会失败
    from sqlalchemy.orm import DeclarativeBase

    class Base(DeclarativeBase):
        pass

    db_mod.Base = Base
    sys.modules["app.database"] = db_mod

    # 占位重型 service 子模块，避免触达 database
    mock_mod = types.ModuleType("app.services.mock_runner")
    mock_mod.MockExecutionRunner = type("MockExecutionRunner", (), {})
    sys.modules["app.services.mock_runner"] = mock_mod

    feishu_mod = types.ModuleType("app.services.feishu")
    feishu_mod.send_feishu_notification = lambda *a, **k: None
    sys.modules["app.services.feishu"] = feishu_mod


_ensure_pydantic_settings()
_stub_db_heavy_submodules()
