"""硬规则引擎管理（方案 6.1）：QualityRule CRUD + 启停。admin 可编辑。

MVP 只做 seed 导入只读；完整版提供编辑界面。命中留痕在 quality_rule_engine + 覆盖矩阵展示。
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.models import QualityRule

router = APIRouter(prefix="/api/quality-rules", tags=["quality-rules"])


class RuleUpsert(BaseModel):
    id: str | None = None  # 新建可空(自动 R-xxx)；更新必填
    name: str
    match_tags: list[str] = []
    min_priority: str | None = None
    required_covered_items: list | None = None
    source: str | None = None
    active: bool = True


def _out(r: QualityRule) -> dict:
    return {
        "id": r.id, "name": r.name, "match_tags": r.match_tags, "min_priority": r.min_priority,
        "required_covered_items": r.required_covered_items, "source": r.source, "active": r.active,
        "created_at": r.created_at,
    }


@router.get("")
async def list_rules(active: bool | None = None, db: AsyncSession = Depends(get_db)):
    stmt = select(QualityRule).order_by(QualityRule.id)
    if active is not None:
        stmt = stmt.where(QualityRule.active.is_(active))
    return [_out(r) for r in (await db.execute(stmt)).scalars().all()]


async def _next_rule_id(db: AsyncSession) -> str:
    rows = (await db.execute(select(QualityRule.id))).scalars().all()
    nums = [int(x.split("-")[1]) for x in rows if x.startswith("R-") and x.split("-")[1].isdigit()]
    return f"R-{(max(nums) + 1 if nums else 1):03d}"


@router.post("", status_code=201)
async def create_rule(body: RuleUpsert, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_admin)):
    rid = body.id or await _next_rule_id(db)
    if await db.get(QualityRule, rid):
        raise HTTPException(400, f"规则 {rid} 已存在")
    r = QualityRule(id=rid, name=body.name, match_tags=body.match_tags, min_priority=body.min_priority,
                    required_covered_items=body.required_covered_items, source=body.source or "manual", active=body.active)
    db.add(r)
    await db.commit()
    return _out(r)


@router.put("/{rule_id}")
async def update_rule(rule_id: str, body: RuleUpsert, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_admin)):
    r = await db.get(QualityRule, rule_id)
    if not r:
        raise HTTPException(404, "规则不存在")
    r.name = body.name
    r.match_tags = body.match_tags
    r.min_priority = body.min_priority
    r.required_covered_items = body.required_covered_items
    r.active = body.active
    if body.source:
        r.source = body.source
    await db.commit()
    return _out(r)


@router.delete("/{rule_id}", status_code=200)
async def delete_rule(rule_id: str, db: AsyncSession = Depends(get_db), current_user: dict = Depends(require_admin)):
    r = await db.get(QualityRule, rule_id)
    if not r:
        raise HTTPException(404, "规则不存在")
    await db.delete(r)
    await db.commit()
    return {"status": "ok"}
