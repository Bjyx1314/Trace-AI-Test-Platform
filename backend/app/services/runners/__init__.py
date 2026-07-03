"""Runner 抽象层包：统一执行契约 + 路由 + 各端 Runner。

包级再导出改为懒加载(PEP 562 __getattr__)：仅导入单个 runner(如执行机 worker 只用
base / android_runner)时不再被动拉入 factory→web_agent_runner→playwright 等重依赖，便于 worker 瘦身打包。
"""

_LAZY = {
    "BaseRunner": ".base",
    "RunContext": ".base",
    "RunOutcome": ".base",
    "parse_pytest_report": ".base",
    "parse_pytest_subprocess_outcome": ".base",
    "run_pytest_subprocess": ".base",
    "resolve_runner_type": ".dispatcher",
    "ApiRunner": ".api_runner",
    "WebRunner": ".web_runner",
    "MockRunner": ".mock_runner",
    "build_runner": ".factory",
}

__all__ = list(_LAZY.keys())


def __getattr__(name):
    mod = _LAZY.get(name)
    if mod:
        import importlib
        m = importlib.import_module(mod, __name__)
        return getattr(m, name)
    raise AttributeError(f"module 'app.services.runners' has no attribute {name!r}")
