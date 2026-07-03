"""ApiRunner 真实执行测试 —— 真起 pytest 子进程跑脚本，验证全链路。

不依赖外部 HTTP 服务：用最小的 assert True/False 脚本验证
"脚本落盘 → pytest --json-report 子进程 → 解析 RunOutcome" 这条真实链路。
"""
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.runners.api_runner import ApiRunner  # noqa: E402
from app.services.runners.base import RunContext  # noqa: E402


class _Case:
    def __init__(self, script, title="demo", case_id="TC-API-0001"):
        self.script = script
        self.title = title
        self.case_id = case_id


def _run(case):
    runner = ApiRunner()
    ctx = RunContext(execution_id="exec-test")
    return asyncio.run(runner.run(case, ctx))


PASS_SCRIPT = "def test_ok():\n    assert 1 + 1 == 2\n"
FAIL_SCRIPT = "def test_bad():\n    assert 1 + 1 == 3\n"
BROKEN_SCRIPT = "import nonexistent_module_xyz\n\ndef test_x():\n    assert True\n"


def test_api_runner_passed():
    out = _run(_Case(PASS_SCRIPT))
    assert out.status == "passed"
    assert out.duration_ms >= 0
    assert out.error_message is None


def test_api_runner_failed_is_real_defect():
    out = _run(_Case(FAIL_SCRIPT))
    assert out.status == "failed"
    assert out.failure_type == "real_defect"
    assert "assert" in (out.error_message or "").lower()


def test_api_runner_broken_script_is_error():
    # import 失败 → 收集阶段报错 → script_error
    out = _run(_Case(BROKEN_SCRIPT))
    assert out.status == "error"
    assert out.failure_type == "script_error"


def test_api_runner_platform_and_no_device():
    runner = ApiRunner()
    assert runner.platform == "api"
    assert runner.requires_device is False
