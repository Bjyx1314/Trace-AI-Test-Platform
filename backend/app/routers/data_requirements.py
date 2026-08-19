"""用例数据要求 CRUD（测试数据准备与状态编排 MVP-0）。

MVP-0：测试人员为用例声明数据要求，并在 manual_values 里直填实际值；执行前置据此把
`${别名.字段}` 注入步骤/凭证。字段级校验、场景绑定、AUTO 造数为后续阶段（本接口已容纳其字段）。
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import get_current_user
from app.models import TestDataRequirement
from app.services.data_prep import injection

router = APIRouter(prefix="/api/data-requirements", tags=["data-requirements"])

# 允许写入的字段（其余忽略，防脏写）
_WRITABLE = {
    "case_id", "alias", "data_type", "schema_version", "target_state", "constraints",
    "strategy", "reuse_policy", "isolation", "post_state", "cleanup_policy",
    "scenario_id", "scenario_version", "output_key", "depends_on", "manual_values",
    "required", "source", "confidence", "review_status", "approved_snapshot",
}


def _dump(r: TestDataRequirement) -> dict:
    return {c: getattr(r, c) for c in (
        "id", "case_id", "alias", "data_type", "schema_version", "target_state", "constraints",
        "strategy", "reuse_policy", "isolation", "post_state", "cleanup_policy",
        "scenario_id", "scenario_version", "output_key", "depends_on", "manual_values",
        "required", "source", "confidence", "review_status", "approved_snapshot",
    )}


@router.get("")
async def list_requirements(case_id: str, db: AsyncSession = Depends(get_db),
                            user: dict = Depends(get_current_user)):
    """某用例的全部数据要求 + 该用例步骤里引用到的占位符（供 Review 页比对是否有别名未配）。"""
    rows = (await db.execute(
        select(TestDataRequirement).where(TestDataRequirement.case_id == case_id)
        .order_by(TestDataRequirement.created_at.asc())
    )).scalars().all()
    # 用例步骤里引用的占位符
    from app.models import TestCase
    tc = (await db.execute(select(TestCase).where(TestCase.case_id == case_id))).scalars().first()
    placeholders = injection.scan_placeholders(getattr(tc, "steps", None) or []) if tc else []
    return {"requirements": [_dump(r) for r in rows], "referenced_placeholders": placeholders}


@router.post("")
async def upsert_requirement(body: dict, db: AsyncSession = Depends(get_db),
                             user: dict = Depends(get_current_user)):
    """新建/更新（按 case_id+alias 幂等）。body 至少含 case_id、alias。"""
    data = {k: v for k, v in (body or {}).items() if k in _WRITABLE}
    case_id = str(data.get("case_id") or "").strip()
    alias = str(data.get("alias") or "").strip()
    if not case_id or not alias:
        raise HTTPException(400, "case_id 与 alias 必填")
    row = (await db.execute(select(TestDataRequirement).where(
        TestDataRequirement.case_id == case_id, TestDataRequirement.alias == alias,
    ))).scalars().first()
    if row:
        for k, v in data.items():
            setattr(row, k, v)
    else:
        row = TestDataRequirement(**data)
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return _dump(row)


@router.delete("/{req_id}")
async def delete_requirement(req_id: str, db: AsyncSession = Depends(get_db),
                             user: dict = Depends(get_current_user)):
    row = await db.get(TestDataRequirement, req_id)
    if not row:
        raise HTTPException(404, "数据要求不存在")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
