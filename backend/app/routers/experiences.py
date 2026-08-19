"""经验库 router（方案 6.2）：召回、采纳动作、管理、维护。"""
from __future__ import annotations
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Experience, Requirement, ChangeImpactRecord, ReviewFeedback, TestCase
from app.services import experience_recall, experience_lifecycle

router = APIRouter(prefix="/api/experiences", tags=["experiences"])


def _operator(cu: dict | None) -> str:
    return (cu or {}).get("name") or (cu or {}).get("sub") or "系统"


@router.get("/recall")
async def recall_experiences(
    requirement_id: str | None = None,
    impact_id: str | None = None,
    top_n: int = Query(5, ge=1, le=20),
    db: AsyncSession = Depends(get_db),
):
    """按需求语义 + 影响面标签召回经验，每条带命中原因。"""
    project_id = None
    query_text = ""
    risk_tags: list[str] = []
    service_ids: list[str] = []
    api_ids: list[str] = []

    if requirement_id:
        req = await db.get(Requirement, requirement_id)
        if req:
            project_id = req.project_id
            query_text = f"{req.title} {req.content or ''}"[:2000]
            for ip in (req.analysis_result or {}).get("issue_points", []) or []:
                risk_tags += ip.get("platforms") or []
    if impact_id:
        rec = await db.get(ChangeImpactRecord, impact_id)
        if rec and rec.impact_json:
            scope = rec.impact_json.get("impact_scope") or {}
            service_ids += scope.get("affected_services") or []
            api_ids += scope.get("affected_apis") or []
            for s in rec.impact_json.get("suggested_validation_items") or []:
                risk_tags += s.get("risk_tags") or []
            if not query_text:
                query_text = "、".join((scope.get("affected_flows") or []) + (scope.get("affected_pages") or []))

    hits = await experience_recall.recall(
        db, project_id=project_id, query_text=query_text,
        risk_tags=list(set(risk_tags)), service_node_ids=list(set(service_ids)),
        api_node_ids=list(set(api_ids)), top_n=top_n,
    )
    # 命中即记 hit 信号（stats.hit_count）
    for h in hits:
        await experience_lifecycle.record_signal(db, h["experience_id"], "hit")
    return {"requirement_id": requirement_id, "impact_id": impact_id, "hits": hits}


class AdoptAction(BaseModel):
    requirement_id: str | None = None
    reason: str | None = None  # 「本次不适用」必填


@router.post("/{exp_id}/adopt")
async def adopt(exp_id: str, body: AdoptAction, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    exp = await experience_lifecycle.record_signal(db, exp_id, "adopt")
    if not exp:
        raise HTTPException(404, "经验不存在")
    db.add(ReviewFeedback(
        id=str(uuid.uuid4()), test_case_id="", requirement_id=body.requirement_id,
        target_type="experience_hit", action="accept", after={"experience_id": exp_id},
        reason=body.reason, operator=_operator(current_user), experience_id=exp_id,
    ))
    await db.commit()
    return {"status": "ok", "confidence": exp.confidence, "exp_status": exp.status}


@router.post("/{exp_id}/ignore")
async def ignore(exp_id: str, body: AdoptAction, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    exp = await experience_lifecycle.record_signal(db, exp_id, "reject")
    if not exp:
        raise HTTPException(404, "经验不存在")
    db.add(ReviewFeedback(
        id=str(uuid.uuid4()), test_case_id="", requirement_id=body.requirement_id,
        target_type="experience_hit", action="reject", after={"experience_id": exp_id},
        reason=body.reason, operator=_operator(current_user), experience_id=exp_id,
    ))
    await db.commit()
    return {"status": "ok", "confidence": exp.confidence, "exp_status": exp.status}


@router.post("/{exp_id}/not-applicable")
async def not_applicable(exp_id: str, body: AdoptAction, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    if not body.reason:
        raise HTTPException(400, "「本次不适用」必须填写原因（负反馈）")
    exp = await experience_lifecycle.record_signal(db, exp_id, "not_applicable")
    if not exp:
        raise HTTPException(404, "经验不存在")
    db.add(ReviewFeedback(
        id=str(uuid.uuid4()), test_case_id="", requirement_id=body.requirement_id,
        target_type="experience_hit", action="reject", after={"experience_id": exp_id, "not_applicable": True},
        reason=body.reason, operator=_operator(current_user), experience_id=exp_id,
    ))
    await db.commit()
    return {"status": "ok", "confidence": exp.confidence, "exp_status": exp.status}


def _out(e: Experience) -> dict:
    return {
        "experience_id": e.id, "title": e.title, "project_id": e.project_id,
        "trigger_context": e.trigger_context, "suggested_covered_items": e.suggested_covered_items,
        "source": e.source, "reason": e.reason, "evidence": e.evidence,
        "stats": e.stats, "confidence": e.confidence, "status": e.status,
        "merged_from": e.merged_from, "created_at": e.created_at,
    }


@router.get("")
async def list_experiences(project_id: str | None = None, status: str | None = None,
                           limit: int = Query(100, ge=1, le=500), db: AsyncSession = Depends(get_db)):
    stmt = select(Experience).order_by(Experience.confidence.desc()).limit(limit)
    if project_id:
        stmt = stmt.where(Experience.project_id == project_id)
    if status:
        stmt = stmt.where(Experience.status == status)
    return [_out(e) for e in (await db.execute(stmt)).scalars().all()]


class ExperienceUpdate(BaseModel):
    status: str | None = None  # active/dormant/stale
    title: str | None = None
    reason: str | None = None


@router.patch("/{exp_id}")
async def update_experience(exp_id: str, body: ExperienceUpdate, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    exp = await db.get(Experience, exp_id)
    if not exp:
        raise HTTPException(404, "经验不存在")
    for f in ("status", "title", "reason"):
        v = getattr(body, f)
        if v is not None:
            setattr(exp, f, v)
    await db.commit()
    return _out(exp)


@router.post("/maintenance/merge")
async def run_merge(project_id: str | None = None, db: AsyncSession = Depends(get_db), current_user: dict = Depends(get_current_user)):
    n = await experience_lifecycle.run_merge_maintenance(db, project_id)
    return {"merged": n}
