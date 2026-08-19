"""质量闭环度量指标（方案 15）。没有指标的闭环无法证明自己在变好。

五项：影响面准确率、AI 用例 Review 修改率、经验召回采纳率、用例复用率、覆盖项验证率。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import ReviewFeedback, TestCase, ChangeImpactRecord

router = APIRouter(prefix="/api/metrics", tags=["metrics"])


def _rate(a: int, b: int) -> float:
    return round(a / b, 4) if b else 0.0


@router.get("/quality-loop")
async def quality_loop_metrics(project_id: str | None = None, db: AsyncSession = Depends(get_db)):
    # 1. 影响面准确率：impact_scope 反馈采纳 / 总
    fb_total_scope = (await db.execute(
        select(func.count()).select_from(ReviewFeedback).where(ReviewFeedback.target_type == "impact_scope")
    )).scalar_one()
    fb_accept_scope = (await db.execute(
        select(func.count()).select_from(ReviewFeedback).where(
            ReviewFeedback.target_type == "impact_scope", ReviewFeedback.action == "accept")
    )).scalar_one()

    # 2. AI 用例 Review 修改率：被 edit/delete 的用例覆盖项反馈 / 有反馈的用例
    fb_edit = (await db.execute(
        select(func.count()).select_from(ReviewFeedback).where(
            ReviewFeedback.target_type == "covered_item", ReviewFeedback.action.in_(("edit_item", "delete_item")))
    )).scalar_one()
    fb_all_item = (await db.execute(
        select(func.count()).select_from(ReviewFeedback).where(ReviewFeedback.target_type == "covered_item")
    )).scalar_one()

    # 3. 经验召回采纳率：experience_hit accept / 总
    exp_total = (await db.execute(
        select(func.count()).select_from(ReviewFeedback).where(ReviewFeedback.target_type == "experience_hit")
    )).scalar_one()
    exp_accept = (await db.execute(
        select(func.count()).select_from(ReviewFeedback).where(
            ReviewFeedback.target_type == "experience_hit", ReviewFeedback.action == "accept")
    )).scalar_one()

    # 5. 覆盖项验证率：covered / 总（跨用例内嵌 covered_items 聚合）
    cases_stmt = select(TestCase.covered_items).where(TestCase.deleted_at.is_(None))
    if project_id:
        cases_stmt = cases_stmt.where(TestCase.project_id == project_id)
    all_items = (await db.execute(cases_stmt)).scalars().all()
    total_ci = covered_ci = 0
    for lst in all_items:
        for ci in (lst or []):
            total_ci += 1
            if ci.get("coverage_status") == "covered":
                covered_ci += 1

    # 4. 用例复用率：ChangeImpactRecord.reuse 汇总 (reusable+need_adjust)/(全部)
    reuse_reusable = reuse_total = 0
    recs = (await db.execute(select(ChangeImpactRecord.impact_json).where(ChangeImpactRecord.impact_json.isnot(None)))).scalars().all()
    for j in recs:
        reuse = (j or {}).get("reuse") or {}
        r = len(reuse.get("reusable") or []) + len(reuse.get("need_adjust") or [])
        n = r + len(reuse.get("need_new") or [])
        reuse_reusable += r
        reuse_total += n

    return {
        "impact_accuracy": _rate(fb_accept_scope, fb_total_scope),
        "ai_case_modify_rate": _rate(fb_edit, fb_all_item),
        "experience_adopt_rate": _rate(exp_accept, exp_total),
        "case_reuse_rate": _rate(reuse_reusable, reuse_total),
        "coverage_verify_rate": _rate(covered_ci, total_ci),
        "raw": {
            "impact_scope_feedback": fb_total_scope, "covered_item_feedback": fb_all_item,
            "experience_hits": exp_total, "reuse_total": reuse_total,
            "covered_items_total": total_ci,
        },
    }
