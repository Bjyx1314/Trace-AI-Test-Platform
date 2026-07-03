"""WebRunner —— PC web 自动化真实执行（Playwright + pytest 子进程，详设 P2）。

与 ApiRunner 同构（都跑 pytest --json-report 子进程），差异仅在：
- 脚本是 Playwright 用例（用 pytest-playwright 提供的 page fixture）
- pytest 命令追加 --browser chromium，默认 headless
"""
from __future__ import annotations

import re
import tempfile
from pathlib import Path
from typing import Any

from .base import (
    BaseRunner,
    RunContext,
    RunOutcome,
    parse_pytest_subprocess_outcome,
    run_pytest_subprocess,
)

_DEFAULT_TIMEOUT = 180  # web 用例含浏览器启动，超时给得比接口宽


def _safe_name(case: Any) -> str:
    raw = getattr(case, "case_id", None) or getattr(case, "title", None) or "case"
    return re.sub(r"[^0-9A-Za-z_]+", "_", str(raw))[:40] or "case"


class WebRunner(BaseRunner):
    platform = "web"
    requires_device = False

    def __init__(self, timeout_sec: int = _DEFAULT_TIMEOUT, headed: bool = False):
        self.timeout_sec = timeout_sec
        self.headed = headed

    def _prepare(self, case: Any, ctx: RunContext) -> Path:
        script = getattr(case, "script", None)
        tmp = Path(tempfile.mkdtemp(prefix="webrun_"))
        test_file = tmp / f"test_{_safe_name(case)}.py"
        if not script or not script.strip():
            script = (
                "def test_missing_script(page):\n"
                "    assert False, '用例尚未生成可执行脚本'\n"
            )
        test_file.write_text(script, encoding="utf-8")
        return tmp

    async def _execute(self, workdir: Path, case: Any, ctx: RunContext) -> dict:
        extra = ["--browser", "chromium"]
        if self.headed:
            extra.append("--headed")
        return await run_pytest_subprocess(workdir, extra_args=extra, timeout_sec=self.timeout_sec)

    def _parse(self, report: dict) -> RunOutcome:
        return parse_pytest_subprocess_outcome(report)
