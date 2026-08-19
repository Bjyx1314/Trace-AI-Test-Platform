"""用例复用判断（方案 10.1/10.3，阶段四）。

给定一批建议覆盖项 → 检索已有用例(覆盖项向量 + 页面/接口节点匹配) → 分三类：
- 可复用：已有用例覆盖相同覆盖项且稳定通过；
- 需调整：命中用例但页面结构因变更改变(PageCacheDiff)，定位器可能失效；
- 需新增：无关联用例的覆盖项。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TestCase, CoveredItemVec
from app.services import covered_item_vec


async def judge(
    db: AsyncSession, *, project_id: str | None, requirement_id: str | None,
    suggested_items: list[dict], changed_url_patterns: set[str] | None = None,
) -> dict:
    """返回 {reusable: [...], need_adjust: [...], need_new: [...]}。

    changed_url_patterns：本次变更导致结构变化的页面(来自 PageCacheDiff / 影响分析)，
    命中这些页面的用例标 need_adjust。
    """
    changed = changed_url_patterns or set()
    reusable: list[dict] = []
    need_adjust: list[dict] = []
    need_new: list[dict] = []

    for item in suggested_items:
        hits = await covered_item_vec.search_similar(
            db, project_id=project_id, query_item=item, top_k=3, min_sim=0.82,
        )
        if not hits:
            need_new.append({"item": item.get("name") or item.get("item"), "reason": "无关联用例的覆盖项"})
            continue
        # 取最相似命中用例
        best = hits[0]
        tc = await db.get(TestCase, best["case_id"])
        if not tc:
            need_new.append({"item": item.get("name"), "reason": "关联用例已删除"})
            continue
        # 页面结构变化 → 需调整
        page_changed = False
        for node in (tc.affected_page_nodes or []):
            if any(pat and pat in str(node) for pat in changed):
                page_changed = True
                break
        entry = {"item": item.get("name") or item.get("item"), "case_id": tc.case_id,
                 "case_uuid": tc.id, "title": tc.title, "sim": round(best["sim"], 3)}
        if page_changed:
            entry["reason"] = "命中用例所在页面结构本次有变化，定位器可能失效（PageCacheDiff）"
            need_adjust.append(entry)
        elif tc.last_status == "passed":
            reusable.append(entry)
        else:
            entry["reason"] = f"命中用例最近状态={tc.last_status}，建议复核"
            reusable.append(entry)

    return {"reusable": reusable, "need_adjust": need_adjust, "need_new": need_new}


async def mark_cases_need_adjust_by_page(db: AsyncSession, project_id: str, url_pattern: str) -> int:
    """PageCacheDiff pending → 命中该 url_pattern 的用例标 regression_flag=need_adjust。返回标记数。"""
    # 用例的 affected_page_nodes 里含该 url_pattern → 需调整
    vec_rows = (await db.execute(
        select(CoveredItemVec.case_id).where(CoveredItemVec.project_id == project_id).distinct()
    )).scalars().all()
    marked = 0
    # 简化：扫该项目未删用例，affected_page_nodes 命中 url_pattern 即标
    cases = (await db.execute(
        select(TestCase).where(TestCase.project_id == project_id, TestCase.deleted_at.is_(None))
    )).scalars().all()
    for tc in cases:
        nodes = tc.affected_page_nodes or []
        if any(url_pattern and url_pattern in str(n) for n in nodes):
            if tc.regression_flag != "need_adjust":
                tc.regression_flag = "need_adjust"
                marked += 1
    if marked:
        await db.commit()
    return marked
