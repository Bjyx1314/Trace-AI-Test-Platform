"""Runner 工厂 —— 把路由类型 + 配置开关解析成具体 Runner 实例。

激进路线纪律：架构支持全部六端，但只有「已实现且开关开启」的 Runner 才真实执行；
其余（开关关闭 / 尚未实现）一律回退 MockRunner，保证执行链路永不阻塞。
"""
from __future__ import annotations

from typing import Any

from .base import BaseRunner, RunOutcome, RunContext
from .api_runner import ApiRunner
from .web_runner import WebRunner  # noqa: F401 (脚本型，保留备用)
from .web_agent_runner import WebAgentRunner
from .android_runner import AndroidAgentRunner  # noqa: F401 (worker 端本地执行用；平台侧用 WorkerDispatchRunner 派发)
from .worker_dispatch_runner import WorkerDispatchRunner
from .mock_runner import MockRunner
from .dispatcher import resolve_runner_type


class _UnavailableRunner(BaseRunner):
    """端执行环境未就绪且禁止 mock(服务器真实环境)时使用：产出真实的 env_error 结果，绝不伪造数据。"""
    def __init__(self, reason: str):
        self._reason = reason

    async def run(self, case: Any, ctx: RunContext) -> RunOutcome:
        return RunOutcome(status="error", duration_ms=0, error_message=self._reason, failure_type="env_error")

    def _prepare(self, case: Any, ctx: RunContext):
        raise NotImplementedError

    async def _execute(self, workdir, case: Any, ctx: RunContext) -> dict:
        raise NotImplementedError


def _fallback(reason: str, cfg: Any) -> BaseRunner:
    """未就绪端的回退：本地用 MockRunner(假执行)；服务器真实环境用 UnavailableRunner(报错)。"""
    if getattr(cfg, "mock_allowed", False):
        return MockRunner()
    return _UnavailableRunner(reason)

# 已实现真实执行的 Runner（其余端在 P5 逐个补充后登记到这里）
_IMPLEMENTED: dict[str, type[BaseRunner]] = {
    "api": ApiRunner,
    "web": WebAgentRunner,             # AI 视觉驱动浏览器执行 PC web 用例
    "android": WorkerDispatchRunner,   # 派发给执行机 worker，worker 端用 AndroidAgentRunner 真连真机
}

# 各端开关在 settings 上的属性名
_ENABLE_FLAG = {
    "api": "runner_api_enabled",
    "web": "runner_web_enabled",
    "android": "runner_android_enabled",
    "ios": "runner_ios_enabled",
    "harmony": "runner_harmony_enabled",
    "miniprogram": "runner_miniprogram_enabled",
}


def _get_cfg():
    from app.config import settings
    return settings


def build_runner(case: Any, cfg: Any = None, platform_group_map: dict[str, str] | None = None) -> BaseRunner:
    """根据用例与配置返回应使用的 Runner 实例。

    回退到 MockRunner 的条件（任一）：
    - execution_mode != "real"
    - 该端开关未开启
    - 该端尚未实现真实 Runner
    """
    cfg = cfg or _get_cfg()

    if getattr(cfg, "execution_mode", "mock") != "real":
        return _fallback("执行引擎未启用真实执行(execution_mode≠real)", cfg)

    runner_type = resolve_runner_type(case, platform_group_map)

    flag_attr = _ENABLE_FLAG.get(runner_type)
    if not flag_attr or not getattr(cfg, flag_attr, False):
        return _fallback(f"「{runner_type}」端执行未开启或不支持，无法真实执行", cfg)

    runner_cls = _IMPLEMENTED.get(runner_type)
    if runner_cls is None:
        return _fallback(f"「{runner_type}」端真实 Runner 尚未实现", cfg)

    return runner_cls()
