"""Runner 抽象层与 Dispatcher 路由的单元测试（纯逻辑，不触达数据库/网络）。

覆盖：
- resolve_runner_type 按 (case_type, platforms) 的路由优先级
- RunOutcome 从 pytest-json-report 字典的解析
- BaseRunner._parse 默认实现
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.runners.base import RunOutcome, parse_pytest_report  # noqa: E402
from app.services.runners.dispatcher import resolve_runner_type  # noqa: E402


class _Case:
    """最小用例替身，仅暴露 dispatcher 关心的两个字段。"""
    def __init__(self, case_type="ui", platforms=None):
        self.case_type = case_type
        self.platforms = platforms or []


# ---------- Dispatcher 路由优先级 ----------

def test_api_case_type_routes_to_api():
    assert resolve_runner_type(_Case(case_type="api")) == "api"


def test_backend_api_platform_routes_to_api_even_if_ui():
    # platforms 含 backend_api → 视为接口，优先级最高
    assert resolve_runner_type(_Case(case_type="ui", platforms=["backend_api"])) == "api"


def test_miniprogram_beats_web():
    assert resolve_runner_type(_Case(platforms=["web", "miniprogram"])) == "miniprogram"


def test_legacy_harmony_routes_to_android_worker():
    assert resolve_runner_type(_Case(platforms=["android", "harmony"])) == "android"


def test_android_routes_to_android():
    assert resolve_runner_type(_Case(platforms=["android"])) == "android"


def test_ios_routes_to_shared_app_worker():
    assert resolve_runner_type(_Case(platforms=["ios"])) == "android"


def test_web_routes_to_web():
    assert resolve_runner_type(_Case(platforms=["web"])) == "web"


def test_empty_platforms_defaults_to_web():
    assert resolve_runner_type(_Case(platforms=[])) == "web"


# ---------- RunOutcome / pytest 报告解析 ----------

def _report(outcome, duration=0.5, longrepr=None):
    """构造一份最小 pytest-json-report 字典。"""
    test = {"outcome": outcome, "call": {"duration": duration}}
    if longrepr:
        test["call"]["longrepr"] = longrepr
    return {"tests": [test], "duration": duration}


def test_parse_passed_report():
    out = parse_pytest_report(_report("passed", duration=0.25))
    assert out.status == "passed"
    assert out.duration_ms == 250
    assert out.error_message is None


def test_parse_failed_report_carries_error():
    out = parse_pytest_report(_report("failed", longrepr="AssertionError: 1 != 2"))
    assert out.status == "failed"
    assert "AssertionError" in out.error_message
    assert out.failure_type == "real_defect"


def test_parse_error_report_is_script_error():
    # collection/setup 错误 → script_error（脚本本身问题，不是被测系统缺陷）
    out = parse_pytest_report(_report("error", longrepr="ImportError: no module"))
    assert out.status == "error"
    assert out.failure_type == "script_error"


def test_parse_empty_report_is_error():
    # 没有任何 test 收集到（脚本无法运行）→ error
    out = parse_pytest_report({"tests": [], "duration": 0})
    assert out.status == "error"
    assert out.failure_type == "script_error"


def test_run_outcome_defaults():
    out = RunOutcome(status="skipped", duration_ms=0)
    assert out.error_message is None
    assert out.screenshot_url is None
    assert out.raw_report is None
