"""质量看板规则引擎：评估CI/CD发布门禁。

固定规则（不可配置）:
  1. 测试进度100% —— pass_rate 必须等于100，否则阻断
  2. 致命缺陷数为0 —— 最高缺陷等级的未关闭缺陷数必须为0，否则阻断
"""
from __future__ import annotations
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models import Execution, Defect
from app.services.severity import get_blocking_severity

OPEN_DEFECT_STATUSES = ("draft", "confirmed", "ticket_created")


async def evaluate_gate(
    db: AsyncSession,
    execution: Execution,
) -> dict:
    """评估CI/CD质量门禁，返回 {releasable, blocking_reasons:[{rule,message,severity}]}。"""
    blocking_reasons: list[dict] = []

    # 规则1: 测试进度100%
    if execution.pass_rate < 100:
        blocking_reasons.append({
            "rule": "pass_rate_100",
            "message": f"测试进度 {execution.pass_rate:.1f}%，未达到100%",
            "severity": "block",
        })

    # 规则2: 致命缺陷数为0（最高缺陷等级的未关闭缺陷）
    blocking_level = await get_blocking_severity(db)
    open_critical_count = (
        await db.execute(
            select(func.count())
            .select_from(Defect)
            .where(
                Defect.execution_id == execution.id,
                Defect.severity == blocking_level,
                Defect.status.in_(OPEN_DEFECT_STATUSES),
            )
        )
    ).scalar() or 0

    if open_critical_count > 0:
        blocking_reasons.append({
            "rule": "critical_defects_zero",
            "message": f"存在 {open_critical_count} 个「{blocking_level}」未关闭缺陷",
            "severity": "block",
        })

    releasable = not blocking_reasons
    return {"releasable": releasable, "blocking_reasons": blocking_reasons}
