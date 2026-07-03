"""Runner 工厂测试：路由类型 + 配置开关 → Runner 实例，未就绪回退 Mock。"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.runners.factory import build_runner, _UnavailableRunner  # noqa: E402
from app.services.runners.api_runner import ApiRunner  # noqa: E402
from app.services.runners.mock_runner import MockRunner  # noqa: E402
from app.services.runners.worker_dispatch_runner import WorkerDispatchRunner  # noqa: E402


class _Cfg:
    """配置替身，仅暴露工厂关心的开关字段。"""
    def __init__(self, mode="real", **flags):
        self.execution_mode = mode
        self.mock_allowed = flags.get("mock_allowed", False)
        self.runner_api_enabled = flags.get("api", True)
        self.runner_web_enabled = flags.get("web", False)
        self.runner_android_enabled = flags.get("android", False)
        self.runner_ios_enabled = flags.get("ios", False)
        self.runner_harmony_enabled = flags.get("harmony", False)
        self.runner_miniprogram_enabled = flags.get("miniprogram", False)


class _Case:
    def __init__(self, case_type="ui", platforms=None):
        self.case_type = case_type
        self.platforms = platforms or []


def test_mock_mode_always_returns_mock():
    r = build_runner(_Case(case_type="api"), cfg=_Cfg(mode="mock", mock_allowed=True))
    assert isinstance(r, MockRunner)


def test_mock_mode_is_error_when_mock_is_forbidden():
    r = build_runner(_Case(case_type="api"), cfg=_Cfg(mode="mock"))
    assert isinstance(r, _UnavailableRunner)


def test_real_api_enabled_returns_api_runner():
    r = build_runner(_Case(case_type="api"), cfg=_Cfg(mode="real", api=True))
    assert isinstance(r, ApiRunner)


def test_real_but_runner_disabled_is_unavailable_in_strict_mode():
    r = build_runner(_Case(platforms=["web"]), cfg=_Cfg(mode="real", web=False))
    assert isinstance(r, _UnavailableRunner)


def test_real_android_enabled_returns_worker_dispatch():
    r = build_runner(_Case(platforms=["android"]), cfg=_Cfg(mode="real", android=True))
    assert isinstance(r, WorkerDispatchRunner)
