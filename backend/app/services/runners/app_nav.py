"""App 导航加速：路径缓存(①) + 原生 scroll.to 直达(②)。

目标：App 用例每次执行都靠 AI 视觉从零盲滑找入口（如"工作台→资源列表"滑十几次、还常翻错页）。
用两招加速：
  ① 导航路径缓存：某条用例成功到达目标入口后，记下"入口页 + 大致滚动次数 + 附近参照文案"，
     下次同 App 同目标先把这条经验作为【提示】注入给 AI（坚持朝该方向滚、别浅尝换页），命中率高。
  ② 原生 scroll.to：进入入口后，先尝试 uiautomator2 原生 `scroll.to(text=目标)` 直接滚到目标控件
     并点击——原生控件(RecyclerView 等)时零 AI 开销直达；自绘(Flutter/RN)取不到节点则回退视觉。
"""
from __future__ import annotations

import asyncio
import logging
import re

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models import AppNavRecipe, AppStepRecipe

logger = logging.getLogger(__name__)

_STEP_SIG_MAX = 180          # 步骤签名截断长度
_STEP_ACTIONS_MAX = 14       # 只沉淀"较干净"的成功(操作数≤此值)，避免把一堆试错噪声也记下来


def step_signature(s_action: str) -> str:
    """把步骤操作文本归一化成签名(去空白/标点、截断)，作为同一步骤的复用键。"""
    t = re.sub(r"\s+", "", s_action or "")
    t = re.sub(r"[，,。；;、：:（）()\"'“”]", "", t)
    return t[:_STEP_SIG_MAX]


async def load_step_recipe(app_pkg: str | None, sig: str | None) -> list | None:
    if not app_pkg or not sig:
        return None
    try:
        async with AsyncSessionLocal() as db:
            r = (await db.execute(
                select(AppStepRecipe).where(
                    AppStepRecipe.app_pkg == app_pkg, AppStepRecipe.step_sig == sig
                )
            )).scalar_one_or_none()
            return (r.actions or None) if r else None
    except Exception as e:  # noqa: BLE001
        logger.info("读步骤操作经验失败(忽略)：%s", e)
        return None


async def save_step_recipe(app_pkg: str | None, sig: str | None, actions: list) -> None:
    """沉淀某步骤的成功操作序列。只记较干净的成功(操作数≤上限)，去掉连续重复动作。"""
    if not app_pkg or not sig or not actions:
        return
    # 去连续重复(同类型+同目标)，压缩噪声
    cleaned: list = []
    for a in actions:
        if cleaned and cleaned[-1] == a:
            continue
        cleaned.append(a)
    if not cleaned or len(cleaned) > _STEP_ACTIONS_MAX:
        return
    try:
        async with AsyncSessionLocal() as db:
            r = (await db.execute(
                select(AppStepRecipe).where(
                    AppStepRecipe.app_pkg == app_pkg, AppStepRecipe.step_sig == sig
                )
            )).scalar_one_or_none()
            if r:
                r.actions = cleaned
                r.n_actions = len(cleaned)
                r.hits = (r.hits or 1) + 1
            else:
                db.add(AppStepRecipe(app_pkg=app_pkg, step_sig=sig,
                                     actions=cleaned, n_actions=len(cleaned)))
            await db.commit()
    except Exception as e:  # noqa: BLE001
        logger.info("写步骤操作经验失败(忽略)：%s", e)

# 路径里要丢掉的通用词/动词（不是页面名）。
# 【严禁】含单字助词"中/里/的/在"或"首页"——它们是"服务中心/资源中心/首页待办"等正当页面名的一部分，
# 用 seg.replace 无差别删除会把"服务中心"抠成"服务心"、"资源中心"→"资产心"，导致原生/OCR 找不到入口、
# 退回视觉瞎找还常撞错入口。只丢确定不是页面名的动词/通用后缀。
_DROP_TOKENS = ("打开", "进入", "点击", "点开", "找到", "查看", "页面", "菜单", "App", "app")
# 一段路径文案的最大长度（防止把整句操作当成页面名）
_MAX_LABEL = 10


