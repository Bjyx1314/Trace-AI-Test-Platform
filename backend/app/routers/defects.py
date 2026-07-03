"""缺陷列表/详情/状态更新 —— Agent5(DefectDiagnostician)输出的复核与流转。"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.database import get_db
from app.models import Defect, Execution, TestCase, Requirement
from app.schemas import DefectOut, DefectUpdate
from app.services.feishu_app import create_defect_ticket, FeishuError
from app.services import external_tasks
from app.services.external_tasks import ExternalTaskError


async def _attach_requirement(db: AsyncSession, defects: list[Defect]) -> list[Defect]:
    """给缺陷批量挂上关联需求(requirement_id/title)，供列表/详情展示。"""
    tc_ids = {d.test_case_id for d in defects if d.test_case_id}
    cases = {}
    if tc_ids:
        cases = {c.id: c for c in (await db.execute(
            select(TestCase).where(TestCase.id.in_(tc_ids))
        )).scalars().all()}
    req_ids = {c.requirement_id for c in cases.values() if c.requirement_id}
    reqs = {}
    if req_ids:
        reqs = {r.id: r for r in (await db.execute(
            select(Requirement).where(Requirement.id.in_(req_ids))
        )).scalars().all()}
    for d in defects:
        c = cases.get(d.test_case_id)
        rid = c.requirement_id if c else None
        d.requirement_id = rid
        d.requirement_title = reqs[rid].title if rid in reqs else None
    return defects

router = APIRouter(prefix="/api/defects", tags=["defects"])


def _compose_repro(defect: Defect) -> str | None:
    steps = (defect.draft_ticket or {}).get("reproduction_steps") or []
    if not steps:
        return None
    return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))


def _compose_bug_desc(defect: Defect) -> str:
    d = defect.draft_ticket or {}
    parts = []
    if d.get("summary"):
        parts.append(d["summary"])
    repro = _compose_repro(defect)
    if repro:
        parts.append("复现步骤：\n" + repro)
    if d.get("affected_scope"):
        parts.append("影响范围：" + d["affected_scope"])
    return "\n\n".join(parts)


async def _external_project_ids(db: AsyncSession, defect: Defect) -> list[str] | None:
    """缺陷关联需求来自外部系统时，可返回其外部项目标识。"""
    tc = await db.get(TestCase, defect.test_case_id)
    if tc and tc.requirement_id:
        req = await db.get(Requirement, tc.requirement_id)
        if req and req.source == "external" and req.source_record_id:
            # 当前仅记录外部需求标识，未持久化外部项目标识。
            pass
    return None


async def _related_external_requirement(db: AsyncSession, defect: Defect) -> str | None:
    """返回缺陷关联的外部需求标识。"""
    tc = await db.get(TestCase, defect.test_case_id)
    if tc and tc.requirement_id:
        req = await db.get(Requirement, tc.requirement_id)
        if req and req.source == "external":
            return req.source_record_id
    return None


@router.get("", response_model=list[DefectOut])
async def list_defects(
    project_id: str | None = None,
    requirement_id: str | None = None,
    status: str | None = None,
    severity: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    q = select(Defect).order_by(Defect.created_at.desc())
    if project_id:
        q = q.join(Execution, Defect.execution_id == Execution.id).where(
            Execution.project_id == project_id
        )
    if requirement_id:
        q = q.join(TestCase, Defect.test_case_id == TestCase.id).where(
            TestCase.requirement_id == requirement_id
        )
    if status == "resolved":          # 无需处理 = 忽略 + 标记重复
        q = q.where(Defect.status.in_(("ignored", "duplicate")))
    elif status:
        q = q.where(Defect.status == status)
    if severity:
        q = q.where(Defect.severity == severity)

    result = await db.execute(q)
    defects = list(result.scalars().all())
    return await _attach_requirement(db, defects)


@router.get("/{defect_id}", response_model=DefectOut)
async def get_defect(defect_id: str, db: AsyncSession = Depends(get_db)):
    defect = await db.get(Defect, defect_id)
    if not defect:
        raise HTTPException(404, "Defect not found")
    (await _attach_requirement(db, [defect]))
    return defect


@router.patch("/{defect_id}", response_model=DefectOut)
async def update_defect(
    defect_id: str,
    body: DefectUpdate,
    db: AsyncSession = Depends(get_db),
):
    defect = await db.get(Defect, defect_id)
    if not defect:
        raise HTTPException(404, "Defect not found")

    for k, v in body.model_dump(exclude_unset=True, exclude_none=True).items():
        setattr(defect, k, v)

    # 缺陷确认：优先写入已配置的外部任务系统，否则回退飞书。
    already_ticketed = defect.external_ticket_id or defect.feishu_ticket_id
    if defect.status == "confirmed" and not already_ticketed:
        if external_tasks.is_configured():
            try:
                bug = await external_tasks.create_bug(
                    title=defect.title,
                    description=_compose_bug_desc(defect),
                    severity=defect.severity,
                    project_ids=await _external_project_ids(db, defect),
                    related_requirement_id=await _related_external_requirement(db, defect),
                    reproduce_steps=_compose_repro(defect),
                    found_stage="acceptance",
                )
                bid = str(bug.get("id") or "")
                if bid:
                    defect.external_ticket_id = bid
                    defect.external_ticket_url = external_tasks.bug_url(bid)
            except ExternalTaskError as e:
                raise HTTPException(400, f"缺陷确认建单失败：{e}")
            except Exception as e:
                raise HTTPException(502, f"缺陷确认建单失败：{e}")
        else:
            try:
                defect.feishu_ticket_id = await create_defect_ticket(defect)
            except FeishuError as e:
                raise HTTPException(400, f"缺陷确认建单失败：{e}")
            except Exception as e:
                raise HTTPException(502, f"缺陷确认建单失败：{e}")

    # 忽略或标记重复时，尽力回写外部单据状态，不阻断本地流转。
    if defect.status in ("ignored", "duplicate") and defect.external_ticket_id and external_tasks.is_configured():
        try:
            note = "测试平台标记为重复" if defect.status == "duplicate" else None
            await external_tasks.update_bug_status(defect.external_ticket_id, "archived", note)
        except Exception:
            pass

    await db.commit()

    # 缺陷解决后检查关联需求是否可以标记为已完成
    if defect.status in ("ignored", "duplicate"):
        tc = await db.get(TestCase, defect.test_case_id)
        if tc and tc.requirement_id:
            from app.services.requirement_status import apply_requirement_completion
            await apply_requirement_completion(db, tc.requirement_id)
            await db.commit()

    await db.refresh(defect)
    (await _attach_requirement(db, [defect]))
    return defect
