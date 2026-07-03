"""RepoRunner —— 在框架仓库 checkout 内跑框架自身命令（仓库内执行模型）。

与 Api/WebRunner（把脚本写进空临时目录跑）的根本区别：本 Runner 直接在已绑定框架仓库的
工作区里、对生成并提交回仓库的用例文件执行框架原生命令（如 pytest <target> -m smoke
--project demo）。这样用例复用框架的 conftest/fixtures/POM/AW 关键字，与本地手跑完全一致。

- cleanup_workdir=False：绝不删除框架仓库工作区。
- run_command 模板支持 {target} 占位；无论模板如何，都确保附加 --json-report 以便统一解析。
- json 报告写到仓库外的临时文件，避免污染工作树。
"""
from __future__ import annotations

import shlex
import sys
import tempfile
from pathlib import Path
from typing import Any

from .base import (
    BaseRunner,
    RunContext,
    RunOutcome,
    parse_pytest_subprocess_outcome,
    run_reported_command,
)

_DEFAULT_TIMEOUT = 300


class RepoRunner(BaseRunner):
    platform = "repo"
    requires_device = False
    cleanup_workdir = False

    def __init__(
        self,
        repo_root: str | Path,
        target: str,
        *,
        run_command: str | None = None,
        env: dict | None = None,
        timeout_sec: int = _DEFAULT_TIMEOUT,
    ):
        self.repo_root = Path(repo_root)
        self.target = target           # 仓库相对路径（执行入口：壳/test）
        self.run_command = run_command
        self.env = env or {}
        self.timeout_sec = timeout_sec
        self._report_path: Path | None = None

    def _prepare(self, case: Any, ctx: RunContext) -> Path:
        # 不准备临时目录，直接用框架仓库工作区
        return self.repo_root

    def _build_argv(self) -> list[str]:
        report = Path(tempfile.mkstemp(prefix="reporun_", suffix=".json")[1])
        self._report_path = report
        report_flag = f"--json-report-file={report}"

        if self.run_command:
            tpl = self.run_command.replace("{target}", self.target)
            argv = shlex.split(tpl, posix=False)
            # 模板未自带 pytest 解释器前缀时，补 sys.executable -m pytest
            if argv and argv[0] == "pytest":
                argv = [sys.executable, "-m", *argv]
        else:
            argv = [sys.executable, "-m", "pytest", self.target]

        if "--json-report" not in argv:
            argv.append("--json-report")
        argv.append(report_flag)
        if "-q" not in argv and "--quiet" not in argv:
            argv.append("-q")
        return argv

    async def _execute(self, workdir: Path, case: Any, ctx: RunContext) -> dict:
        argv = self._build_argv()
        try:
            return await run_reported_command(
                self.repo_root, argv, self._report_path,
                timeout_sec=self.timeout_sec, env=self.env,
            )
        finally:
            if self._report_path and self._report_path.exists():
                self._report_path.unlink(missing_ok=True)

    def _parse(self, report: dict) -> RunOutcome:
        return parse_pytest_subprocess_outcome(report)