def nav_path_from_step(s_action: str, s_expected: str = "") -> list[str]:
    """从步骤文案里解析导航面包屑，如"打开Android AppApp → 工作台 → 资源列表，确认…" → ["工作台","资源列表"]。

    只认箭头链(→ > ›)——这是路径的明确信号；没有箭头则返回空(不猜)。取每段核心页面名，
    丢掉 App 名/通用动词/逗号后的说明子句。最后一个即目标入口。"""
    text = s_action or ""
    # 只取第一个逗号/句号前的导航短语（逗号后通常是"确认…/输入…"等操作说明，不是路径）
    head = re.split(r"[，,。;；]", text, maxsplit=1)[0]
    if not re.search(r"[→>›]", head):
        return []
    parts = re.split(r"[→>›]", head)
    labels: list[str] = []
    for p in parts:
        raw = p.strip()
        # "打开 Android AppApp"这类是【启动 App】段(不是页内导航)，整段丢掉——否则原生导航会去点 App 名而卡住
        is_app_open = ("app" in raw.lower()) or raw.startswith("打开")
        seg = raw
        for w in _DROP_TOKENS:
            seg = seg.replace(w, "")
        seg = re.sub(r"[^一-龥A-Za-z0-9]", "", seg).strip()
        if not seg or len(seg) > _MAX_LABEL or is_app_open:
            continue
        if seg not in labels:
            labels.append(seg)
    return labels


async def native_navigate(d, path: list[str], per_label_wait: float = 1.2) -> tuple[int, str]:
    """② 原生逐段导航：对 path 每一段，先原生按文案点击；点不到就原生 scroll.to 滚到该控件再点。
    返回 (成功到达的段数, 说明)。全程在线程里跑(u2 同步 API)。任一段原生取不到→停在该段，交回视觉。"""
    reached = 0
    notes: list[str] = []

    def _tap(label: str) -> bool:
        for sel in (lambda: d(text=label), lambda: d(textContains=label), lambda: d(description=label)):
            try:
                obj = sel()
                if obj.exists:
                    obj.click()
                    return True
            except Exception:
                continue
        return False

    def _scroll_to_tap(label: str) -> bool:
        # 原生可滚容器里滚到目标控件（自绘应用会失败→返回 False）
        try:
            if d(scrollable=True).scroll.to(textContains=label):
                return _tap(label)
        except Exception:
            return False
        return False

    for label in path:
        ok = await asyncio.to_thread(_tap, label)
        if not ok:
            ok = await asyncio.to_thread(_scroll_to_tap, label)
        if not ok:
            break
        reached += 1
        notes.append(f"原生直达「{label}」")
        await asyncio.sleep(per_label_wait)
    return reached, "；".join(notes)


async def load(app_pkg: str | None, target: str | None) -> dict | None:
    if not app_pkg or not target:
        return None
    try:
        async with AsyncSessionLocal() as db:
            r = (await db.execute(
                select(AppNavRecipe).where(
                    AppNavRecipe.app_pkg == app_pkg, AppNavRecipe.target == target
                )
            )).scalar_one_or_none()
            if not r:
                return None
            return {"entry": r.entry, "path": r.path or [], "swipes": r.swipes,
                    "direction": r.direction, "near_text": r.near_text, "hits": r.hits}
    except Exception as e:  # noqa: BLE001 缓存读失败不阻断执行
        logger.info("读导航缓存失败(忽略)：%s", e)
        return None


async def save(app_pkg: str | None, target: str | None, *, entry: str | None,
               path: list[str], swipes: int, direction: str = "up",
               near_text: str | None = None) -> None:
    if not app_pkg or not target:
        return
    try:
        async with AsyncSessionLocal() as db:
            r = (await db.execute(
                select(AppNavRecipe).where(
                    AppNavRecipe.app_pkg == app_pkg, AppNavRecipe.target == target
                )
            )).scalar_one_or_none()
            if r:
                r.entry = entry or r.entry
                r.path = path or r.path
                r.swipes = swipes
                r.direction = direction
                if near_text:
                    r.near_text = near_text
                r.hits = (r.hits or 1) + 1
            else:
                db.add(AppNavRecipe(app_pkg=app_pkg, target=target, entry=entry,
                                    path=path or None, swipes=swipes, direction=direction,
                                    near_text=near_text))
            await db.commit()
    except Exception as e:  # noqa: BLE001 缓存写失败不阻断执行
        logger.info("写导航缓存失败(忽略)：%s", e)


def hint(recipe: dict | None) -> str:
    """把缓存经验拼成一句给 AI 的导航提示；无缓存返回空串。"""
    if not recipe:
        return ""
    tgt_from = f"进入『{recipe['entry']}』后，" if recipe.get("entry") else ""
    near = f"（在『{recipe['near_text']}』附近）" if recipe.get("near_text") else ""
    n = recipe.get("swipes") or 0
    dir_cn = "向下" if (recipe.get("direction") or "up") == "up" else "向上"
    scroll = f"{dir_cn}滚动约 {n} 次即可见" if n else f"{dir_cn}滚动查找"
    return (f"\n【导航提示·来自上次成功路径】{tgt_from}目标入口{near}需{scroll}；"
            f"请坚持朝该方向滚动定位，不要浅尝就返回/改走别的页面。\n")
