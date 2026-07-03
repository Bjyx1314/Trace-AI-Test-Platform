"""页面探索 —— AI 按「页面名 + 如何到达(描述)」自动导航到目标页面，再抽取页面结构。

用户在页面缓存里填：页面名称(中文，如“订单列表”) + 如何到达/具体操作(如“点订单中心→订单列表”)。
本服务带登录态打开被测系统首页，用 AI 视觉一步步点菜单/tab 导航到该页面，到达后抽取交互元素结构。
page_name 直接用用户填的中文名；description 作为导航提示。
"""
from __future__ import annotations

import asyncio
from io import BytesIO

from app.services.runners.web_agent_runner import _EXTRACT_JS, _encode_at  # noqa: F401
from app.services.runners.android_runner import _encode

_VIEWPORT = {"width": 1440, "height": 900}
_MAX_NAV_STEPS = 14

# 抽取页面导航「目录结构」(左侧菜单/顶部 tab，含层级)
_MENU_JS = """
() => {
  const clean = (t) => (t || '').replace(/\\s+/g, ' ').trim();
  const out = [], seen = new Set();
  const SEL = '.ant-menu-submenu-title,.ant-menu-item,.el-submenu__title,.el-menu-item,'
    + '.ant-tabs-tab,.el-tabs__item,[role="menuitem"],[role="tab"]';
  document.querySelectorAll(SEL).forEach(el => {
    const t = clean(el.innerText);
    if (!t || t.length > 24 || seen.has(t)) return; seen.add(t);
    const sub = el.classList.contains('ant-menu-submenu-title') || el.classList.contains('el-submenu__title');
    // 估算层级(祖先里的 submenu 数量)
    let lvl = 0, p = el;
    while (p && (p = p.parentElement)) {
      if (p.classList && (p.classList.contains('ant-menu-submenu') || p.classList.contains('el-submenu'))) lvl++;
    }
    out.push({ name: t, type: sub ? 'submenu' : 'menu', level: Math.min(lvl, 4) });
  });
  return out.slice(0, 150);
}
"""

_NAV_SYSTEM = (
    "你是 PC 网页导航代理。任务：从当前页面【一步步导航到目标页面】。每步只输出一个 JSON(不要解释/markdown)：\n"
    '{"action":"tap|input|swipe|wait|done","x":数,"y":数,"text":"输入内容","direction":"up|down|left|right","reason":"依据"}\n'
    "规则：坐标 x,y 用当前截图像素(左上角0,0)指控件中心；tap点击/input在已聚焦框输入/swipe滚动/wait等待；"
    "优先按‘如何到达’的提示点击左侧菜单/顶部tab逐级进入；遇登录页或无关弹窗先处理；"
    "当确认已经到达目标页面(页面标题/列表/表单与目标一致)时输出 action=done。"
)


async def _settle(page):
    try:
        await page.wait_for_load_state("networkidle", timeout=6000)
    except Exception:
        pass
    try:
        await page.wait_for_function(
            "() => !document.querySelector('.ant-spin-spinning, .el-loading-mask, .ant-skeleton-active')",
            timeout=4000)
    except Exception:
        pass


async def explore(base_url: str, page_name: str, description: str | None = None) -> dict | None:
    """AI 导航到「page_name」页面并抽取结构。返回 {page_name, regions, final_url, reached}；失败 None。"""
    from app.services.web_login import platform_for_base_url, ensure_login_state, launch_args_for

    plat = platform_for_base_url(base_url)
    state, args = None, []
    if plat:
        try:
            state = await ensure_login_state(plat)
        except Exception:
            state = None
        args = launch_args_for(plat)

    try:
        from playwright.async_api import async_playwright
        from PIL import Image
        from app.agents.llm import get_provider, _extract_json
    except Exception:
        return None
    provider = get_provider()
    dev_w, dev_h = _VIEWPORT["width"], _VIEWPORT["height"]
    reached = False

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=args)
            ctx = await browser.new_context(viewport=_VIEWPORT, storage_state=state)
            page = await ctx.new_page()
            try:
                await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                await _settle(page)

                notes: list[str] = []
                for _ in range(_MAX_NAV_STEPS):
                    try:
                        raw = await page.screenshot()
                        b64, sw, sh, _png = _encode(Image.open(BytesIO(raw)))
                    except Exception:
                        break
                    scale_x, scale_y = dev_w / sw, dev_h / sh
                    user = (
                        f"目标页面：{page_name}\n"
                        f"如何到达：{description or '在左侧菜单/顶部导航中找到并进入该页面'}\n"
                        f"已执行：\n" + ("\n".join(notes) or "(无)") +
                        f"\n\n这是 PC 网页(宽{sw}高{sh}像素)。请输出下一步操作 JSON；若已到达目标页面输出 action=done。"
                    )
                    try:
                        out = await provider.text_multi(_NAV_SYSTEM, user, [(b64, "image/jpeg")], 400)
                    except Exception:
                        break
                    act = _extract_json(out)
                    a = (act.get("action") or "").lower()
                    if a == "done":
                        reached = True
                        break
                    try:
                        if a == "tap":
                            await page.mouse.click(int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y))
                            notes.append(f"点击({act.get('x')},{act.get('y')}) {act.get('reason', '')}")
                        elif a == "input":
                            await page.keyboard.type(str(act.get("text", "")))
                            notes.append(f"输入「{act.get('text', '')}」")
                        elif a == "swipe":
                            d = act.get("direction", "")
                            await page.mouse.move(dev_w / 2, dev_h / 2)
                            await page.mouse.wheel(0, 500 if d == "up" else -500 if d == "down" else 0)
                            notes.append(f"滚动({d})")
                        elif a == "wait":
                            await asyncio.sleep(1.5)
                            notes.append("等待")
                        else:
                            notes.append(f"未知动作 {a}")
                        await asyncio.sleep(1.0)
                        await _settle(page)
                    except Exception as e:
                        notes.append(f"动作异常：{e}")

                await _settle(page)
                els = await page.evaluate(_EXTRACT_JS)
                menu = await page.evaluate(_MENU_JS)
                final_url = page.url
            finally:
                await browser.close()
    except Exception:
        return None

    name = (page_name or "").strip() or "页面"
    regions = []
    if menu:
        # 导航目录(菜单树)：供执行时直接照菜单定位，省去逐个试探
        regions.append({"name": "导航目录", "selector": "nav,.ant-menu,.el-menu", "kind": "menu", "elements": menu})
    regions.append({"name": name, "selector": "body", "kind": "page", "elements": els or []})
    return {"page_name": name, "regions": regions, "final_url": final_url, "reached": reached}
