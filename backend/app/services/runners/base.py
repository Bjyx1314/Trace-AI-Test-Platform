"""Runner 抽象层 —— 统一执行契约（详设第 2 章）。

所有端（接口/PC web/Android/iOS/鸿蒙/微信小程序）的执行都收敛到 BaseRunner.run()，
统一产出 RunOutcome，由上层一份逻辑回填 TestResult。子类只负责"准备脚本环境"和
"启动执行引擎"，结果解析尽量复用本模块的 parse_pytest_report。
"""
from __future__ import annotations

import asyncio
import json
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunContext:
    """执行上下文：携带执行引擎需要的环境信息，由 Dispatcher/worker 注入。"""
    execution_id: str
    base_url: str | None = None          # 被测系统地址（接口/web）
    project_id: str | None = None        # 所属项目（执行时自动补充页面结构缓存用）
    device_udid: str | None = None       # 设备标识（移动端/鸿蒙/云真机）
    page_cache: dict | None = None       # 页面结构缓存快照（UI 端定位用）
    extra: dict = field(default_factory=dict)


@dataclass
class RunOutcome:
    """统一执行结果 —— 所有 Runner 的产出，直接映射到 TestResult 字段。"""
    status: str                          # passed/failed/skipped/error
    duration_ms: int
    error_message: str | None = None
    screenshot_url: str | None = None
    failure_type: str | None = None      # script_error/env_error/real_defect
    api_trace: dict | None = None        # 接口用例：{request, response, trace_id}
    ui_trace: list | None = None         # App 真机：分步轨迹 [{seq, action, expected, shots:[url], note}]
    page_captures: list | None = None    # web 执行时抓到的页面结构 [{url, page_name, regions}]，供自动补充缓存
    raw_report: dict | None = None       # 引擎原始报告，便于回溯


async def run_pytest_subprocess(
    workdir: Path,
    extra_args: list[str] | None = None,
    timeout_sec: int = 120,
) -> dict:
    """在 workdir 内跑 `pytest --json-report` 子进程，返回报告 dict。

    Api/Web 等基于 pytest 的 Runner 共用。子进程用 sys.executable（即 venv python），
    确保 worker 环境的依赖一致。超时/无报告时返回带 _timeout/_stdout 的空报告，交解析层判定。
    """
    report_path = workdir / "report.json"
    # pytest-json-report 通过 entrypoint 自动注册，勿用 -p 显式加载（会重复注册报错）
    cmd = [
        sys.executable, "-m", "pytest",
        str(workdir),
        "--json-report",
        f"--json-report-file={report_path}",
        "-q",
        *(extra_args or []),
    ]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(workdir),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "tests": [],
            "duration": timeout_sec,
            "_timeout": True,
            "_stdout": f"执行超时（超过 {timeout_sec}s 已终止）",
        }

    if report_path.exists():
        try:
            return json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"tests": [], "duration": 0, "_stdout": (stdout or b"").decode("utf-8", "ignore")}


async def run_reported_command(
    cwd: Path,
    argv: list[str],
    report_path: Path,
    timeout_sec: int = 300,
    env: dict | None = None,
) -> dict:
    """在 cwd 内执行任意 argv（应已含 --json-report-file=report_path），返回报告 dict。

    供 RepoRunner 在框架仓库 checkout 内跑框架自身 pytest 命令复用；与 run_pytest_subprocess
    的差异：命令完全由调用方给定（含框架特定 marker/参数），cwd 与被测目标解耦。
    """
    import os
    full_env = {**os.environ, **(env or {})}
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=full_env,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_sec)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {"tests": [], "duration": timeout_sec, "_timeout": True,
                "_stdout": f"执行超时（超过 {timeout_sec}s 已终止）"}

    if report_path.exists():
        try:
            return json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"tests": [], "duration": 0, "_stdout": (stdout or b"").decode("utf-8", "ignore")}


def parse_pytest_subprocess_outcome(report: dict) -> RunOutcome:
    """把 run_pytest_subprocess 的报告解析为 RunOutcome（含超时/崩溃归类）。"""
    out = parse_pytest_report(report)
    if report.get("_timeout"):
        out.failure_type = "env_error"
        out.error_message = report.get("_stdout") or out.error_message
    elif out.status == "error" and not out.error_message:
        out.error_message = report.get("_stdout")
    return out


def parse_pytest_report(report: dict) -> RunOutcome:
    """把 pytest-json-report 字典解析为统一 RunOutcome。

    判定规则：
    - 没有任何用例被收集 → error / script_error（脚本根本跑不起来）
    - 单条用例：outcome 直接映射；
      - failed → real_defect（断言失败 = 被测系统问题）
      - error  → script_error（收集/setup 报错 = 脚本本身问题）
    """
    tests = report.get("tests") or []
    total_duration_ms = int(round(report.get("duration", 0) * 1000))

    if not tests:
        return RunOutcome(
            status="error",
            duration_ms=total_duration_ms,
            error_message="未收集到任何测试用例，脚本可能无法运行",
            failure_type="script_error",
            raw_report=report,
        )

    # 单用例场景（一个脚本一条用例）；多用例时取首个非通过的结果代表
    chosen = tests[0]
    for t in tests:
        if t.get("outcome") not in ("passed", "skipped"):
            chosen = t
            break

    outcome = chosen.get("outcome", "error")
    call = chosen.get("call") or chosen.get("setup") or {}
    duration_ms = int(round(call.get("duration", report.get("duration", 0)) * 1000)) or total_duration_ms
    longrepr = call.get("longrepr") or chosen.get("longrepr")

    failure_type = None
    error_message = None
    if outcome == "failed":
        failure_type = "real_defect"
        error_message = _shorten(longrepr)
    elif outcome == "error":
        failure_type = "script_error"
        error_message = _shorten(longrepr)

    return RunOutcome(
        status=outcome,
        duration_ms=duration_ms,
        error_message=error_message,
        failure_type=failure_type,
        raw_report=report,
    )


def _shorten(longrepr: Any, limit: int = 2000) -> str | None:
    if longrepr is None:
        return None
    text = longrepr if isinstance(longrepr, str) else str(longrepr)
    return text[:limit]


class BaseRunner(ABC):
    """所有执行引擎的基类。子类声明 platform/requires_device 并实现准备与执行。"""

    platform: str = "base"
    requires_device: bool = False

    #: run() 结束后是否清理 _prepare 返回的工作目录（临时目录场景置 True）
    cleanup_workdir: bool = True

    async def run(self, case: Any, ctx: RunContext) -> RunOutcome:
        """模板方法：准备 → 执行 → 解析。子类一般无需覆写本方法。"""
        workdir = self._prepare(case, ctx)
        try:
            report = await self._execute(workdir, case, ctx)
            return self._parse(report)
        finally:
            if self.cleanup_workdir and workdir and Path(workdir).exists():
                import shutil
                shutil.rmtree(workdir, ignore_errors=True)

    @abstractmethod
    def _prepare(self, case: Any, ctx: RunContext) -> Path:
        """把脚本/依赖写入隔离的临时工作目录，返回该目录。"""

    @abstractmethod
    async def _execute(self, workdir: Path, case: Any, ctx: RunContext) -> dict:
        """在 workdir 内启动引擎执行，返回引擎原始报告（默认按 pytest-json-report 结构）。"""

    def _parse(self, report: dict) -> RunOutcome:
        """默认解析 pytest-json-report；非 pytest 引擎的子类可覆写。"""
        return parse_pytest_report(report)
