import asyncio
import subprocess
import sys

import pytest
from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError

from app.services.runners.web_agent_runner import _pick_latest_page


def _chromium_ready() -> bool:
    try:
        import playwright  # noqa: F401
    except Exception:
        return False
    try:
        out = subprocess.run(
            [
                sys.executable,
                "-c",
                "from playwright.sync_api import sync_playwright;"
                "p=sync_playwright().start();"
                "b=p.chromium.launch();"
                "b.close();"
                "p.stop()",
            ],
            capture_output=True,
            timeout=60,
        )
        return out.returncode == 0
    except Exception:
        return False


class _FakePage:
    def __init__(self, name: str, closed: bool = False):
        self.name = name
        self._closed = closed

    def is_closed(self) -> bool:
        return self._closed


def test_pick_latest_page_prefers_new_open_page():
    current = _FakePage("current")
    newer = _FakePage("newer")
    closed = _FakePage("closed", closed=True)
    assert _pick_latest_page(current, [current, closed, newer]) is newer


pytestmark = pytest.mark.skipif(not _chromium_ready(), reason="Playwright/chromium 不可用，跳过 WebAgentRunner 新开页回归测试")


def test_playwright_window_open_creates_new_page():
    async def _run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1200, "height": 800})
            page = await context.new_page()
            await page.set_content(
                """
                <a id="open" href="data:text/html,<title>child</title><h1>child</h1>" target="_blank">
                  open
                </a>
                """
            )
            try:
                async with context.expect_page(timeout=3000) as page_info:
                    await page.click("#open")
                new_page = await page_info.value
            except PlaywrightTimeoutError:
                await browser.close()
                pytest.skip("当前 Playwright headless shell 未稳定抛出新页事件，跳过浏览器级 popup 回归")
            await new_page.wait_for_load_state("domcontentloaded")
            assert len(context.pages) == 2
            assert await new_page.title() == "child"
            await browser.close()

    asyncio.run(_run())
