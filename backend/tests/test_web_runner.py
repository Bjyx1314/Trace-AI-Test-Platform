"""WebRunner 真实执行测试 —— 真起 pytest+playwright 子进程跑脚本。

浏览器不可用时优雅 skip（保持无浏览器环境可运行的约定）。
"""
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.runners.web_runner import WebRunner  # noqa: E402
from app.services.runners.base import RunContext  # noqa: E402


def _chromium_ready() -> bool:
    """venv 内能 import playwright 且已安装 chromium 才算就绪。"""
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    # 探测浏览器是否已下载
    try:
        out = subprocess.run(
            [sys.executable, "-c",
             "from playwright.sync_api import sync_playwright;"
             "p=sync_playwright().start();b=p.chromium.launch();b.close();p.stop()"],
            capture_output=True, timeout=60,
        )
        return out.returncode == 0
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _chromium_ready(), reason="Playwright/chromium 不可用，跳过 WebRunner 真实执行")


class _Case:
    def __init__(self, script, title="webdemo", case_id="TC-WEB-0001"):
        self.script = script
        self.title = title
        self.case_id = case_id


def _run(case):
    return asyncio.run(WebRunner().run(case, RunContext(execution_id="exec-web")))


# 用 data: URL 避免依赖外部站点；page fixture 由 pytest-playwright 提供
PASS_SCRIPT = (
    "def test_title(page):\n"
    "    page.set_content('<h1>hello</h1>')\n"
    "    assert page.locator('h1').inner_text() == 'hello'\n"
)
FAIL_SCRIPT = (
    "def test_title(page):\n"
    "    page.set_content('<h1>hello</h1>')\n"
    "    assert page.locator('h1').inner_text() == 'world'\n"
)


def test_web_runner_passed():
    out = _run(_Case(PASS_SCRIPT))
    assert out.status == "passed"


def test_web_runner_failed_is_real_defect():
    out = _run(_Case(FAIL_SCRIPT))
    assert out.status == "failed"
    assert out.failure_type == "real_defect"


def test_web_runner_platform():
    r = WebRunner()
    assert r.platform == "web"
    assert r.requires_device is False
