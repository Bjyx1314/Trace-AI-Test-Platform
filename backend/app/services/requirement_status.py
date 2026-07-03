"""需求状态生命周期服务：评估并自动推进需求到"已完成"状态。"""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models import Requirement, TestCase, Defect


async def evaluate_requirement_completion(db: AsyncSession, requirement_id: str) -> bool:
    """Returns True if all test cases passed AND no open defects."""
    cases = (await db.execute(
        select(TestCase).where(TestCase.requirement_id == requirement_id)
    )).scalars().all()

    if not cases:
        return False

    if not all(tc.last_status == "passed" for tc in cases):
        return False

    open_defect_count = (await db.execute(
        select(func.count()).select_from(Defect)
        .join(TestCase, Defect.test_case_id == TestCase.id)
        .where(
            TestCase.requirement_id == requirement_id,
            Defect.status.in_(["draft", "ticket_created", "confirmed"]),
        )
    )).scalar() or 0

    return open_defect_count == 0


async def apply_requirement_completion(db: AsyncSession, requirement_id: str) -> None:
    """If all conditions met, advance requirement status to 'done'. Does not commit."""
    req = await db.get(Requirement, requirement_id)
    if not req or req.status not in ("testing", "pending_test"):
        return
    if await evaluate_requirement_completion(db, requirement_id):
        req.status = "done"
