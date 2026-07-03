"""CI/CD integration endpoints — trigger executions and check quality gates."""
from fastapi import APIRouter, Depends, HTTPException, Header
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import BaseModel
from app.database import get_db
from app.models import Project, Execution, TestCase
from app.services.mock_runner import MockExecutionRunner
import asyncio

router = APIRouter(prefix="/api/cicd", tags=["cicd"])


class TriggerRequest(BaseModel):
    project_id: str
    pipeline_name: str = "CI Pipeline"
    branch: str = "main"
    commit_sha: str | None = None


@router.post("/trigger")
async def trigger_ci_run(body: TriggerRequest, db: AsyncSession = Depends(get_db)):
    """Called by CI system to kick off a test execution."""
    proj = await db.get(Project, body.project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    result = await db.execute(
        select(TestCase.id).where(TestCase.project_id == body.project_id)
    )
    case_ids = list(result.scalars().all())

    name = f"[CI] {body.pipeline_name} @ {body.branch}"
    if body.commit_sha:
        name += f" ({body.commit_sha[:8]})"

    execution = Execution(
        project_id=body.project_id,
        name=name,
        trigger="ci",
        status="pending",
        total=len(case_ids),
    )
    db.add(execution)
    await db.commit()
    await db.refresh(execution)

    asyncio.create_task(MockExecutionRunner().run(execution.id, case_ids))

    return {
        "execution_id": execution.id,
        "status": "triggered",
        "total_cases": len(case_ids),
        "gate_check_url": f"/api/cicd/gate/{execution.id}",
    }


@router.get("/gate/{execution_id}")
async def get_gate_result(execution_id: str, db: AsyncSession = Depends(get_db)):
    """Poll this endpoint after execution to get the CI gate result ({releasable, blocking_reasons})."""
    ex = await db.get(Execution, execution_id)
    if not ex or ex.status != "done":
        raise HTTPException(404, "Execution not found or not finished")
    return ex.ci_gate_result
