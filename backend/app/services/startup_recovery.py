"""启动时回收“孤儿任务”。

后端重启/部署时，正在进行的需求分析、用例生成、用例执行的后台任务会随进程一起死掉，
但数据库里的状态还停在“进行中”（analyzing / generating_cases / running），导致前端按钮
永久置灰、用户无法重新触发，即“卡死”。

这里在 lifespan 启动时把这些非终态的在途任务一律收口为失败终态，并写明原因，
让前端能立刻看到“服务重启中断，请重试”，且按钮恢复可点。幂等、容错（任一步失败不影响启动）。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Requirement, Execution

logger = logging.getLogger(__name__)

_REASON = "服务重启导致任务中断，请重新触发"


async def reset_orphaned_jobs(db: AsyncSession) -> dict:
    """把重启前残留的在途任务收口为终态。返回各类回收数量。"""
    from app.models import RequirementSlice
    counts = {"requirements": 0, "slices": 0, "executions": 0}

    # 需求：回退到原状态(不新增失败状态)，错误写 analysis_result 仅作提示
    reqs = (await db.execute(
        select(Requirement).where(Requirement.status.in_(["analyzing", "generating_cases"]))
    )).scalars().all()
    for req in reqs:
        base = dict(req.analysis_result or {})
        if req.status == "analyzing":
            req.status = "pending_analysis"
            base["error_message"] = _REASON
        else:
            req.status = "pending_case_generation"
            base["generation_error"] = _REASON
        req.analysis_result = base
        counts["requirements"] += 1

    # 切片：同样回退原状态
    sls = (await db.execute(
        select(RequirementSlice).where(RequirementSlice.status.in_(["analyzing", "generating_cases"]))
    )).scalars().all()
    for sl in sls:
        base = dict(sl.analysis_result or {})
        if sl.status == "analyzing":
            sl.status = "pending_analysis"
            base["error_message"] = _REASON
        else:
            sl.status = "pending_case_generation"
            base["generation_error"] = _REASON
        sl.analysis_result = base
        counts["slices"] += 1

    # 执行：pending/running → failed（写 error_message，供前端展示并允许重跑）
    exes = (await db.execute(
        select(Execution).where(Execution.status.in_(["pending", "running"]))
    )).scalars().all()
    now = datetime.now()
    for ex in exes:
        ex.status = "failed"
        ex.error_message = _REASON
        if ex.finished_at is None:
            ex.finished_at = now
        counts["executions"] += 1

    # App 派发任务：pending/claimed/running → cancelled(避免重启后 worker 领到孤儿任务)
    from app.models import AppExecJob
    counts["app_jobs"] = 0
    jobs = (await db.execute(
        select(AppExecJob).where(AppExecJob.status.in_(["pending", "claimed", "running"]))
    )).scalars().all()
    for j in jobs:
        j.status = "cancelled"
        if j.finished_at is None:
            j.finished_at = now
        counts["app_jobs"] += 1

    if counts["requirements"] or counts["slices"] or counts["executions"] or counts["app_jobs"]:
        await db.commit()
        logger.warning("启动回收孤儿任务：需求 %d、切片 %d、执行 %d、App任务 %d 回退",
                       counts["requirements"], counts["slices"], counts["executions"], counts["app_jobs"])
    return counts
