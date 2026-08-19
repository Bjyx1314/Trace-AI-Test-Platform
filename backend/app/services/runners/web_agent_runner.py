"""WebAgentRunner —— AI 视觉驱动浏览器执行 PC web 手动用例(Playwright，无需脚本)。

与 AndroidAgentRunner 同构(复用其提示词/编码/截图/逐步判定逻辑)，只是把"驱动真机"换成
"驱动 Chromium"：Playwright 打开被测 PC 地址 → 每步截图给 AI → 点/输/滚 → check_points 逐步判定。
被测地址(base_url)由执行上下文(取自页面缓存)提供。
"""
from __future__ import annotations

import asyncio
import re
import time
from io import BytesIO
from pathlib import Path
from typing import Any

from app.services import execution_control
from . import upload_fixtures
from .base import BaseRunner, RunOutcome, RunContext
from .android_runner import (
    _SYSTEM, _VERDICT_CN, _save_shot, _MAX_ACTIONS_PER_STEP, _STUCK_LIMIT, _ACTION_MAX_TOKENS,
    _reasoning_effort, _prior_steps_hint, _now_hint, _step_needs_photo_picker_context,
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


# 列表「前 N 行 × 各列 列名=值」提取：按屏幕 X 坐标把表头 th 与每行各 td 就近配对。
# 兼容 antd 固定列(表拆成 fixed-left/center/fixed-right 多个子表、多个 tbody)——
# 每个子表取前 5 行、按行下标对齐汇总 td，再按 X 与表头配对，不受 DOM 拆分/横向滚动错位影响。
# 返回：行数组，每行是 ["列名=值", ...]。首行不满足测试要求时可依次看后续行(最多 5 行)。
_TABLE_ROW_PAIRS_JS = r"""
() => {
  const norm = s => (s||'').replace(/\s+/g,' ').trim();
  const cx = el => { const r = el.getBoundingClientRect(); return r.left + r.width/2; };
  const ths = [...document.querySelectorAll('.ant-table-thead th, thead th, .el-table__header th, [role=columnheader]')]
    .map(t => ({name: norm(t.innerText), x: cx(t)})).filter(t => t.name && t.name.length <= 20);
  if (!ths.length) return [];
  const bodies = [...document.querySelectorAll('.ant-table-tbody, .el-table__body tbody, table tbody')];
  const rowsPerBody = bodies.map(b => [...b.querySelectorAll('tr')].slice(0, 5));
  const maxRows = Math.min(5, Math.max(0, ...rowsPerBody.map(r => r.length)));
  const out = [];
  for (let i = 0; i < maxRows; i++) {
    let tds = [];
    for (const rows of rowsPerBody) {
      if (rows[i]) tds = tds.concat([...rows[i].querySelectorAll('td')].map(td => ({text: norm(td.innerText), x: cx(td)})));
    }
    if (!tds.length) continue;
    const usedName = new Set(); const pairs = [];
    for (const h of ths) {
      if (usedName.has(h.name)) continue; usedName.add(h.name);
      let best = null, bd = 1e9;
      for (const d of tds) { const dd = Math.abs(d.x - h.x); if (dd < bd) { bd = dd; best = d; } }
      const val = (best && bd < 45) ? best.text : '';
      pairs.push(h.name + '=' + (val || '(空)'));
    }
    out.push(pairs.slice(0, 60));
  }
  return out;   // [[行1 col=val...], [行2 ...], ...] 最多5行
}
"""


def _judge_evidence_png(raw_png: bytes, full_png: bytes | None = None) -> bytes:
    """最终判定步骤优先落整页证据图，没有时再回退当前视口图。"""
    return full_png or raw_png


def _stack_pngs_vertically(pngs: list[bytes], max_width: int = 1600, gap: int = 8) -> bytes | None:
    """把多张证据图纵向拼成一张：整页图 + 表格各横向分段图。
    宽表(如资源列表)目标列在右侧、单张整页图横向截不全 → 纵向拼上各横向分段，
    结果截图里就能看到"质保有效期"等右侧目标列，不再只看到左侧几列。"""
    from PIL import Image as _Img
    imgs = []
    for p in pngs:
        if not p:
            continue
        try:
            im = _Img.open(BytesIO(p)).convert("RGB")
            if im.width > max_width:
                im = im.resize((max_width, max(1, int(im.height * max_width / im.width))))
            imgs.append(im)
        except Exception:
            pass
    if not imgs:
        return None
    if len(imgs) == 1:
        buf = BytesIO(); imgs[0].save(buf, "JPEG", quality=90); return buf.getvalue()
    w = max(im.width for im in imgs)
    h = sum(im.height for im in imgs) + gap * (len(imgs) - 1)
    canvas = _Img.new("RGB", (w, h), (240, 242, 245))
    y = 0
    for im in imgs:
        canvas.paste(im, (0, y))
        y += im.height + gap
    buf = BytesIO(); canvas.save(buf, "JPEG", quality=86)
    return buf.getvalue()


def _menu_targets_from_step(action: str, expected: str = "") -> list[str]:
    """从步骤文案里提取目标菜单词，优先返回更具体的子菜单。"""
    text = " ".join(s for s in [action, expected] if s).replace("—", "-").replace("->", "-").replace("→", "-")
    text = re.sub(r"[，。；：,.!！?？()（）【】\[\]]", " ", text)
    parts: list[str] = []
    for seg in text.split():
        for item in seg.split("-"):
            item = item.strip(" '\"")
            if not item:
                continue
            if any(k in item for k in ("进入", "点击", "查看", "搜索区域", "筛选项", "查询", "支持", "正确")):
                item = re.sub(r"(点击进入|点击|进入|查看|核对|在|并执行查询|执行查询|搜索区域|筛选项|支持多选且查询结果正确|提供)", "", item)
                item = item.strip()
            for splitter in ("提供", "支持", "并", "且"):
                if splitter in item and len(item) > 4:
                    item = item.split(splitter, 1)[0].strip()
            if 1 < len(item) <= 12 and re.search(r"[\u4e00-\u9fffA-Za-z]", item):
                parts.append(item)
    seen: set[str] = set()
    ordered: list[str] = []
    for item in reversed(parts):
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return ordered


def _is_navigation_step(action: str, expected: str = "") -> bool:
    text = f"{action} {expected}"
    return any(k in text for k in ("进入", "打开", "跳转", "页面", "菜单", "列表页")) and any(
        k in text for k in ("-", "→", "->", "点击")
    )


_FIELD_LIST_RE = re.compile(r"展示以下\s*\d+\s*个字段")
_LOOK_KW = ("查看", "核对", "检查", "浏览")


def _is_field_inspection_step(action: str, expected: str = "") -> bool:
    """这一步是不是【核对字段展示】——判定时给"尽早 judge"提示的依据。

    判据看【预期在验什么】，不看动作里有没有操作动词：字段核对步骤的动作里往往也带着导航
    (如"搜索订单编号…进入订单详情→查看详情页全部字段")，按动作里有"搜索"就排除，会把
    真正的字段核对步骤挡在外面——线上 TC-ZN-0513 就是这样没收到提示、继续上下滚动到步数耗尽。
    """
    exp, act = expected or "", action or ""
    if _FIELD_LIST_RE.search(exp):          # 模板生成的字段清单用例，最明确的信号
        return True
    return "字段" in exp and any(k in act for k in _LOOK_KW)


def _is_filter_step(action: str, expected: str = "") -> bool:
    text = action or ""
    return any(k in text for k in ("筛选", "选择", "下拉", "查询", "多选"))


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


_OPT_SEL = ("li, [role=option], [role=menuitem], [role=treeitem], "
            "[class*=option], [class*=select-item], [class*=dropdown-item], [class*=cascader]")

# 给下拉选项打标记并回报"当前可见的选项"。与 date 动作同一套路：靠【点击后新出现】的元素
# 认出这次真正打开的那个面板——一个筛选区常有三四个下拉，全页搜"可见选项"必然张冠李戴。
_OPT_TAG_JS = """(sel) => {
    const vis = el => { const r = el.getBoundingClientRect();
        return r.width > 1 && r.height > 1 && getComputedStyle(el).visibility !== 'hidden'; };
    const all = [...document.querySelectorAll(sel)];
    all.forEach((el, i) => { if (!el.dataset.tpopt) el.dataset.tpopt = 'o' + i + '_' + (Date.now() % 100000); });
    return all.filter(el => vis(el) && (el.innerText || '').trim() && (el.innerText || '').trim().length < 60)
              .map(el => ({ idx: el.dataset.tpopt, text: (el.innerText || '').trim(),
                            on: /selected|checked|active/i.test(el.className || '')
                                || el.getAttribute('aria-selected') === 'true' }));
}"""


# 埋点/日志/静态资源等噪声接口：留它们的报文既无造数价值又占空间
_NOISE_API_RE = re.compile(
    r"(collectLog|/api/v1/log|/logstores|web_log|alert-alarm|/track|/report|/heartbeat|/monitor|"
    r"/analytics|/sls|/beacon|"
    r"\.js$|\.css$|\.png$|\.jpg$|\.svg$|\.woff)", re.I)
# 埋点上报的报文里常含手机号/姓名等个人信息，且对造数毫无价值。除了按 URL 过滤，
# 再按【报文特征】兜一层：实测阿里 SLS 的 /logstores/web_log 就漏过了 URL 过滤，
# 把 userPhone/userName 一并落了库。
_TELEMETRY_BODY_RE = re.compile(r'"__logs__"|"__topic__"|"userPhone"|"deviceId"|"sessionId"', re.I)
# 一律不落库的敏感字段名（造数不需要它们，落库反而是风险）
_SENSITIVE_RE = re.compile(
    r'("(?:[^"]*)?(?:password|passwd|pwd|secret|token|ticket|idCard|idNumber|bankCard|'
    r'verifyCode|smsCode)(?:[^"]*)?"\s*:\s*)("(?:[^"\\]|\\.)*"|\d+)', re.I)
_PHONE_RE = re.compile(r'"1[3-9]\d{9}"')      # 报文里裸露的手机号
_BODY_LIMIT = 4000


def _is_noise_api(url: str) -> bool:
    return bool(_NOISE_API_RE.search(url or ""))


def _redact_payload(text: str | None, limit: int = _BODY_LIMIT) -> str | None:
    """报文脱敏 + 截断，供造数能力沉淀使用。

    这些报文会落库并可能被重放，所以密码/令牌/证件号一律替换掉；超长的截断，
    避免把上传的大附件 base64 塞进数据库。
    """
    if not text:
        return None
    if _TELEMETRY_BODY_RE.search(str(text)):
        return None                     # 埋点上报：整条丢弃，别把个人信息落库
    s = _SENSITIVE_RE.sub(lambda m: m.group(1) + '"***"', str(text))
    s = _PHONE_RE.sub('"***"', s)       # 裸手机号(不在敏感字段名下的)也脱掉
    return s[:limit] + ("…(截断)" if len(s) > limit else "")


async def _fill_response_body(resp, rec: dict) -> None:
    """把响应体回填进已入列的记录（响应体只能异步读）。"""
    try:
        rec["response_body"] = _redact_payload(await resp.text(), limit=2000)
    except Exception:  # noqa: BLE001 流式/二进制响应读不了就算了
        pass


async def _web_tap(page, target: str | None, *, x: Any = None, y: Any = None,
                   scale_x: float = 1.0, scale_y: float = 1.0, reason: str = "") -> str:
    """点击：给了 target 就【按文案精准点】，只把坐标当"点哪一个"的线索。

    为什么不能只按坐标：表格操作列里"查看 完成 作废"这类链接彼此只隔三四十像素，AI 报的
    坐标偏一点就点到隔壁——线上就出现过要点"作废"却点开"完成"弹窗，还被判成产品缺陷的误报。
    _SYSTEM 里本就承诺「系统会用 target 精准定位点击」，但 web 端一直没实现，只按坐标点。
    """
    px = int(float(x) * scale_x) if x is not None else None
    py = int(float(y) * scale_y) if y is not None else None
    t = (str(target or "")).strip()

    if t and len(t) <= 24:
        try:
            # 文案完全相同的元素可能有多个(每行都有"作废")，取离 AI 给的坐标最近的那个：
            # 坐标定"哪一行"，文案定"哪一个按钮"，两者结合才准。
            hit = await page.evaluate(
                """([txt, pt]) => {
                    const norm = s => (s || '').replace(/\\s+/g, '');
                    const want = norm(txt);
                    const out = [];
                    for (const el of document.querySelectorAll(
                            'a,button,span,div,li,td,label,[role=button]')) {
                        if (norm(el.textContent) !== want) continue;
                        if (el.querySelector('a,button')) continue;   // 只要最内层可点元素
                        const r = el.getBoundingClientRect();
                        if (r.width < 1 || r.height < 1) continue;
                        const cx = r.x + r.width / 2, cy = r.y + r.height / 2;
                        const d = pt ? (cx - pt.x) ** 2 + (cy - pt.y) ** 2 : 0;
                        out.push({ x: cx, y: cy, d });
                    }
                    out.sort((a, b) => a.d - b.d);
                    return out[0] || null;
                }""",
                [t, ({"x": px, "y": py} if px is not None else None)],
            )
        except Exception:  # noqa: BLE001
            hit = None
        # 【校正必须有上限】同样的文案页面上别处也可能有(菜单、面包屑、别的页签)。
        # 不设上限就会"越过大半个屏幕"去点那个同名元素：实测出现过校正 1580px、
        # 直接跳到无关页面、整条用例跟着跑飞。超过阈值就认为不是同一个东西，按坐标点。
        _MAX_FIX = 220
        if hit and px is not None:
            dist = ((hit["x"] - px) ** 2 + (hit["y"] - py) ** 2) ** 0.5
            if dist > _MAX_FIX:
                await page.mouse.click(px, py)
                return (f"点击({x},{y}) {reason}"
                        f"（页面上「{t}」离该坐标 {int(dist)}px，判定不是同一个，已按坐标点）")
        if hit:
            await page.mouse.click(int(hit["x"]), int(hit["y"]))
            moved = int(((hit["x"] - px) ** 2 + (hit["y"] - py) ** 2) ** 0.5) if px is not None else 0
            return (f"点击「{t}」({int(hit['x'])},{int(hit['y'])})"
                    + (f"[按文案校正 {moved}px]" if moved > 4 else "") + f" {reason}")

    if px is None or py is None:
        return f"点击失败：既没给坐标也没找到「{t}」"
    await page.mouse.click(px, py)
    return f"点击({x},{y}) {reason}" + (f"（未按文案找到「{t}」，已按坐标点）" if t else "")


async def _web_select_options(page, options: list[str], *, x: Any = None, y: Any = None,
                              scale_x: float = 1.0, scale_y: float = 1.0, log=None) -> str:
    """在下拉/多选控件里按【文案】勾选若干选项，返回动作描述(含实际选中的项)。

    为什么要确定性动作：多选下拉是"一次点击选一项"，三个下拉各选两项就是 6+ 次点击，再加
    展开/收起，单步 20 次动作预算(_MAX_ACTIONS_PER_STEP)很快见底，用例还没走到搜索就判无法
    验证(线上 TC-ZN-0492 步骤3 正是如此)。这里一次调用把该下拉要选的都选完。

    选项只按【新出现的面板】里的文案匹配，不靠坐标猜——坐标猜会点到相邻下拉的选项上。
    """
    if x is None or y is None:
        return "选择失败：未给出下拉控件坐标"
    wanted = [str(o).strip() for o in (options or []) if str(o).strip()]
    if not wanted:
        return "选择失败：没有给出要选的选项文案"
    px, py = int(float(x) * scale_x), int(float(y) * scale_y)

    async def _visible_opts() -> list[dict]:
        try:
            return await page.evaluate(_OPT_TAG_JS, _OPT_SEL) or []
        except Exception:  # noqa: BLE001
            return []

    before = {o["idx"] for o in await _visible_opts()}
    await page.mouse.click(px, py)
    await asyncio.sleep(0.6)

    async def _panel_opts() -> list[dict]:
        """这次点开的面板里的选项；面板已开(多选连点)时退回当前全部可见选项。"""
        for _ in range(3):
            now = await _visible_opts()
            fresh = [o for o in now if o["idx"] not in before]
            if fresh:
                return fresh
            await asyncio.sleep(0.5)
        return [o for o in await _visible_opts() if o["idx"] not in before] or await _visible_opts()

    if not await _panel_opts():
        return (f"选择失败：点击({x},{y})后没有打开任何下拉面板——"
                "请确认坐标点在下拉控件上（不是它的标签文字），或先用 tap 展开再试")

    picked, missing = [], []
    for want in wanted:
        opts = await _panel_opts()
        if not opts:
            # 单选下拉选完会自动收起 → 重新点开再选下一个
            await page.mouse.click(px, py)
            await asyncio.sleep(0.6)
            opts = await _panel_opts()
        def _match(pool: list[dict]) -> dict | None:
            return next((o for o in pool if o["text"] == want), None) \
                or next((o for o in pool if want in o["text"] or o["text"] in want), None)

        hit = _match(opts)
        if not hit:
            # 面板里没有这个选项，常见原因是【当前开着的是别的下拉】(上一步展开过、或本次点击把
            # 目标下拉又收起了)。重点一次目标下拉再找一遍，找不到才算真没有。
            await page.mouse.click(px, py)
            await asyncio.sleep(0.6)
            hit = _match(await _panel_opts())
        if not hit:
            missing.append(want)
            continue
        try:
            await page.locator(f'[data-tpopt="{hit["idx"]}"]').first.click()
            picked.append(hit["text"])
            await asyncio.sleep(0.45)
        except Exception as e:  # noqa: BLE001
            missing.append(f"{want}(点击失败:{str(e)[:40]})")

    # 收起面板：用 Esc，不点空白处——点空白可能误触发页面上的别的控件
    try:
        await page.keyboard.press("Escape")
        await asyncio.sleep(0.35)
    except Exception:  # noqa: BLE001
        pass

    # 回读：控件上实际显示出来的已选项，作为"到底选中没有"的实据
    shown = None
    try:
        shown = await page.evaluate(
            """(pt) => {
                const el = document.elementFromPoint(pt.x, pt.y);
                if (!el) return null;
                // 只往上找 4 层内的选择器容器；找不到就用命中元素本身。
                // 不能无脑退到 parentElement——那常常一路退到 body，把整片筛选区的文字都读回来。
                let box = null;
                for (let cur = el, i = 0; cur && i < 5; cur = cur.parentElement, i++) {
                    if (/select|picker|dropdown|form-item/i.test(cur.className || '')) { box = cur; break; }
                }
                box = box || el;
                return (box.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120) || null;
            }""",
            {"x": px, "y": py},
        )
    except Exception:  # noqa: BLE001
        shown = None

    if log:
        log(f"　下拉已选 {picked}" + (f"，未找到 {missing}" if missing else "")
            + (f"，控件显示「{shown}」" if shown else ""))
    if not picked:
        return f"选择失败：下拉里没找到 {missing}（请确认坐标点在该下拉上、且选项文案与页面一致）"
    desc = f"下拉选中 {picked}"
    if missing:
        desc += f"；未找到 {missing}"
    return desc + (f"（控件显示「{shown}」）" if shown else "")


async def _web_set_date(page, start: str, end: str | None = None, *, x: Any = None, y: Any = None,
                        scale_x: float = 1.0, scale_y: float = 1.0, log=None) -> str:
    """把日期/日期区间【直接键入】日期控件，不去点日历格子。返回动作描述(含回读到的实际值)。

    为什么不点日历格子：区间选择器要【点两次】(开始 + 结束)，而"确定"按钮在两个都选定前是
    【禁用】的。AI 常常只点了开始日期就去点确定，连点几次毫无反应，步数耗尽判无法验证——
    线上 TC-ZN-0492 步骤3 正是如此(三次点在禁用的确定上)。键入还顺带解决"目标日期在别的
    月份"要反复点上/下月箭头的麻烦。

    键入后【回读输入框实际值】一并返回：各家日期组件的提交时机不一，回读能让 AI 和执行日志
    看到真正落进去的是什么，而不是假设成功。
    """
    if x is None or y is None:
        return "选日期失败：未给出日期控件坐标"
    px, py = int(float(x) * scale_x), int(float(y) * scale_y)

    # 给页面上每个 input 打稳定标记，并记下点击【之前】哪些是可见的。
    # 一个页面常有多个日期控件(创建时间/完成时间)，全页搜"可见日期输入框"会搜到【别的、已经填好的
    # 那个】——实测点"完成时间"却把值写进了"创建时间"，白填还浪费步数。点击后【新出现】的输入框
    # 才是这次真正打开的那个面板。
    _TAG_JS = """() => {
        const vis = el => { const r = el.getBoundingClientRect();
            return r.width > 1 && r.height > 1 && getComputedStyle(el).visibility !== 'hidden'; };
        const all = [...document.querySelectorAll('input')];
        all.forEach((el, i) => { if (!el.dataset.tpIdx) el.dataset.tpIdx = 'tp' + i + '_' + (Date.now() % 100000); });
        return all.filter(vis).map(el => ({ idx: el.dataset.tpIdx, ph: el.placeholder || '' }));
    }"""
    try:
        before = {r["idx"] for r in (await page.evaluate(_TAG_JS) or [])}
    except Exception:  # noqa: BLE001
        before = set()

    await page.mouse.click(px, py)          # 点开面板（日期输入框通常就在面板顶部）
    await asyncio.sleep(0.6)

    # 按"日期/时间"把面板里的输入框分好类。这类控件常把一个区间拆成【4 个】输入框：
    # 开始日期|开始时间|结束日期|结束时间——按下标盲填会把结束日期填进"开始时间"里。
    # 面板是动画展开的，首次查询常常还没渲染出来。查不到就多等一会儿重试——
    # 不重试的话本次动作会"填了个寂寞"，AI 见回读为空又会重复调用，白烧步数上限。
    def _is_date_ph(ph: str) -> bool:
        p = (ph or "").lower()
        return any(k in p for k in ("日期", "date", "开始", "结束", "start", "end")) and not _is_time_ph(ph)

    def _is_time_ph(ph: str) -> bool:
        p = (ph or "").lower()
        return "时间" in p or "time" in p

    date_inputs, time_inputs = [], []
    for _try in range(3):
        try:
            now = await page.evaluate(_TAG_JS) or []
        except Exception:  # noqa: BLE001
            now = []
        fresh = [r for r in now if r["idx"] not in before]
        # 优先用"点击后新出现的"输入框；没有新出现的(面板本就开着/组件复用同一批框)才退回全页匹配
        pool = [r for r in fresh if _is_date_ph(r["ph"]) or _is_time_ph(r["ph"])]
        if not pool:
            pool = [r for r in now if _is_date_ph(r["ph"]) or _is_time_ph(r["ph"])]
        date_inputs = [page.locator(f'[data-tp-idx="{r["idx"]}"]') for r in pool if not _is_time_ph(r["ph"])]
        time_inputs = [page.locator(f'[data-tp-idx="{r["idx"]}"]') for r in pool if _is_time_ph(r["ph"])]
        if date_inputs:
            break
        await asyncio.sleep(0.6)   # 面板是动画展开的，首次查询常常还没渲染出来

    if not date_inputs:
        return (f"选日期失败：在({x},{y})处没找到可填的日期输入框——"
                "请确认坐标点在日期控件上，或改用 tap 打开面板后再试")

    # 值可以带时间("2026-08-01 09:00:00")，拆开分别填进日期框和时间框
    def _split(v: str) -> tuple[str, str]:
        parts = str(v or "").strip().split()
        return parts[0], (parts[1] if len(parts) > 1 else "00:00:00")

    vals = [v for v in (start, end) if v and str(v).strip()]
    filled = []
    for i, v in enumerate(vals):
        d, t = _split(v)
        if i < len(date_inputs):
            # 用元素级 fill：它会【先聚焦该元素】再赋值并派发 input 事件。
            # 绝不能再用 keyboard 的 Control+a —— 焦点不在输入框时那会【全选整个页面】，
            # 随后键入全部落空（线上就是这么把整页刷成蓝色而一个字没进去的）。
            await date_inputs[i].fill(d)
            await date_inputs[i].press("Enter")
            await asyncio.sleep(0.4)
            filled.append(d)
        if i < len(time_inputs):
            try:
                await time_inputs[i].fill(t)
                await time_inputs[i].press("Enter")
                await asyncio.sleep(0.3)
            except Exception:  # noqa: BLE001 时间框非必填，失败不影响日期
                pass

    # 面板底部的"确定"：两端都填好后才会由禁用变可用，此时点它才真正生效
    confirmed = ""
    for name in ("确定", "确 定", "确认", "OK"):
        try:
            btn = page.locator(f"button:visible:has-text('{name}')").last
            if not await btn.count():
                continue
            # 不能用 Locator.is_enabled()：当前 Playwright 版本对 <button> 会抛
            # "Element is not an <input>…"，异常被吞掉就等于从不点确定（线上正是这样）。
            # 直接读 disabled 属性 + 常见的禁用类名。
            usable = await btn.evaluate(
                "e => !e.disabled && !/disabled/i.test(e.className || '')"
                " && e.getAttribute('aria-disabled') !== 'true'"
            )
            if usable:
                await btn.click()
                confirmed = "；已点确定"
                await asyncio.sleep(0.5)
                break
        except Exception:  # noqa: BLE001
            continue

    # 回读实际落进控件的值——不做这一步就只能假设成功
    actual = []
    for b in date_inputs + time_inputs:
        try:
            v = await b.input_value()
            if v:
                actual.append(v)
        except Exception:  # noqa: BLE001 面板已收起 → 元素失效，下面从触发框回读
            continue
    if not actual:
        # 点确定后面板收起，面板里的输入框随之失效。改从【触发控件】回读已提交的值，
        # 否则会回报"控件当前值 空"，AI 以为没填上又重复调用一遍。
        try:
            actual = await page.evaluate(
                """(pt) => {
                    const el = document.elementFromPoint(pt.x, pt.y);
                    const box = el && (el.closest('[class*=picker], [class*=date], [class*=range]')
                                       || el.parentElement);
                    return box ? [...box.querySelectorAll('input')].map(i => i.value).filter(Boolean) : [];
                }""",
                {"x": px, "y": py},
            ) or []
        except Exception:  # noqa: BLE001
            actual = []

    if log:
        log(f"　日期填入 {filled}，控件回读 {actual}{confirmed}")
    return (f"填日期 {' 至 '.join(filled)}（控件当前值 {actual or '空'}）{confirmed}"
            if filled else f"选日期失败：未能写入任何日期框（控件当前值 {actual or '空'}）")


async def _web_upload_files(page, count: int, size_kb: int, *, x: Any = None, y: Any = None,
                            scale_x: float = 1.0, scale_y: float = 1.0, log=None) -> str:
    """把 count 个约 size_kb 的测试图片喂给页面的文件输入框，返回本次动作描述。

    三条路径，从最稳到最兜底：
    1) 【首选】直接 set_input_files 到 input[type=file]：不弹任何对话框，隐藏的 input 也吃得下
       (绝大多数上传组件把真正的 input 藏在样式层下，这正是 AI 点不到的原因)。多选 input 一次
       带上全部文件；单选 input 就逐个塞。
    2) 页面上有多个 input 时，选【离 AI 给的坐标最近】的那个，避免把附件传进了别的上传区。
    3) 页面暂时没有 input(点了才创建)：用 expect_file_chooser 包住一次点击，接住选择框再塞文件。
    """
    files = upload_fixtures.make_images(count, size_kb)
    px = int(float(x) * scale_x) if x is not None else None
    py = int(float(y) * scale_y) if y is not None else None

    async def _pick_input():
        """页面里所有 file input，按离 (px,py) 的距离挑最近的；没坐标就取第一个可用的。"""
        inputs = await page.query_selector_all('input[type="file"]')
        if not inputs:
            return None, 0
        if px is None or py is None or len(inputs) == 1:
            return inputs[0], len(inputs)
        best, best_d = inputs[0], None
        for el in inputs:
            box = None
            try:
                box = await el.bounding_box()
                # 上传组件藏 input 的三种常见手法都会让它自己的 box 不可用作定位依据：
                # display:none → 没有 box；opacity:0/宽高为 0 → 退化的 box；left:-9999px → box 在
                # 屏幕外。后两种若照用，算出来的距离会大得离谱，把本该选中的输入框判输(实测坐标
                # 明明给的是第二个上传区，却把文件传进了第一个)。一律改用最近的可见祖先定位。
                if not box or box["width"] < 2 or box["height"] < 2 or box["x"] < 0 or box["y"] < 0:
                    handle = await el.evaluate_handle("e => e.closest('label,button,div') || e.parentElement")
                    anchor = handle.as_element()
                    if anchor:
                        box = await anchor.bounding_box() or box
            except Exception:  # noqa: BLE001
                box = None
            if not box:
                continue
            d = (box["x"] + box["width"] / 2 - px) ** 2 + (box["y"] + box["height"] / 2 - py) ** 2
            if best_d is None or d < best_d:
                best, best_d = el, d
        return best, len(inputs)

    el, total = await _pick_input()
    if el is not None:
        multiple = False
        try:
            multiple = bool(await el.evaluate("e => e.multiple"))
        except Exception:  # noqa: BLE001
            pass
        if multiple or len(files) == 1:
            await el.set_input_files(files)
        else:
            # 单选 input 传多张：逐次设置，让页面的 change 事件一张张累积
            for f in files:
                await el.set_input_files(f)
                await asyncio.sleep(0.4)
        if log:
            log(f"　已上传 {len(files)} 个文件（单张 {size_kb}KB，页面共 {total} 个上传输入框）")
        return f"上传 {len(files)} 个文件(单张{size_kb}KB)"

    # 页面还没有 input：点一下上传控件，用 file_chooser 接住
    if px is None or py is None:
        return f"上传失败：页面未找到文件输入框，且未给出上传控件坐标"
    try:
        async with page.expect_file_chooser(timeout=5000) as fc:
            await page.mouse.click(px, py)
        chooser = await fc.value
        await chooser.set_files(files if chooser.is_multiple() else files[0])
        if log:
            log(f"　点击上传控件后接住文件选择框，已选入 {len(files)} 个文件")
        return f"点击上传控件并选入 {len(files)} 个文件(单张{size_kb}KB)"
    except Exception as e:  # noqa: BLE001
        return f"上传失败：未能打开文件选择框（{e}）"


async def _web_input_text(page, text: str, *, x: Any = None, y: Any = None,
                          scale_x: float = 1.0, scale_y: float = 1.0) -> bool:
    """PC input action: focus the editable under the model's coordinates, clear it, then type real keys."""
    txt = str(text or "")
    clicked = False
    try:
        has_xy = x is not None and y is not None
        if has_xy:
            px, py = int(float(x) * scale_x), int(float(y) * scale_y)
            await page.mouse.click(px, py)
            clicked = True
            await asyncio.sleep(0.15)
            await page.evaluate(
                """({x, y}) => {
                    const visible = (el) => {
                      if (!el) return false;
                      const r = el.getBoundingClientRect();
                      return r.width > 2 && r.height > 2;
                    };
                    const editableFrom = (el) => {
                      let cur = el;
                      while (cur && cur !== document.body) {
                        if (cur.matches?.('input:not([disabled]),textarea:not([disabled]),[contenteditable="true"]')) return cur;
                        const child = cur.querySelector?.('input:not([disabled]),textarea:not([disabled]),[contenteditable="true"]');
                        if (visible(child)) return child;
                        cur = cur.parentElement;
                      }
                      return null;
                    };
                    const el = document.elementFromPoint(x, y);
                    const editable = editableFrom(el);
                    if (!editable) return false;
                    editable.focus();
                    try { editable.click(); } catch (_) {}
                    try {
                      if (typeof editable.select === 'function') editable.select();
                    } catch (_) {}
                    return true;
                }""",
                {"x": px, "y": py},
            )
    except Exception:
        pass

    try:
        focused = bool(await page.evaluate(
            """() => {
                const el = document.activeElement;
                if (!el) return false;
                if (el.matches?.('input,textarea')) {
                  try { el.select(); } catch (_) {}
                  return true;
                }
                if (el.isContentEditable) {
                  const range = document.createRange();
                  range.selectNodeContents(el);
                  const sel = window.getSelection();
                  sel.removeAllRanges();
                  sel.addRange(range);
                  return true;
                }
                return false;
            }"""
        ))
        if not focused:
            return False
        await page.keyboard.press("Control+A")
        await page.keyboard.press("Backspace")
        if txt:
            await page.keyboard.type(txt)
        await asyncio.sleep(0.35)
    except Exception:
        return False

    try:
        return bool(await page.evaluate(
            """(txt) => {
                const el = document.activeElement;
                if (!el) return false;
                const val = el.isContentEditable ? (el.innerText || el.textContent || '') : (el.value ?? '');
                if (txt === '') return val === '';
                return val === txt || val.includes(txt);
            }""",
            txt,
        ))
    except Exception:
        return clicked


# 执行时抽取页面交互元素(输入/按钮/链接/下拉)，用于自动补充页面结构缓存
_CAPTURE_JS = """
() => {
  const norm = (s) => (s||'').replace(/\\s+/g,' ').trim();
  const vis = (el) => !!(el && (el.offsetWidth || el.offsetHeight));
  // 取元素"自身"文本(优先直接文本节点/title)，避免把展开的子菜单文本一起吞进来
  const ownText = (el) => {
    const t = norm(Array.from(el.childNodes).filter(n=>n.nodeType===3).map(n=>n.textContent).join(''));
    return t || norm((el.innerText||'').split('\\n')[0]);
  };

  // ---- 左侧菜单树(带层级) → kind=menu ----
  const menuRoot = document.querySelector(
    '.ant-menu:not(.ant-menu-horizontal):not(.ant-dropdown-menu), .el-menu:not(.el-menu--horizontal), nav [role="menu"], aside');
  const menu = [];
  if (menuRoot) {
    menuRoot.querySelectorAll(
      '.ant-menu-submenu-title, .ant-menu-item, .el-submenu__title, .el-menu-item, [role="menuitem"]'
    ).forEach(el => {
      if (!vis(el)) return;
      const name = norm(el.getAttribute('title')||'') || ownText(el);
      if (!name || name.length > 24) return;
      const isSub = el.matches('.ant-menu-submenu-title, .el-submenu__title');
      let level = 0, p = el.parentElement;
      while (p && p !== menuRoot) {
        if (p.matches('.ant-menu-sub, .el-menu--inline, ul')) level++;
        p = p.parentElement;
      }
      const selected = /selected|is-active|menu-item-active/.test(el.className||'');
      menu.push({ name, type: isSub ? 'submenu' : 'item', level: Math.min(level,4), selected });
    });
  }

  // ---- 当前页名 = 选中的菜单项；描述 = 面包屑 或 分组/页 ----
  const sel = document.querySelector(
    '.ant-menu-item-selected, .el-menu-item.is-active, .ant-menu-item.ant-menu-item-active');
  let page_name = sel ? ownText(sel) : '';
  const bc = document.querySelector('.ant-breadcrumb, .el-breadcrumb, [class*="breadcrumb"]');
  let crumb = (bc && vis(bc)) ? norm(bc.innerText).replace(/\\s*[\\/>》]\\s*/g,' / ') : '';
  let parent = '';
  if (sel) {
    const ps = sel.closest('.ant-menu-submenu, .el-submenu');
    if (ps) { const t = ps.querySelector('.ant-menu-submenu-title, .el-submenu__title'); if (t) parent = ownText(t); }
  }
  let description = crumb || [parent, page_name].filter(Boolean).join(' / ');

  // ---- 控件(可交互元素扁平清单) → kind=controls ----
  const css = (el) => {
    if (el.id) return '#' + CSS.escape(el.id);
    const ph = el.getAttribute && el.getAttribute('placeholder');
    if (ph) return el.tagName.toLowerCase() + '[placeholder="' + ph + '"]';
    if (el.name) return el.tagName.toLowerCase() + '[name="' + el.name + '"]';
    return el.tagName.toLowerCase();
  };
  const controls = [], seen = new Set();
  document.querySelectorAll(
    'input,textarea,select,button,a[href],[role="button"],.ant-tabs-tab,.el-tabs__item,[role="tab"]'
  ).forEach(el => {
    if (!vis(el)) return;
    const name = (el.getAttribute('placeholder') || (el.innerText||'').trim()
      || el.getAttribute('aria-label') || el.name || el.value || '').replace(/\\s+/g,' ').trim();
    if (!name || name.length > 40) return;
    const type = el.tagName.toLowerCase();
    const k = name + '|' + type;
    if (seen.has(k)) return; seen.add(k);
    controls.push({ name, type, selector: css(el) });
  });

  return { page_name, description, menu: menu.slice(0,120), controls: controls.slice(0,80) };
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
        # 数据前置已把 ${别名.字段} 注入好的步骤（方案 §20），优先用它；否则用用例原步骤。
        steps = (ctx.extra or {}).get("steps_override") or getattr(case, "steps", None) or [
            {"action": getattr(case, "title", "执行用例"), "expected": getattr(case, "expected_result", "") or ""}
        ]
        title = getattr(case, "title", "")
        case_id = getattr(case, "id", "case")
        # AI 质量闭环：下发覆盖项，判定时回标 item_id（方案 12.1）
        from app.services.runners import coverage_evidence
        covered_items = getattr(case, "covered_items", None)
        cov_hint = coverage_evidence.covered_items_hint(covered_items)
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

        def _log(text: str, level: str = "info") -> None:
            # PC 执行的实时步骤日志：推到 execution_control，前端日志抽屉按 case_id 增量拉取
            try:
                execution_control.log(ctx.execution_id, text, level, case_id=case_id)
            except Exception:
                pass

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

                # 【文件选择框兜底】AI 点到上传控件时会弹出【操作系统原生文件对话框】——Playwright
                # 截不到它、它还阻塞页面，表现就是"点了没反应"，一路空点到步数耗尽(线上 TC-ZN-0489
                # 三个步骤全卡在这)。这里全局接管 filechooser：一旦弹出就立刻塞进测试图片，
                # 页面永远不会被原生框卡住。upload 动作走的是更精确的路径，这里只兜底。
                _last_upload: dict = {"count": 1, "size_kb": upload_fixtures.DEFAULT_SIZE_KB}

                def _on_file_chooser(chooser):
                    try:
                        files = upload_fixtures.make_images(
                            _last_upload["count"] if chooser.is_multiple() else 1,
                            _last_upload["size_kb"],
                        )
                        asyncio.ensure_future(chooser.set_files(files))
                        _log(f"　检测到文件选择框，已自动选入 {len(files)} 个测试文件")
                    except Exception as _fe:  # noqa: BLE001 兜底失败也不能崩执行
                        logger.warning("execution %s 文件选择框自动填充失败：%s", ctx.execution_id, _fe)

                page.on("filechooser", _on_file_chooser)

                # 阶段五：运行时接口采集 —— 监听 XHR/fetch 响应，供图谱 Page→API 运行时边回补
                api_calls: list[dict] = []
                _seen_api: set[str] = set()

                def _on_response(resp):
                    try:
                        req = resp.request
                        if req.resource_type not in ("xhr", "fetch"):
                            return
                        u = resp.url.split("?")[0]
                        key = f"{req.method}:{u}"
                        if key in _seen_api:
                            return
                        _seen_api.add(key)
                        rec = {"method": req.method, "url": u, "status": resp.status}
                        # 【写操作留报文】造数能力要靠它沉淀：只有拿到真实请求体，才能把一次
                        # "人工/AI 走通的新建"变成可重放的造数动作。只留写操作(GET 是查询、
                        # 量大且无造数价值)，截断防止把大附件塞进库，敏感字段一律不落库。
                        api_calls.append(rec)
                        if req.method in ("POST", "PUT", "PATCH", "DELETE") and not _is_noise_api(u):
                            try:
                                body = req.post_data     # 属性，同步可取
                            except Exception:  # noqa: BLE001 有些请求拿不到 body
                                body = None
                            if body:
                                rec["request_body"] = _redact_payload(body)
                            # 【查询参数也要留】不少写接口把参数放在 URL 上(@RequestParam)而不是
                            # 请求体里——作废接口就是 ?code=YCxxx、请求体为空。只存 post_data 的话
                            # 这类能力沉淀出来是个空壳，重放必然报"缺少参数"。
                            qs = resp.url.split("?", 1)[1] if "?" in resp.url else ""
                            if qs:
                                rec["query"] = _redact_payload(qs, limit=800)
                            # 响应体要 await，同步回调里取不了 → 调度任务回填进同一个 rec
                            asyncio.ensure_future(_fill_response_body(resp, rec))
                    except Exception:
                        pass

                def _watch_page(p):
                    try:
                        p.on("response", _on_response)
                    except Exception:
                        pass

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

                async def _capture():
                    """抓当前页面结构(按 url 去重)，供执行后写入页面结构缓存：
                    左侧菜单树(kind=menu,带层级) + 控件清单(kind=controls) + 具体页名/功能描述。"""
                    try:
                        data = await page.evaluate(_CAPTURE_JS)
                    except Exception:
                        return
                    if not data:
                        return
                    menu = data.get("menu") or []
                    controls = data.get("controls") or []
                    if not menu and not controls:
                        return
                    # 页名：优先"选中的菜单项"(SPA 里 document.title 常年是"主页"，不可靠)，退回 title/url
                    pname = (data.get("page_name") or "").strip()
                    if not pname:
                        try:
                            pname = (await page.title()) or page.url
                        except Exception:
                            pname = page.url
                    desc = (data.get("description") or "").strip()
                    regions: list[dict] = []
                    if menu:
                        regions.append({"name": "左侧菜单", "kind": "menu",
                                        "selector": ".ant-menu,.el-menu,aside", "elements": menu})
                    regions.append({"name": (pname[:60] or "页面"), "kind": "controls",
                                    "selector": "body", "elements": controls})
                    captures[page.url] = {"page_name": pname[:120], "description": desc[:200], "regions": regions}

                async def _click_visible_menu_target(targets: list[str]) -> str | None:
                    if not targets:
                        return None
                    try:
                        hit = await page.evaluate(
                            """(targets) => {
                                const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                                const visible = (el) => {
                                  if (!el) return false;
                                  const r = el.getBoundingClientRect();
                                  return !!(r.width > 4 && r.height > 4);
                                };
                                const sels = [
                                  '.ant-menu-item','.ant-menu-submenu-title','.el-menu-item','.el-submenu__title',
                                  '[role="menuitem"]','li','a','button','span','div'
                                ];
                                const nodes = Array.from(document.querySelectorAll(sels.join(','))).filter(visible);
                                for (const target of targets) {
                                  const exact = nodes.find((el) => norm(el.innerText) === target);
                                  if (exact) {
                                    const r = exact.getBoundingClientRect();
                                    return { target, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), exact: true };
                                  }
                                  const fuzzy = nodes.find((el) => {
                                    const t = norm(el.innerText);
                                    return t.includes(target) && t.length <= Math.max(target.length + 8, 18);
                                  });
                                  if (fuzzy) {
                                    const r = fuzzy.getBoundingClientRect();
                                    return { target, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), exact: false };
                                  }
                                }
                                return null;
                            }""",
                            targets,
                        )
                    except Exception:
                        hit = None
                    if not hit:
                        return None
                    try:
                        await page.mouse.click(int(hit.get("x", 0)), int(hit.get("y", 0)))
                        return f"兜底点击菜单「{hit.get('target', '')}」"
                    except Exception:
                        return None

                async def _scroll_click_menu(targets: list[str]) -> str | None:
                    """在左侧菜单里定位目标项——【即使它在侧边栏折叠线以下】：先 scrollIntoView 把它滚进视口再点。
                    解决"展开父级菜单后子项在折叠线以下、而垂直滚轮只滚主内容区、侧边栏永远滚不到"的顽疾。
                    命中更具体的子项优先(targets 已按具体→宽泛排序)。"""
                    if not targets:
                        return None
                    try:
                        hit = await page.evaluate(
                            """(targets) => {
                                const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                                const sels = [
                                  '.ant-menu-item','.ant-menu-submenu-title','.el-menu-item','.el-submenu__title',
                                  '[role="menuitem"]','li a','li'
                                ];
                                const nodes = Array.from(document.querySelectorAll(sels.join(',')));
                                const pick = (pred) => nodes.find(pred);
                                for (const target of targets) {
                                  let el = pick((e) => norm(e.innerText) === target);
                                  if (!el) el = pick((e) => { const t = norm(e.innerText); return t.includes(target) && t.length <= Math.max(target.length + 8, 18); });
                                  if (el) {
                                    try { el.scrollIntoView({block: 'center', inline: 'nearest'}); } catch (_) {}
                                    const r = el.getBoundingClientRect();
                                    return { target, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
                                  }
                                }
                                return null;
                            }""",
                            targets,
                        )
                    except Exception:
                        hit = None
                    if not hit:
                        return None
                    try:
                        await asyncio.sleep(0.25)   # 等 scrollIntoView 稳定，坐标才准
                        await page.mouse.click(int(hit.get("x", 0)), int(hit.get("y", 0)))
                        return f"侧栏滚动定位并点击菜单「{hit.get('target', '')}」"
                    except Exception:
                        return None

                async def _expand_menu_groups() -> int:
                    """展开左侧【所有未展开的子菜单组】(antd/element)，把嵌套的菜单项(如"资源中心"下的
                    "业务单据")从折叠状态露出来。返回本次展开的组数。用例导航写得不精确(漏中间层)时，
                    靠这个自己把菜单过一遍、找到真正的入口。"""
                    try:
                        return await page.evaluate(
                            """() => {
                                const titles = [...document.querySelectorAll('.ant-menu-submenu-title, .el-submenu__title')];
                                let n = 0;
                                for (const t of titles) {
                                  const sub = t.closest('.ant-menu-submenu, .el-submenu');
                                  const open = sub && (sub.classList.contains('ant-menu-submenu-open')
                                                       || sub.classList.contains('is-opened'));
                                  if (!open) { try { t.click(); n++; } catch (e) {} }
                                }
                                return n;
                            }"""
                        )
                    except Exception:
                        return 0

                async def _click_visible_field_target(targets: list[str]) -> str | None:
                    if not targets:
                        return None
                    try:
                        hit = await page.evaluate(
                            """(targets) => {
                                const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                                const visible = (el) => {
                                  if (!el) return false;
                                  const r = el.getBoundingClientRect();
                                  return !!(r.width > 8 && r.height > 8);
                                };
                                const textOf = (el) => norm(
                                  el?.getAttribute?.('placeholder')
                                  || el?.getAttribute?.('aria-label')
                                  || el?.innerText
                                  || el?.textContent
                                  || ''
                                );
                                const sels = [
                                  '.ant-select-selector','.ant-select','.el-select','.el-input','.el-input__wrapper',
                                  '[role="combobox"]','input','select','textarea','.ant-picker','.ant-input-affix-wrapper'
                                ];
                                const nodes = Array.from(document.querySelectorAll(sels.join(','))).filter(visible);
                                for (const target of targets) {
                                  const exact = nodes.find((el) => textOf(el) === target);
                                  if (exact) {
                                    const r = exact.getBoundingClientRect();
                                    return { target, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
                                  }
                                  const fuzzy = nodes.find((el) => {
                                    const t = textOf(el);
                                    return t.includes(target) && t.length <= Math.max(target.length + 12, 24);
                                  });
                                  if (fuzzy) {
                                    const r = fuzzy.getBoundingClientRect();
                                    return { target, x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) };
                                  }
                                }
                                return null;
                            }""",
                            targets,
                        )
                    except Exception:
                        hit = None
                    if not hit:
                        return None
                    try:
                        await page.mouse.click(int(hit.get("x", 0)), int(hit.get("y", 0)))
                        return f"兜底点击筛选控件「{hit.get('target', '')}」"
                    except Exception:
                        return None

                async def _progress_multiselect_filter(targets: list[str]) -> str | None:
                    if not targets:
                        return None
                    target = targets[0]
                    try:
                        result = await page.evaluate(
                            """(target) => {
                                const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();
                                const visible = (el) => {
                                  if (!el) return false;
                                  const r = el.getBoundingClientRect();
                                  return !!(r.width > 8 && r.height > 8);
                                };
                                const textOf = (el) => norm(
                                  el?.getAttribute?.('placeholder')
                                  || el?.getAttribute?.('aria-label')
                                  || el?.innerText
                                  || el?.textContent
                                  || ''
                                );
                                const field = Array.from(document.querySelectorAll(
                                  '.ant-select,.ant-select-selector,.el-select,[role="combobox"],input,select,.el-input,.el-input__wrapper'
                                )).find((el) => visible(el) && textOf(el).includes(target));
                                if (!field) return null;
                                const root = field.closest('.ant-select') || field;
                                const selected = Array.from(root.querySelectorAll(
                                  '.ant-select-selection-item,.ant-select-selection-overflow-item,.el-tag'
                                ))
                                  .map(textOf)
                                  .filter(Boolean);
                                const popup = document.querySelector('.ant-select-dropdown:not(.ant-select-dropdown-hidden), .el-select-dropdown:not([style*="display: none"])');
                                const optionSel = '.ant-select-item-option:not(.ant-select-item-option-disabled), .el-select-dropdown__item:not(.is-disabled)';
                                const options = popup ? Array.from(popup.querySelectorAll(optionSel)).filter(visible) : [];
                                if (!popup) {
                                  const r = (root.querySelector('.ant-select-selector,[role=\"combobox\"],input,.el-input__wrapper') || root).getBoundingClientRect();
                                  return { action: 'open', x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), selected };
                                }
                                if (selected.length < 2) {
                                  const next = options.find((el) => {
                                    const t = textOf(el);
                                    return t && !selected.includes(t);
                                  });
                                  if (next) {
                                    const r = next.getBoundingClientRect();
                                    return { action: 'pick', value: textOf(next), x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), selected };
                                  }
                                }
                                const searchBtn = Array.from(document.querySelectorAll('button,.ant-btn,[role=\"button\"]')).find((el) => visible(el) && /搜索|查询/.test(textOf(el)));
                                if (searchBtn) {
                                  const r = searchBtn.getBoundingClientRect();
                                  return { action: 'search', x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2), selected };
                                }
                                return { action: 'done', selected };
                            }""",
                            target,
                        )
                    except Exception:
                        result = None
                    if not result:
                        return None
                    action = result.get("action")
                    if action in {"open", "pick", "search"}:
                        try:
                            await page.mouse.click(int(result.get("x", 0)), int(result.get("y", 0)))
                        except Exception:
                            return None
                    if action == "open":
                        return f"兜底展开筛选控件「{target}」"
                    if action == "pick":
                        return f"兜底补选筛选项「{result.get('value', '')}」"
                    if action == "search":
                        return "兜底点击搜索"
                    if action == "done":
                        selected = "、".join(result.get("selected") or [])
                        return f"筛选项已选中：{selected}" if selected else None
                    return None

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
                    menu_targets = _menu_targets_from_step(s_action, s_expected)
                    is_navigation_step = _is_navigation_step(s_action, s_expected)
                    is_filter_step = _is_filter_step(s_action, s_expected)
                    # 上传步骤：把 upload 动作顶到 AI 眼前。光靠 _SYSTEM 里的通用规则，模型仍会
                    # 习惯性地去 tap 上传控件(那会弹出它看不见的系统文件框，连点几次耗光步数)。
                    # 宽表/多字段核对：判定时系统会自动送上【整页截图 + 表格横向分段截图】再让 AI 判一次
                    # (见下方 judge 分支)。AI 不知道这件事，就会试图靠自己横向滚动把所有列看全——
                    # 宽表根本滚不完，实测左右横跳 10 次耗光 20 步，本来能判出结果的用例反而判"无法验证"。
                    # 【只发给"看字段"的步骤】这条提示鼓励尽早 judge，对核对字段的步骤是对的，
                    # 但发给"选筛选项/填表单/提交"这类操作步骤会让 AI 活没干完就判定
                    # (线上 TC-ZN-0492 步骤3 因此漏填了完成时间、也没给异常类型补选第二个值)。
                    wide_table_hint = ""
                    if _is_field_inspection_step(s_action, s_expected):
                        wide_table_hint = (
                            "\n\n【核对字段不要靠滚动逐个找】你输出 judge 后，系统会自动把【整页完整截图】、"
                            "【表格横向分段】和【内部滚动区域的纵向分段】一起发给你再判一次——届时页面各处都看得到。"
                            "所以当前屏看不全时【直接 judge】即可，不要为了看全字段反复上下/左右滚动"
                            "(滚到头画面就不再变化，纯耗步数)。"
                            "\n【字段算不算「已展示」的口径】只要该字段的【值】在页面上可识别地呈现出来就算展示，"
                            "【不要求必须有独立的文字标签】。以下情形都【算展示】："
                            "①以标题/大字号呈现(如详情页顶部直接写着单号 YC20260814xxxx)；"
                            "②以状态徽章/标签呈现(如'待处理''已完成')；"
                            "③多个字段合并在一处呈现(如'租户/负责人'一栏同时给出两者，"
                            "就算'租户经理名称'和'负责人名称'都已展示)；"
                            "④在流程/审批轨迹卡片里体现(如当前节点、当前处理人)。"
                            "【上面判定锚点里的『存在某字段且展示对应值』是模板的统一措辞，"
                            "一律按『能看到该字段的值』来核对】——不要求页面上必须有这个文字标签。"
                            "例：锚点『存在工单状态字段』，页面标题旁有徽章「待处理」，即为满足；"
                            "锚点『存在当前节点、当前处理人字段』，审批轨迹里能看出停在哪个节点、"
                            "由谁办理，即为满足。"
                            "只有当你把【所有截图】都看过、确实找不到该字段的值时，才判它缺失，"
                            "并在 reason 里写清是哪几个字段、你在哪些图里找过。"
                        )
                    upload_block = ""
                    if _step_needs_photo_picker_context(s_action, s_expected, step.get("check_points")):
                        _n, _kb = upload_fixtures.parse_upload_request({}, f"{s_action} {s_expected}")
                        upload_block = (
                            "\n\n【本步涉及上传，必须用 upload 动作】不要去点上传控件等文件对话框——"
                            "PC 端点开的是系统原生文件框，你在截图里看不见它、页面也会被它卡住。"
                            f'直接输出 {{"action":"upload","x":上传控件中心x,"y":上传控件中心y,'
                            f'"count":{_n},"size_mb":{round(_kb / 1024, 2)}}}，'
                            "系统会自己造好测试图片并直接喂给页面。上传后看截图确认附件数量变化再判定。"
                        )
                    s_checks = step.get("check_points") or []
                    checks_result: list[dict] = []
                    shot: str | None = None
                    judge_full_png: bytes | None = None
                    judge_raw_pngs: list[bytes] = []   # 判定证据原图(整页+横向分段)，供结果查看
                    notes: list[str] = []
                    verdict, reason = None, ""
                    prev_png, same_count = None, 0
                    last_act_desc = ""    # 上一个动作的描述，供"零效果"反馈引用
                    last_act_type = ""    # 上一个动作类型：只有点击类才做零效果提示
                    nav_arrived = False   # 导航已确定性到达目标入口 → 后续别再反复点菜单
                    _log(f"▶ 步骤 {i}/{len(steps)}：{s_action}")

                    # ── PC 导航加速：进入本步前先【确定性】点侧栏菜单面包屑，能处理折叠线以下的子菜单，
                    # 免得 AI 靠滚主内容区盲找侧栏入口(垂直滚轮滚的是内容区、不是侧边栏)。仅导航步生效。
                    # targets 具体→宽泛排序：首轮父级(如资源中心)展开子菜单，次轮子级(如资源列表)滚进视口点中。
                    if is_navigation_step and menu_targets:
                        _prev_url = page.url
                        for _ in range(6):
                            _pre = await _scroll_click_menu(menu_targets)
                            if _pre:
                                notes.append(_pre)
                                _log(f"　{_pre}")
                                await asyncio.sleep(1.0)
                                # 到达叶子页的判据：点击导致路由 URL 变化(进了叶子菜单页)。
                                if page.url != _prev_url:
                                    nav_arrived = True
                                    _log("✅ 已进入目标菜单页，开始核对/操作")
                                    break
                                _prev_url = page.url
                            else:
                                # 目标不在当前(可见)菜单里 → 展开所有折叠的子菜单组，把嵌套项(如资源中心下的
                                # 业务单据)露出来再找。导航写得不精确/漏中间层时，靠这个自己把菜单过一遍找入口。
                                _n = await _expand_menu_groups()
                                if not _n:
                                    break   # 没有可展开的了，交回 AI 视觉探索
                                _log(f"　展开 {_n} 个折叠菜单组，继续找入口…")
                                await asyncio.sleep(0.8)

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
                        # 【零效果反馈】界面和上一轮一模一样，说明刚才那下【什么也没做成】——最常见的是
                        # 点在了禁用按钮上(如区间日期没选全时的"确定")。不告诉 AI 的话它会认定"没生效=没点到"
                        # 而对着同一坐标反复点，直到步数耗尽(线上 TC-ZN-0492 步骤3 连点三次禁用的确定)。
                        # 只对【点击类】动作提示，且【卡住提示优先】：
                        # - 滚动本来就常常滚到头没变化(看宽表尤其如此)，对它提示会把 AI 推着左右横跳；
                        # - stuck 时既有提示是"别再试了，直接判定"，两条一起下发会互相抵消，
                        #   实测让原本能判定的列表字段用例改为反复横向滚动直到步数耗尽。
                        no_effect_hint = ""
                        if same_count >= 1 and not stuck and last_act_type in ("tap", "double_tap", "long_press"):
                            no_effect_hint = (
                                f"\n\n【上一个动作「{last_act_desc[:50]}」执行后界面没有任何变化】"
                                "——多半点到了【禁用状态】的按钮或点空了，【别再点同一个位置】。"
                                "若这是区间日期控件，'确定'在开始和结束【都选定前一直是禁用】，"
                                "改用 action=date 直接键入两端。"
                            )
                        # 界面连续无变化：可能已到达目标(无需再操作)，也可能真卡住。
                        # 不直接判 blocked，而是让 AI 基于当前界面做一次最终判定(避免把"已到位"误判为卡住)。
                        stuck = same_count >= _STUCK_LIMIT

                        checks_text = ("\n判定锚点(逐条核对)：\n" + "\n".join(f"- {c}" for c in s_checks)) if s_checks else ""
                        # 覆盖项是【用例级】的，只在最后一步下发：早期步骤看到"提交后生成任务"这类
                        # 尚不可能满足的覆盖项，会把它当锚点判不通过，把前面步骤误伤成 blocked。
                        if i == len(steps):
                            checks_text += cov_hint  # 覆盖项提示（含 item_id），供 AI 回标
                        stuck_hint = (
                            "\n\n注意：界面已连续多次无变化。若当前界面已满足本步预期，请直接 judge=pass；"
                            "若明显不符合预期 judge=fail；若确实卡住/无法到达目标页 judge=blocked。不要再尝试无效操作。"
                            if stuck else ""
                        )
                        user = (
                            f"测试用例：{title}\n\n"
                            + _now_hint()
                            + _prior_steps_hint(ui_trace)
                            + f"当前步骤 {i}/{len(steps)}：\n操作：{s_action}\n预期：{s_expected}{checks_text}\n\n"
                            f"本步已执行：\n" + ("\n".join(notes) or "(无)") +
                            f"\n\n这是 PC 网页(宽{sw}高{sh}像素)。请输出下一步操作 JSON，或在可判定时输出 judge+verdict"
                            + ("(并在 checks 里逐条给出锚点核对结果)" if s_checks else "") + "。"
                            + stuck_hint + no_effect_hint + nav_block + upload_block + wide_table_hint
                        )
                        try:
                            raw = await provider.text_multi(_SYSTEM, user, [(b64, "image/jpeg")], _ACTION_MAX_TOKENS,
                                                            reasoning_effort=_reasoning_effort())
                        except Exception as e:
                            run_error = f"AI 决策失败：{e}"
                            _log(f"　AI 决策失败：{e}", "warn")
                            break
                        act = _extract_json(raw)
                        a = (act.get("action") or "").lower()
                        # 模型看宽表右侧隐藏列时常吐 action="drag"(拖横向滚动条)——本 runner 无 drag 动作，
                        # 会落到"未知动作 drag"空转。归一成 swipe 横向滚动(直接滚表格容器，比拖滚动条更稳)：
                        # 有 left/right 方向就沿用，没有则默认看右侧(约定 left=看右侧内容)。
                        if a == "drag":
                            a = "swipe"
                            if act.get("direction") not in ("left", "right", "up", "down"):
                                act["direction"] = "left"
                        # 模型常只吐 {"verdict":...,"reason":...} 而漏写 action="judge" → 当作判定处理，
                        # 否则会落到 else 分支反复"未取到有效动作，重试"空转到步数耗尽。
                        if a not in ("tap", "input", "enter", "paste", "long_press", "double_tap",
                                     "clear", "swipe", "back", "wait", "judge") \
                                and act.get("verdict") in ("pass", "fail", "blocked"):
                            a = "judge"
                        # 卡住时强制收敛为判定：即使 AI 仍想操作，也按其 verdict(没有则 blocked)结束本步。
                        # 注：曾改成"转 judge 走整页复核再下结论"，但那是一处【未经真实执行验证】的
                        # 行为改动，与另一处提示词改动叠在一起后用例反而更差，已退回原行为。
                        # 要再动这里，应当单独改、单独跑一次验证。
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
                                # 先把所有横向滚动容器归零：让表头与表体对齐(否则表头滚了、表体没滚→字段与值错位)
                                await page.evaluate(
                                    "()=>{document.querySelectorAll('*').forEach(e=>{"
                                    "if(e.scrollWidth>e.clientWidth+2) e.scrollLeft=0;});}")
                                await asyncio.sleep(0.25)
                                full_png = await page.screenshot(full_page=True)
                                judge_full_png = full_png
                                judge_raw_pngs = [full_png]   # 原分辨率证据图(整页+各横向分段)，供结果查看
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
                                        # 把【所有】横向溢出容器一起滚到同一位置(表头+表体一起动)，避免只滚了表头/表体导致错位
                                        await page.evaluate(
                                            "(x)=>{document.querySelectorAll('*').forEach(e=>{"
                                            "if(e.scrollWidth>e.clientWidth+2) e.scrollLeft=x;});}", k * cw)
                                        await asyncio.sleep(0.35)
                                        seg = await page.screenshot()
                                        judge_raw_pngs.append(seg)
                                        judge_imgs.append((_encode_at(Image.open(BytesIO(seg)), 1280), "image/jpeg"))
                                    await page.evaluate(
                                        "()=>{document.querySelectorAll('*').forEach(e=>{"
                                        "if(e.scrollWidth>e.clientWidth+2) e.scrollLeft=0;});}")
                                    await asyncio.sleep(0.2)
                                # 纵向同理：详情页常渲染在【内部可滚动容器】(抽屉/弹层/主内容区)里，
                                # full_page 截的是文档，容器只呈现它当前滚到的那一屏——AI 把它滚到底部后
                                # 判定，整页图里就只剩底部的附件/审批卡片，上半部分的字段一个都看不到，
                                # 于是判"详情页未展示要求的字段"(线上 TC-ZN-0510/0513 都是这么误判的)。
                                vinfo = await page.evaluate(
                                    "() => { let el=null,best=0;"
                                    "document.querySelectorAll('div,main,section,article').forEach(e=>{"
                                    "const o=e.scrollHeight-e.clientHeight;"
                                    "const oy=getComputedStyle(e).overflowY;"
                                    "if(o>best && e.clientHeight>200 && (oy==='auto'||oy==='scroll')){best=o;el=e;}});"
                                    "if(!el) return null; el.setAttribute('data-tpvs','1');"
                                    "return {sh:el.scrollHeight, ch:el.clientHeight}; }"
                                )
                                if vinfo and vinfo.get("sh", 0) > vinfo.get("ch", 0) + 30:
                                    ch = max(1, int(vinfo["ch"]))
                                    vn = min(4, -(-int(vinfo["sh"]) // ch))   # ceil，最多 4 段
                                    for k in range(vn):
                                        await page.evaluate(
                                            "(y)=>{const e=document.querySelector('[data-tpvs=\"1\"]');"
                                            "if(e) e.scrollTop=y;}", k * ch)
                                        await asyncio.sleep(0.35)
                                        seg = await page.screenshot()
                                        judge_raw_pngs.append(seg)
                                        judge_imgs.append((_encode_at(Image.open(BytesIO(seg)), 1280), "image/jpeg"))
                                    await page.evaluate(
                                        "()=>{const e=document.querySelector('[data-tpvs=\"1\"]');"
                                        "if(e){e.scrollTop=0;e.removeAttribute('data-tpvs');}}")
                                    await asyncio.sleep(0.2)
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
                                             + "、".join(cols) + "。") if cols else ""
                                # 第一条数据【每列的值】：按 X 坐标把表头与单元格对齐读取(兼容 antd 固定列/多子表)，
                                # 比截图可靠——截图里表头表体可能错位，DOM 按位置配对不会错。
                                try:
                                    row_rows = await page.evaluate(_TABLE_ROW_PAIRS_JS)
                                except Exception:
                                    row_rows = []
                                if row_rows and isinstance(row_rows[0], str):   # 兼容旧返回(单行)
                                    row_rows = [row_rows]
                                if row_rows:
                                    _rl = "\n".join(f"第{idx}行：" + " ｜ ".join(r[:50]) for idx, r in enumerate(row_rows[:5], 1))
                                    row_text = ("\n【列表前几行·各列真实值(DOM按列位置对齐读取，字段=值)】：\n" + _rl
                                                + "\n判定字段值是否有值/正确时【以此为准】，别只凭截图对齐(截图可能表头表体错位)。"
                                                + "\n若第1行不满足本步测试要求(如目标字段为空、或无法据它判定)，就依次看后续行，"
                                                "最多看前5行；任一行满足即以该行判定通过，5行都不满足才判不通过。")
                                else:
                                    row_text = ""
                                fuser = (
                                    f"测试用例：{title}\n\n" + _now_hint() + _prior_steps_hint(ui_trace)
                                    + f"当前步骤 {i}/{len(steps)}：\n操作：{s_action}\n预期：{s_expected}{checks_text}\n\n"
                                    f"下面是同一页面的 {len(judge_imgs)} 张可读截图：第1张是【整页纵向完整截图】，"
                                    "其余(若有)是【表格从左到右的横向分段】与【内部滚动区域从上到下的纵向分段】。"
                                    "字段可能分散在不同分段里，【必须逐张看完再判定】，"
                                    "不要因为第1张没看到某字段就判它缺失。请综合所有图"
                                    + cols_text + row_text +
                                    "\n看全整页所有字段/列后再判定本步骤，"
                                    "输出 judge+verdict" + ("(并在 checks 里逐条核对锚点)" if s_checks else "") + "。"
                                )
                                fraw = await provider.text_multi(_SYSTEM, fuser, judge_imgs, _ACTION_MAX_TOKENS,
                                                                 reasoning_effort=_reasoning_effort())
                                fact = _extract_json(fraw)
                                if (fact.get("action") or "").lower() == "judge":
                                    act = fact  # 用整页复核结果覆盖
                            except Exception:
                                pass
                            verdict = act.get("verdict") if act.get("verdict") in ("pass", "fail", "blocked") else "blocked"
                            reason = act.get("reason") or ""
                            raw_checks = act.get("checks") if isinstance(act.get("checks"), list) else []
                            checks_result = [{"point": str(c.get("point", "")), "ok": bool(c.get("ok")), "item_id": c.get("item_id")}
                                             for c in raw_checks if isinstance(c, dict)]
                            # 只有【本步自己的锚点】未满足才降级；用例级覆盖项不参与单步成败判定。
                            own_miss = coverage_evidence.step_own_failed_checks(checks_result, covered_items)
                            if verdict == "pass" and own_miss:
                                miss = "、".join(c["point"] for c in own_miss)
                                verdict = "blocked"
                                reason = (reason + f"；但锚点未满足：{miss}").strip("；")
                            # 结果证据图：宽表把"整页+各横向分段"纵向拼一张，保证目标列(如质保有效期)也在图里；
                            # 无横向分段时退回整页图。
                            _evidence = _stack_pngs_vertically(judge_raw_pngs) if len(judge_raw_pngs) > 1 \
                                else _judge_evidence_png(raw_png, judge_full_png)
                            shot = _save(_evidence or _judge_evidence_png(raw_png, judge_full_png))
                            break

                        try:
                            if a == "upload":
                                # 【确定性上传】直接把测试文件喂给页面的 input[type=file]，
                                # 全程不弹原生对话框、不依赖 AI 点得准，30 张也是一次调用带上。
                                _n, _kb = upload_fixtures.parse_upload_request(act, f"{s_action} {s_expected}")
                                _last_upload.update({"count": _n, "size_kb": _kb})
                                desc = await _web_upload_files(
                                    page, _n, _kb,
                                    x=act.get("x"), y=act.get("y"),
                                    scale_x=scale_x, scale_y=scale_y, log=_log,
                                )
                            elif a == "select":
                                # 选项来源：options 数组优先；只给了 text 就按常见分隔符拆开
                                _opts = act.get("options")
                                if not isinstance(_opts, list) or not _opts:
                                    _opts = [s for s in re.split(r"[、,，/;；]+", str(act.get("text") or "")) if s.strip()]
                                desc = await _web_select_options(
                                    page, _opts, x=act.get("x"), y=act.get("y"),
                                    scale_x=scale_x, scale_y=scale_y, log=_log,
                                )
                            elif a == "date":
                                desc = await _web_set_date(
                                    page, act.get("text") or "", act.get("text2") or None,
                                    x=act.get("x"), y=act.get("y"),
                                    scale_x=scale_x, scale_y=scale_y, log=_log,
                                )
                            elif a == "tap":
                                desc = await _web_tap(
                                    page, act.get("target"), x=act.get("x"), y=act.get("y"),
                                    scale_x=scale_x, scale_y=scale_y,
                                    reason=str(act.get("reason", "")),
                                )
                            elif a == "input":
                                _txt = str(act.get("text", ""))
                                _ok = await _web_input_text(
                                    page, _txt,
                                    x=act.get("x"), y=act.get("y"),
                                    scale_x=scale_x, scale_y=scale_y,
                                )
                                desc = f"输入「{_txt}」" + ("" if _ok else "（疑未进框）")
                            elif a == "enter":
                                # 触发搜索/提交：回车键
                                await page.keyboard.press("Enter")
                                desc = "回车/触发搜索"
                            elif a == "paste":
                                # 粘贴(验证“复制”功能)：先点坐标聚焦，再 Ctrl+V 粘贴真实剪贴板；
                                # 模型若直接给了 text 就退回键入该文本。
                                if act.get("x") is not None and act.get("y") is not None:
                                    await page.mouse.click(int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y))
                                _pt = str(act.get("text", ""))
                                if _pt:
                                    await page.keyboard.type(_pt)
                                    desc = f"粘贴「{_pt}」"
                                else:
                                    await page.keyboard.press("Control+V")
                                    desc = "粘贴剪贴板"
                            elif a == "long_press":
                                _lx, _ly = int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y)
                                await page.mouse.move(_lx, _ly)
                                await page.mouse.down()
                                await asyncio.sleep(0.9)
                                await page.mouse.up()
                                desc = f"长按({act.get('x')},{act.get('y')}) {act.get('reason', '')}"
                            elif a == "double_tap":
                                await page.mouse.dblclick(int(act.get("x", 0) * scale_x), int(act.get("y", 0) * scale_y))
                                desc = f"双击({act.get('x')},{act.get('y')})"
                            elif a == "clear":
                                # 清空输入框：复用 _web_input_text 的【先确认聚焦到可编辑元素】再全选删除。
                                # 不能直接 Control+A —— 坐标点空时它全选的是整个页面，然后照样报
                                # "清空成功"，把失败伪装成成功(与 date 动作此前踩的是同一个坑)。
                                _cleared = await _web_input_text(
                                    page, "", x=act.get("x"), y=act.get("y"),
                                    scale_x=scale_x, scale_y=scale_y,
                                )
                                desc = "清空输入框" if _cleared else "清空失败：该坐标处不是可编辑输入框"
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
                                # AI 未吐出合法动作(空/非法)：不当作已知动作，短暂等待后重试，
                                # 给 AI 一次拿新截图重来的机会，避免开局"未知动作"空转卡死。
                                await asyncio.sleep(1.0)
                                desc = "未取到有效动作，重试" if not a else f"未知动作 {a}"
                                auto_fix = None
                                if is_filter_step:
                                    auto_fix = await _progress_multiselect_filter(menu_targets)
                                if not auto_fix and is_filter_step:
                                    auto_fix = await _click_visible_field_target(menu_targets)
                                if not auto_fix and is_navigation_step and not nav_arrived:
                                    # 先试"滚动侧栏定位"(处理折叠线以下的菜单)，再退回原可见项点击。
                                    # 已确定性到达目标入口(nav_arrived)后不再重复点菜单——否则每个空动作都把
                                    # 目标项再点一遍，既拖时间又把日志刷成一堆重复的"点击资源列表"。
                                    auto_fix = await _scroll_click_menu(menu_targets) \
                                        or await _click_visible_menu_target(menu_targets)
                                if auto_fix:
                                    desc = f"{desc}；{auto_fix}"
                            try:
                                switched = await asyncio.wait_for(_adopt_new_page(), timeout=2.5)
                            except Exception:
                                switched = None
                            if switched:
                                desc = f"{desc}；{switched}"
                            notes.append(desc)
                            last_act_desc, last_act_type = desc, a
                            _log(f"　{desc}")
                            await asyncio.sleep(1.0)
                        except Exception as e:
                            notes.append(f"动作异常：{e}")
                            _log(f"　动作异常：{e}", "warn")

                    if run_error:
                        break
                    if verdict is None:
                        verdict, reason = "blocked", f"{_MAX_ACTIONS_PER_STEP} 步操作内仍无法判定本步骤"
                    _log(f"■ 步骤 {i} 判定：{_VERDICT_CN.get(verdict, verdict)}"
                         + (f"　{reason}" if reason else ""),
                         "info" if verdict == "pass" else "warn")
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
        # AI 质量闭环：聚合覆盖项级证据 + 运行时接口调用（图谱 Page→API 边数据源）
        checked_points = coverage_evidence.build_checked_points(covered_items, ui_trace)
        actual_pages = coverage_evidence.build_actual_visited_pages(page_caps)
        actual_apis = coverage_evidence.build_actual_api_calls(locals().get("api_calls"))
        if run_error and not ui_trace:
            return RunOutcome(status="error", duration_ms=duration_ms, error_message=run_error,
                              failure_type="env_error", screenshot_url=final_shot, ui_trace=ui_trace, page_captures=page_caps,
                              checked_points=checked_points or None, actual_visited_pages=actual_pages, actual_api_calls=actual_apis)

        non_pass = [st for st in ui_trace if st["verdict"] != "pass"]
        if ui_trace and not non_pass:
            return RunOutcome(status="passed", duration_ms=duration_ms, screenshot_url=final_shot, ui_trace=ui_trace, page_captures=page_caps,
                              checked_points=checked_points or None, actual_visited_pages=actual_pages, actual_api_calls=actual_apis)
        summary = "；".join(f"步骤{st['seq']}{st['verdict_cn']}：{st['reason']}" for st in non_pass)[:600]
        only_blocked = bool(non_pass) and all(st["verdict"] == "blocked" for st in non_pass)
        return RunOutcome(
            status="failed", duration_ms=duration_ms,
            error_message=(run_error or (("存在无法验证的步骤：" if only_blocked else "存在不符合预期的步骤：") + summary)),
            failure_type="env_error" if (only_blocked or run_error) else "real_defect",
            screenshot_url=final_shot, ui_trace=ui_trace, page_captures=page_caps,
            checked_points=checked_points or None, actual_visited_pages=actual_pages, actual_api_calls=actual_apis,
        )

    def _prepare(self, case: Any, ctx: RunContext) -> Path:
        raise NotImplementedError

    async def _execute(self, workdir, case: Any, ctx: RunContext) -> dict:
        raise NotImplementedError
