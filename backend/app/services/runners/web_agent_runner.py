"""WebAgentRunner —— AI 视觉驱动浏览器执行 PC web 手动用例(Playwright，无需脚本)。

与 AndroidAgentRunner 同构(复用其提示词/编码/截图/逐步判定逻辑)，只是把"驱动真机"换成
"驱动 Chromium"：Playwright 打开被测 PC 地址 → 每步截图给 AI → 点/输/滚 → check_points 逐步判定。
被测地址(base_url)由执行上下文(取自页面缓存)提供。
"""
from __future__ import annotations

import asyncio
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from .base import BaseRunner, RunOutcome, RunContext
from .android_runner import (
    _SYSTEM, _VERDICT_CN, _save_shot, _MAX_ACTIONS_PER_STEP, _STUCK_LIMIT,
)

_VIEWPORT = {"width": 1440, "height": 900}

# 发给 AI 的动作循环截图宽度：PC 网页专用，比安卓真机的 540 大很多。
# 桌面视口 1440 宽、左侧菜单仅 ~232px，若沿用 540 会把菜单压到 ~87px 宽、行高 ~11px，
# AI 无法精确定位小菜单项，点击坐标会系统性点偏(实测点到菜单下方空白导致菜单不展开)。
# 1280 宽既在 Claude 视觉最佳分辨率(~1568 长边)内，又让菜单/按钮足够清晰、坐标更准。
_WEB_SEND_W = 1280


async def _wait_for_page_ready(page, timeout_ms: int = 6000) -> None:
    """等待新页基础可用，避免刚切过去就截图到空白页/中间态。"""
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout_ms)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:
        pass


def _pick_latest_page(current_page, pages: list[Any]) -> Any:
    """从 context.pages 中挑选最新可用页；没有更合适的就保留当前页。"""
    for cand in reversed(pages or []):
        try:
            if cand != current_page and not cand.is_closed():
                return cand
        except Exception:
            continue
    return current_page


def _encode_web(img) -> tuple[str, int, int, bytes]:
    """按 _WEB_SEND_W 等比缩放并编码为 JPEG(返回 b64, 宽, 高, 原始bytes)。
    与 android 的 _encode 同签名，AI 坐标按 scale=dev/宽 还原回真实视口。"""
    import base64
    w, h = img.size
    if w > _WEB_SEND_W:
        s = _WEB_SEND_W / w
        img = img.resize((_WEB_SEND_W, int(h * s)))
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=80)
    data = buf.getvalue()
    return base64.b64encode(data).decode(), img.size[0], img.size[1], data


def _encode_at(img, width: int) -> str:
    """把截图按指定宽度编码为 JPEG base64(整页复核用，比发给动作循环的 540 大、保证可读)。"""
    import base64
    w, h = img.size
    if w > width:
        s = width / w
        img = img.resize((width, int(h * s)))
    buf = BytesIO()
    img.convert("RGB").save(buf, format="JPEG", quality=82)
    return base64.b64encode(buf.getvalue()).decode()


# 执行时抽取页面交互元素(输入/按钮/链接/下拉)，用于自动补充页面结构缓存
_EXTRACT_JS = """
() => {
  const css = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const ph = el.getAttribute && el.getAttribute('placeholder');
    if (ph) return el.tagName.toLowerCase() + '[placeholder="' + ph + '"]';
    if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
    return el.tagName.toLowerCase();
  };
  const out = [], seen = new Set();
  const SEL = 'input,textarea,select,button,a[href],[role="button"],'
    + '.ant-menu-item,.ant-tabs-tab,.el-menu-item,.el-tabs__item,[role="menuitem"],[role="tab"]';
  document.querySelectorAll(SEL).forEach(el => {
    if (!(el.offsetWidth || el.offsetHeight)) return;  // 跳过不可见
    const name = (el.getAttribute('placeholder') || (el.innerText||'').trim()
      || el.getAttribute('aria-label') || el.name || el.value || '').replace(/\\s+/g,' ').trim();
    if (!name || name.length > 40) return;
    const type = el.tagName.toLowerCase();
    const k = name + '|' + type;
    if (seen.has(k)) return; seen.add(k);
    out.push({ name, type, selector: css(el) });
  });
  return out.slice(0, 80);
}
"""


