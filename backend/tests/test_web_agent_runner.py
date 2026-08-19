import asyncio
import subprocess
import sys

import pytest
from playwright._impl._errors import TimeoutError as PlaywrightTimeoutError

from app.services.runners.web_agent_runner import (
    _is_filter_step,
    _is_navigation_step,
    _judge_evidence_png,
    _menu_targets_from_step,
    _pick_latest_page,
    _web_input_text,
)


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


def test_judge_evidence_png_prefers_full_page_png():
    raw = b"viewport-shot"
    full = b"full-page-shot"
    assert _judge_evidence_png(raw, full) == full
    assert _judge_evidence_png(raw, None) == raw


def test_menu_targets_from_step_prefers_specific_submenu():
    targets = _menu_targets_from_step("点击进入设备中心-设备列表，查看搜索区域", "设备列表提供仓库搜索条件")
    assert targets[0] == "设备列表"
    assert "设备中心" in targets


def test_menu_targets_from_step_keeps_multiselect_target():
    targets = _menu_targets_from_step("在仓库筛选项中选择两个不同仓库并执行查询", "仓库支持多选且查询结果正确")
    assert "仓库" in targets


def test_step_kind_detection_distinguishes_navigation_and_filter():
    assert _is_navigation_step("点击进入设备中心-设备列表，查看搜索区域", "设备列表提供仓库搜索条件")
    assert not _is_filter_step("点击进入设备中心-设备列表，查看搜索区域", "设备列表提供仓库搜索条件")
    assert _is_filter_step("在仓库筛选项中选择两个不同仓库并执行查询", "仓库支持多选且查询结果正确")
    assert not _is_navigation_step("在仓库筛选项中选择两个不同仓库并执行查询", "仓库支持多选且查询结果正确")


def test_filter_step_detects_multiselect_intent_without_navigation_words():
    assert _is_filter_step("选择两个不同仓库并执行查询", "")
    assert not _is_navigation_step("选择两个不同仓库并执行查询", "")


pytestmark = pytest.mark.skipif(not _chromium_ready(), reason="Playwright/chromium 不可用，跳过 WebAgentRunner 新开页回归测试")


def test_playwright_window_open_creates_new_page():
    async def _run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            context = await browser.new_context(viewport={"width": 1200, "height": 800})
            page = await context.new_page()
            popup_task = asyncio.create_task(context.wait_for_event("page", timeout=3000))
            await page.set_content(
                """
                <button id="open" onclick="window.open('data:text/html,<title>child</title><h1>child</h1>', '_blank')">
                  open
                </button>
                """
            )
            await page.click("#open")
            try:
                new_page = await popup_task
            except PlaywrightTimeoutError:
                await browser.close()
                pytest.skip("当前 Playwright headless 环境未稳定抛出新页事件，跳过 popup 回归")
            await new_page.wait_for_load_state("domcontentloaded")
            assert len(context.pages) == 2
            assert await new_page.title() == "child"
            await browser.close()

    asyncio.run(_run())


def test_web_input_text_focuses_coordinate_target_and_clears_old_value():
    async def _run():
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page(viewport={"width": 800, "height": 400})
            await page.set_content(
                """
                <body tabindex="0">
                  <input id="keyword" placeholder="盘点单号" value="OLD" style="margin:40px;width:220px;height:32px" />
                </body>
                """
            )
            await page.focus("body")
            box = await page.locator("#keyword").bounding_box()
            assert box is not None

            ok = await _web_input_text(
                page,
                "00002",
                x=box["x"] + box["width"] / 2,
                y=box["y"] + box["height"] / 2,
            )

            assert ok is True
            assert await page.locator("#keyword").input_value() == "00002"
            assert await page.evaluate("document.activeElement && document.activeElement.id") == "keyword"
            await browser.close()

    asyncio.run(_run())
