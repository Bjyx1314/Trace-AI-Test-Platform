"""MockRunner —— 符合 BaseRunner 接口的兜底 Runner（单用例）。

与旧的 services/mock_runner.MockExecutionRunner（整批执行+落库+门禁）不同：
本 Runner 只对「单条用例」产出一个随机 RunOutcome，供队列层在目标端 Runner
环境未就绪（RUNNER_*_ENABLED=false 或缺设备）时回退使用，保证平台始终可跑通链路。
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .base import BaseRunner, RunContext, RunOutcome


class MockRunner(BaseRunner):
    platform = "mock"
    requires_device = False
    cleanup_workdir = False  # 不产生临时目录

    def _prepare(self, case: Any, ctx: RunContext) -> Path:
        return Path(".")

    async def _execute(self, workdir: Path, case: Any, ctx: RunContext) -> dict:
        # 用 case_id 派生确定性的伪随机，便于复现（同一用例每次同结果）
        seed = getattr(case, "case_id", None) or getattr(case, "title", "") or "case"
        h = int(hashlib.md5(str(seed).encode()).hexdigest(), 16)
        roll = (h % 100) / 100.0
        if roll < 0.75:
            status = "passed"
        elif roll < 0.90:
            status = "failed"
        else:
            status = "skipped"
        return {"_mock_status": status, "_mock_duration_ms": 100 + (h % 1900)}

    def _parse(self, report: dict) -> RunOutcome:
        status = report.get("_mock_status", "passed")
        return RunOutcome(
            status=status,
            duration_ms=report.get("_mock_duration_ms", 100),
            error_message="AssertionError: expected value mismatch (mock)" if status == "failed" else None,
            failure_type="real_defect" if status == "failed" else None,
            raw_report={"mock": True},
        )