class WebAgentRunner(BaseRunner):
    platform = "web"
    requires_device = False

    async def run(self, case: Any, ctx: RunContext) -> RunOutcome:
        t0 = time.monotonic()
        base_url = ctx.base_url
        if not base_url:
            return RunOutcome(status="error", duration_ms=0,
                              error_message="未配置被测 PC 系统地址(请在页面缓存维护该项目的 PC 端地址)",
                              failure_type="env_error")
        try:
            from playwright.async_api import async_playwright
            from PIL import Image
        except Exception as e:
            return RunOutcome(status="error", duration_ms=0,
                              error_message=f"未安装 playwright/Pillow，无法浏览器执行：{e}", failure_type="env_error")

        from app.agents.llm import get_provider, _extract_json
        provider = get_provider()
        steps = getattr(case, "steps", None) or [
            {"action": getattr(case, "title", "执行用例"), "expected": getattr(case, "expected_result", "") or ""}
        ]
        title = getattr(case, "title", "")
        case_id = getattr(case, "id", "case")
        dev_w, dev_h = _VIEWPORT["width"], _VIEWPORT["height"]
        shot_i = 0

        def _save(raw: bytes | None) -> str | None:
            # 存「原分辨率」高质量 JPEG 供结果查看(发给 AI 的是 540px 压缩图，不能拿来存，放大会糊)。
            nonlocal shot_i
            from PIL import Image as _Img
            out = raw
            if raw:
                try:
                    im = _Img.open(BytesIO(raw)).convert("RGB")
                    buf = BytesIO()
                    im.save(buf, format="JPEG", quality=92)
                    out = buf.getvalue()
                except Exception:
                    out = raw
            url = _save_shot(out, ctx.execution_id, case_id, shot_i)
            shot_i += 1
            return url

        ui_trace: list[dict] = []
        captures: dict[str, dict] = {}  # url -> {page_name, regions}，执行时自动补充页面结构缓存
        run_error: str | None = None

        try:
            async with async_playwright() as p:
                launch_args = (ctx.extra.get("browser_args") if ctx.extra else None) or []
                browser = await p.chromium.launch(headless=True, args=launch_args)
                context = await browser.new_context(viewport=_VIEWPORT,
                                                    storage_state=ctx.extra.get("storage_state") if ctx.extra else None)
                page = await context.new_page()
                popup_queue: asyncio.Queue[Any] = asyncio.Queue()

                def _watch_page(_page):
                    return None

                def _on_popup(new_page):
                    try:
                        popup_queue.put_nowait(new_page)
                    except Exception:
                        pass

                def _bind_popup_watch(p):
                    try:
                        p.on("popup", _on_popup)
                    except Exception:
                        pass

                def _watch_active_page(p):
                    _watch_page(p)
                    _bind_popup_watch(p)

                _watch_active_page(page)
                try:
                    context.on("page", _on_popup)
                except Exception:
                    pass

                async def _settle():
                    """等页面加载稳定后再截图，避免数据未加载完就误判(如列表'共0条'其实在加载中)。"""
                    try:
                        await page.wait_for_load_state("networkidle", timeout=6000)
                    except Exception:
                        pass
                    # 等常见加载动画消失(antd/element-ui 等)
                    try:
                        await page.wait_for_function(
                            "() => !document.querySelector('.ant-spin-spinning, .el-loading-mask, .ant-skeleton-active')",
                            timeout=4000,
                        )
                    except Exception:
                        pass

                async def _adopt_new_page() -> str | None:
                    nonlocal page
                    cand = None
                    while not popup_queue.empty():
                        try:
                            cand = popup_queue.get_nowait()
                        except asyncio.QueueEmpty:
                            break
                    if cand is None:
                        cand = _pick_latest_page(page, list(getattr(context, "pages", []) or []))
                        if cand == page:
                            return None
                    try:
                        await _wait_for_page_ready(cand)
                    except Exception:
                        pass
                    page = cand
                    _watch_active_page(page)
                    try:
                        await page.bring_to_front()
                    except Exception:
                        pass
                    try:
                        cur_title = await page.title()
                    except Exception:
                        cur_title = ""
                    return f"切换到新页面：{(cur_title or page.url or '未命名页面')[:120]}"

                async def _capture():
                    """抓当前页面的交互元素结构(按 url 去重)，供执行后写入页面结构缓存。"""
                    try:
                        els = await page.evaluate(_EXTRACT_JS)
                        if els:
                            title = (await page.title()) or page.url
                            captures[page.url] = {"page_name": title[:120],
                                                  "regions": [{"name": title[:60] or "页面", "selector": "body", "elements": els}]}
                    except Exception:
                        pass

                try:
                    await page.goto(base_url, wait_until="domcontentloaded", timeout=30000)
                    await _settle()
                    await _capture()
                except Exception as e:
                    await browser.close()
                    return RunOutcome(status="error", duration_ms=int((time.monotonic() - t0) * 1000),
                                      error_message=f"打开被测地址失败({base_url})：{e}", failure_type="env_error")

                async def shot_png() -> bytes:
                    await _settle()
                    return await page.screenshot()

                # 已缓存的导航目录/页面 → 注入提示，让 AI 照菜单直接定位、减少探索
                _nav_menu = (ctx.extra or {}).get("nav_menu")
                _known = (ctx.extra or {}).get("known_pages")
                nav_block = ""
                if _nav_menu:
                    _mlines = "\n".join(_nav_menu.splitlines()[:60])
                    nav_block += f"\n\n【该系统导航目录(已缓存，可直接点对应菜单快速到达，无需逐个试探)】：\n{_mlines}"
                if _known:
                    nav_block += f"\n【已缓存页面】：{'、'.join(_known[:30])}"

                for i, step in enumerate(steps, start=1):
                    s_action = step.get("action", "")
                    s_expected = step.get("expected", "")
                    s_checks = step.get("check_points") or []
                    checks_result: list[dict] = []
                    shot: str | None = None
                    notes: list[str] = []
                    verdict, reason = None, ""
                    prev_png, same_count = None, 0

                    for _ in range(_MAX_ACTIONS_PER_STEP):
                        try:
                            raw_png = await shot_png()
                            img = Image.open(BytesIO(raw_png))
                        except Exception as e:
                            run_error = f"截图失败：{e}"
                            break
                        b64, sw, sh, png = _encode_web(img)
                        scale_x, scale_y = dev_w / sw, dev_h / sh

                        same_count = same_count + 1 if (prev_png is not None and png == prev_png) else 0
                        prev_png = png
                        # 界面连续无变化：可能已到达目标(无需再操作)，也可能真卡住。
                        # 不直接判 blocked，而是让 AI 基于当前界面做一次最终判定(避免把"已到位"误判为卡住)。
                        stuck = same_count >= _STUCK_LIMIT

                        checks_text = ("\n判定锚点(逐条核对)：\n" + "\n".join(f"- {c}" for c in s_checks)) if s_checks else ""
                        stuck_hint = (
                            "\n\n注意：界面已连续多次无变化。若当前界面已满足本步预期，请直接 judge=pass；"
                            "若明显不符合预期 judge=fail；若确实卡住/无法到达目标页 judge=blocked。不要再尝试无效操作。"
                            if stuck else ""
                        )
                        user = (
                            f"测试用例：{title}\n\n"
                            f"当前步骤 {i}/{len(steps)}：\n操作：{s_action}\n预期：{s_expected}{checks_text}\n\n"
                            f"本步已执行：\n" + ("\n".join(notes) or "(无)") +
                            f"\n\n这是 PC 网页(宽{sw}高{sh}像素)。请输出下一步操作 JSON，或在可判定时输出 judge+verdict"
                            + ("(并在 checks 里逐条给出锚点核对结果)" if s_checks else "") + "。" + stuck_hint + nav_block
                        )
                        try:
                            raw = await provider.text_multi(_SYSTEM, user, [(b64, "image/jpeg")], 600)
                        except Exception as e:
                            run_error = f"AI 决策失败：{e}"
                            break
                        act = _extract_json(raw)
                        a = (act.get("action") or "").lower()
                        # 卡住时强制收敛为判定：即使 AI 仍想操作，也按其 verdict(没有则 blocked)结束本步
                        if stuck and a != "judge":
                            verdict = act.get("verdict") if act.get("verdict") in ("pass", "fail", "blocked") else "blocked"
                            reason = act.get("reason") or "界面连续无变化，疑似卡住"
                            shot = _save(raw_png)
                            break

                        if a == "judge":
                            # 判定前确保页面渲染稳定(框架无关)：先网络空闲/动画消失，再等 DOM 不再变化(约1.2s无变更)。
                            # 这样：有数据→等数据渲染完；确实无数据→DOM 很快稳定、快速通过(不强求有数据)。
                            await _settle()
                            try:
                                await page.evaluate(
                                    "() => new Promise(r => {"
                                    " let t = setTimeout(() => r(1), 1800);"
                                    " const ob = new MutationObserver(() => { clearTimeout(t);"
                                    "   t = setTimeout(() => { try{ob.disconnect()}catch(e){}; r(1); }, 1800); });"
                                    " try { ob.observe(document.body, {childList:true, subtree:true, characterData:true}); } catch(e) { r(1); }"
                                    " setTimeout(() => { try{ob.disconnect()}catch(e){}; r(1); }, 10000);"
                                    "})")
                            except Exception:
                                pass
                            await asyncio.sleep(2.5)  # 再多留几秒缓冲，确保数据完全稳定
                            # 整页复核：不缩小(缩小字会糊)，而是给 AI 多张【可读分辨率】截图拼出整页——
                            # 整页纵向一张(full_page) + 表格横向分段若干张，综合判定，避免只看首屏/首列就判缺失。
                            try:
                                judge_imgs: list[tuple[str, str]] = []
                                full_png = await page.screenshot(full_page=True)
                                judge_imgs.append((_encode_at(Image.open(BytesIO(full_png)), 1280), "image/jpeg"))
                                # 找最宽可横向滚动容器，按可视宽分段横向截图
                                info = await page.evaluate(
                                    "() => { let el=null,best=0; document.querySelectorAll('*').forEach(e=>{"
                                    "const o=e.scrollWidth-e.clientWidth; if(o>best){best=o;el=e;}});"
                                    "return el ? {sw:el.scrollWidth, cw:el.clientWidth} : null; }"
                                )
                                if info and info.get("sw", 0) > info.get("cw", 0) + 30:
                                    cw = max(1, int(info["cw"]))
                                    n = min(4, -(-int(info["sw"]) // cw))  # ceil
                                    for k in range(n):
                                        await page.evaluate(
                                            "(x)=>{let el=null,best=0;document.querySelectorAll('*').forEach(e=>{"
                                            "const o=e.scrollWidth-e.clientWidth; if(o>best){best=o;el=e;}});"
                                            "if(el) el.scrollLeft=x;}", k * cw)
                                        await asyncio.sleep(0.35)
                                        seg = await page.screenshot()
                                        judge_imgs.append((_encode_at(Image.open(BytesIO(seg)), 1280), "image/jpeg"))
                                    await page.evaluate(
                                        "()=>{let el=null,best=0;document.querySelectorAll('*').forEach(e=>{"
                                        "const o=e.scrollWidth-e.clientWidth; if(o>best){best=o;el=e;}});if(el) el.scrollLeft=0;}")
                                # 从 DOM 直接读表格全部列名(含横向未滚到/被裁剪的)——比截图更可靠
                                try:
                                    cols = await page.evaluate(
                                        "() => [...document.querySelectorAll('.ant-table-thead th,table thead th,"
                                        ".el-table__header th,[role=columnheader]')]"
                                        ".map(t=>(t.innerText||'').replace(/\\s+/g,' ').trim()).filter(Boolean).slice(0,80)"
                                    )
                                except Exception:
                                    cols = []
                                cols_text = ("\n该页面表格的【全部列名(从DOM读取，含横向未滚到/被遮挡的列)】："
                                             + "、".join(cols) + "。以此为准核对字段是否齐全，不要因为截图没滚到就判缺失。") if cols else ""
                                fuser = (
                                    f"测试用例：{title}\n\n当前步骤 {i}/{len(steps)}：\n操作：{s_action}\n预期：{s_expected}{checks_text}\n\n"
                                    f"下面是同一页面的 {len(judge_imgs)} 张可读截图：第1张是【整页纵向完整截图】，"
                                    "其余(若有)是【表格从左到右横向分段截图】。请综合所有图"
                                    + cols_text +
                                    "\n看全整页所有字段/列后再判定本步骤，"
                                    "输出 judge+verdict" + ("(并在 checks 里逐条核对锚点)" if s_checks else "") + "。"
                                )
                                fraw = await provider.text_multi(_SYSTEM, fuser, judge_imgs, 600)
                                fact = _extract_json(fraw)
                                if (fact.get("action") or "").lower() == "judge":
                                    act = fact  # 用整页复核结果覆盖
                            except Exception:
                                pass
                            verdict = act.get("verdict") if act.get("verdict") in ("pass", "fail", "blocked") else "blocked"
                            reason = act.get("reason") or ""
                            raw_checks = act.get("checks") if isinstance(act.get("checks"), list) else []
                            checks_result = [{"point": str(c.get("point", "")), "ok": bool(c.get("ok"))}
                                             for c in raw_checks if isinstance(c, dict)]
                            if verdict == "pass" and checks_result and any(not c["ok"] for c in checks_result):
                                miss = "、".join(c["point"] for c in checks_result if not c["ok"])
                                verdict = "blocked"
                                reason = (reason + f"；但锚点未满足：{miss}").strip("；")
                            shot = _save(raw_png)
                            break

                        try:
                            if a == "tap":
                                await page.mouse.click(int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y))
                                desc = f"点击({act.get('x')},{act.get('y')}) {act.get('reason', '')}"
                            elif a == "input":
                                await page.keyboard.type(str(act.get("text", "")))
                                desc = f"输入「{act.get('text', '')}」"
                            elif a == "swipe":
                                # 移动端约定：up=看下方内容、left=看右侧内容。补全水平滚动(表格看列要横向滚)。
                                _dir = act.get("direction", "")
                                if _dir in ("left", "right"):
                                    # 水平滚动：优先滚动「表格横向滚动容器」(antd/element 等)，再退回最宽可滚元素；
                                    # 都不行才用 mouse.wheel。返回是否真的滚动了，反馈给 AI。
                                    _dx = 800 if _dir == "left" else -800
                                    moved = await page.evaluate(
                                        """(dx) => {
                                            const sel = '.ant-table-body,.ant-table-content,.el-table__body-wrapper,'
                                              + '.ant-table-scroll,[class*="table-body"],[class*="table-scroll"]';
                                            const scrollable = (e) => e && e.scrollWidth > e.clientWidth + 4;
                                            let cands = Array.from(document.querySelectorAll(sel)).filter(scrollable);
                                            if (!cands.length)
                                              cands = Array.from(document.querySelectorAll('*')).filter(scrollable);
                                            if (!cands.length) return false;
                                            cands.sort((a,b)=>(b.scrollWidth-b.clientWidth)-(a.scrollWidth-a.clientWidth));
                                            const el = cands[0];
                                            const before = el.scrollLeft;
                                            const max = el.scrollWidth - el.clientWidth;
                                            el.scrollLeft = Math.max(0, Math.min(max, before + dx));
                                            return el.scrollLeft !== before;
                                        }""", _dx)
                                    if not moved:  # 没有可横向滚动容器，再兜底用 wheel
                                        await page.mouse.move(dev_w / 2, dev_h / 2)
                                        await page.mouse.wheel(_dx, 0)
                                    desc = f"横向滚动({_dir}){'' if moved else '(已到边/无横向溢出)'}"
                                else:
                                    await page.mouse.move(dev_w / 2, dev_h / 2)
                                    await page.mouse.wheel(0, 500 if _dir == "up" else -500 if _dir == "down" else 0)
                                    desc = f"滚动({_dir})"
                            elif a == "back":
                                await page.go_back()
                                desc = "返回"
                            elif a == "wait":
                                await asyncio.sleep(1.5)
                                desc = "等待加载"
                            else:
                                await asyncio.sleep(1.0)
                                desc = "未取到有效动作，重试" if not a else f"未知动作 {a}"
                            try:
                                switched = await asyncio.wait_for(_adopt_new_page(), timeout=2.5)
                            except Exception:
                                switched = None
                            if switched:
                                desc = f"{desc}；{switched}"
                            notes.append(desc)
                            await asyncio.sleep(1.0)
                        except Exception as e:
                            notes.append(f"动作异常：{e}")

                    if run_error:
                        break
                    if verdict is None:
                        verdict, reason = "blocked", f"{_MAX_ACTIONS_PER_STEP} 步操作内仍无法判定本步骤"
                    if shot is None:
                        try:
                            shot = _save((await shot_png()))
                        except Exception:
                            pass
                    ui_trace.append({
                        "seq": i, "action": s_action, "expected": s_expected,
                        "verdict": verdict, "verdict_cn": _VERDICT_CN.get(verdict, verdict),
                        "reason": reason, "note": "；".join(notes)[:300], "shot": shot, "checks": checks_result,
                    })
                    await _capture()  # 本步结束抓一次当前页面结构

                await browser.close()
        except Exception as e:
            run_error = run_error or f"浏览器执行异常：{e}"

        duration_ms = int((time.monotonic() - t0) * 1000)
        final_shot = next((st["shot"] for st in reversed(ui_trace) if st.get("shot")), None)
        page_caps = [{"url": u, **v} for u, v in captures.items()] or None
        if run_error and not ui_trace:
            return RunOutcome(status="error", duration_ms=duration_ms, error_message=run_error,
                              failure_type="env_error", screenshot_url=final_shot, ui_trace=ui_trace, page_captures=page_caps)

        non_pass = [st for st in ui_trace if st["verdict"] != "pass"]
        if ui_trace and not non_pass:
            return RunOutcome(status="passed", duration_ms=duration_ms, screenshot_url=final_shot, ui_trace=ui_trace, page_captures=page_caps)
        summary = "；".join(f"步骤{st['seq']}{st['verdict_cn']}：{st['reason']}" for st in non_pass)[:600]
        only_blocked = bool(non_pass) and all(st["verdict"] == "blocked" for st in non_pass)
        return RunOutcome(
            status="failed", duration_ms=duration_ms,
            error_message=(run_error or (("存在无法验证的步骤：" if only_blocked else "存在不符合预期的步骤：") + summary)),
            failure_type="env_error" if (only_blocked or run_error) else "real_defect",
            screenshot_url=final_shot, ui_trace=ui_trace, page_captures=page_caps,
        )

    def _prepare(self, case: Any, ctx: RunContext) -> Path:
        raise NotImplementedError

    async def _execute(self, workdir, case: Any, ctx: RunContext) -> dict:
        raise NotImplementedError
