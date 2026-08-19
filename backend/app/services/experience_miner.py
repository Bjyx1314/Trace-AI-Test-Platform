"""经验沉淀（方案 6.2.1）：从反馈闭环自动长出经验候选，不靠人工预建。

来源信号：
1. 测试 Review 新增覆盖项（reason 说明为何补）→ 候选；
2. 用例执行发现的真实 Bug → 高置信经验；
3. 线上逃逸问题（金标准，逃逸回溯时沉淀）。

去重：同一用例+同名覆盖项的反馈只沉淀一条经验（ReviewFeedback.experience_id 标记已沉淀）。
转正在 experience_lifecycle 里做（adopt≥2 或关联 Bug）。
"""
from __future__ import annotations
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Experience, ReviewFeedback, TestCase, Defect
from app.services import embedding as emb

logger = logging.getLogger(__name__)


def _trigger_context_from_case(tc: TestCase | None, item: dict | None) -> dict:
    ctx = {
        "affected_features": (tc.modules if tc else []) or [],
        "service_node_ids": [],
        "api_node_ids": list((tc.affected_api_nodes if tc else None) or []),
        "risk_tags": list((item or {}).get("risk_tags") or (tc.risk_tags if tc else []) or []),
    }
    return ctx


def _embed_input(title: str, ctx: dict) -> str:
    parts = [title]
    parts += ctx.get("affected_features") or []
    parts += ctx.get("risk_tags") or []
    return " ".join(str(p) for p in parts if p)


def _norm(s: str | None) -> str:
    """归一化：去首尾空白 + 内部连续空白折叠，用于判断是否实质变化。"""
    return " ".join((s or "").split())


def _edit_is_meaningful(before: dict | None, after: dict | None, reason: str | None) -> bool:
    """修改覆盖项是否值得沉淀经验：带 reason，或名称/预期归一化后确有变化。
    纯措辞/空白/大小写差异不沉淀，避免噪声。"""
    if _norm(reason):
        return True
    b, a = before or {}, after or {}
    if _norm(b.get("name")) != _norm(a.get("name")):
        return True
    if _norm(b.get("expected")) != _norm(a.get("expected")):
        return True
    return False


async def sediment_from_feedback(db: AsyncSession, feedback_id: str) -> str | None:
    """从一条「新增覆盖项」Review 反馈沉淀经验候选。返回 experience_id 或 None。"""
    fb = await db.get(ReviewFeedback, feedback_id)
    if not fb or fb.action != "add_item" or fb.experience_id:
        return None
    added = fb.after or {}
    item_name = added.get("name") or (added.get("added_item") if isinstance(added.get("added_item"), str) else None)
    if not item_name:
        return None
    tc = await db.get(TestCase, fb.test_case_id) if fb.test_case_id else None
    ctx = _trigger_context_from_case(tc, added)
    title = f"补充覆盖项：{item_name}"

    # 同项目下同名覆盖项已有经验则复用（累计信号），否则建候选
    existing = (await db.execute(
        select(Experience).where(
            Experience.title == title,
            Experience.project_id == (tc.project_id if tc else None),
        )
    )).scalar_one_or_none()
    if existing:
        fb.experience_id = existing.id
        await db.commit()
        return existing.id

    exp = Experience(
        id=str(uuid.uuid4()), title=title,
        project_id=tc.project_id if tc else None,
        trigger_context=ctx,
        suggested_covered_items=[item_name],
        source="tester_feedback",
        reason=fb.reason or f"测试在 Review 时补充了「{item_name}」",
        evidence={"found_bug": False},
        stats={"hit_count": 0, "adopt_count": 0, "reject_count": 0},
        confidence=0.5, status="candidate",
    )
    vec = await emb.embed_text(_embed_input(title, ctx))
    if vec:
        exp.embedding = vec
    db.add(exp)
    fb.experience_id = exp.id
    await db.commit()
    logger.info("沉淀经验候选 %s：%s", exp.id, title)
    return exp.id


async def sediment_from_edit(db: AsyncSession, feedback_id: str) -> str | None:
    """从一条「修改覆盖项」Review 反馈沉淀经验候选（AI 判定被人纠正的信号）。"""
    fb = await db.get(ReviewFeedback, feedback_id)
    if not fb or fb.action != "edit_item" or fb.experience_id:
        return None
    before, after = fb.before or {}, fb.after or {}
    if not _edit_is_meaningful(before, after, fb.reason):
        return None
    item_name = after.get("name")
    if not item_name:
        return None
    tc = await db.get(TestCase, fb.test_case_id) if fb.test_case_id else None
    ctx = _trigger_context_from_case(tc, after)
    title = f"覆盖项纠正：{item_name}"
    existing = (await db.execute(
        select(Experience).where(
            Experience.title == title,
            Experience.project_id == (tc.project_id if tc else None),
        )
    )).scalar_one_or_none()
    if existing:
        fb.experience_id = existing.id
        await db.commit()
        return existing.id

    before_name = before.get("name") or "(空)"
    reason = fb.reason or f"测试将「{before_name}」修正为「{item_name}」，说明 AI 原判定不准"
    exp = Experience(
        id=str(uuid.uuid4()), title=title,
        project_id=tc.project_id if tc else None,
        trigger_context=ctx,
        suggested_covered_items=[item_name],
        source="tester_feedback",
        reason=reason,
        evidence={"found_bug": False, "corrected_from": before_name},
        stats={"hit_count": 0, "adopt_count": 0, "reject_count": 0},
        confidence=0.5, status="candidate",
    )
    vec = await emb.embed_text(_embed_input(title, ctx))
    if vec:
        exp.embedding = vec
    db.add(exp)
    fb.experience_id = exp.id
    await db.commit()
    logger.info("沉淀纠正经验候选 %s：%s", exp.id, title)
    return exp.id


async def sediment_from_bug(db: AsyncSession, defect_id: str) -> str | None:
    """从确认的真实缺陷沉淀高置信经验（evidence.found_bug=true）。"""
    d = await db.get(Defect, defect_id)
    if not d:
        return None
    tc = await db.get(TestCase, d.test_case_id) if d.test_case_id else None
    ctx = _trigger_context_from_case(tc, None)
    title = f"缺陷经验：{d.title[:60]}"
    existing = (await db.execute(
        select(Experience).where(Experience.title == title)
    )).scalar_one_or_none()
    if existing:
        return existing.id
    root = (getattr(d, "root_cause", None) or "").strip()
    exp = Experience(
        id=str(uuid.uuid4()), title=title,
        project_id=tc.project_id if tc else None,
        trigger_context=ctx,
        suggested_covered_items=[c.get("covered_item_name") for c in (d.covered_item_ids or []) if isinstance(c, dict)] or [d.title[:40]],
        source="found_bug" if d.source != "production" else "production_issue",
        reason=root or f"执行/线上发现缺陷：{d.title[:80]}",
        evidence={"found_bug": True, "bug_id": d.id, "root_cause": root or None},
        stats={"hit_count": 0, "adopt_count": 0, "reject_count": 0},
        confidence=0.8, status="active",  # 关联已确认 Bug 直接转正
    )
    vec = await emb.embed_text(_embed_input(title, ctx))
    if vec:
        exp.embedding = vec
    db.add(exp)
    await db.commit()
    logger.info("沉淀高置信经验 %s（关联缺陷 %s）", exp.id, defect_id)
    return exp.id
