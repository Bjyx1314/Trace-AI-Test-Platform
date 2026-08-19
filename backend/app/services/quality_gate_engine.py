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

    # 阶段六：AI 发布建议作为门禁输入项（方案 13.3），按项目 release_policy 决定卡位
    try:
        from app.models import QualityGateConfig, TestCase
        cfg = (await db.execute(
            select(QualityGateConfig).where(QualityGateConfig.project_id == execution.project_id)
        )).scalar_one_or_none()
        policy = (cfg.release_policy if cfg else "advisory")
        if policy in ("warn", "block"):
            # 按执行结果关联用例取涉及需求，汇总覆盖矩阵发布建议
            from app.models import TestResult
            rq = (await db.execute(
                select(TestCase.requirement_id).join(TestResult, TestResult.test_case_id == TestCase.id)
                .where(TestResult.execution_id == execution.id, TestCase.requirement_id.isnot(None)).distinct()
            )).scalars().all()
            for rid in rq:
                rep = await build_release_report(db, rid)
                if rep["release_suggestion"] == "block":
                    sev = "block" if policy == "block" else "warn"
                    for r in rep["reasons"]:
                        blocking_reasons.append({"rule": "ai_release_block", "message": f"[需求覆盖] {r}", "severity": sev})
    except Exception:  # noqa: BLE001 门禁增强失败不影响基础门禁
        pass

    releasable = not any(r["severity"] == "block" for r in blocking_reasons)
    return {"releasable": releasable, "blocking_reasons": blocking_reasons}


async def build_release_report(db: AsyncSession, requirement_id: str) -> dict:
    """需求级发布报告（方案 13.2）：由覆盖矩阵 + 命中规则 + 剩余风险生成 release_suggestion。

    - block：存在 P0 覆盖项未覆盖或失败；
    - warn：存在 P1 未覆盖/失败；
    - pass：无高风险缺口。
    reasons 引用具体覆盖缺口/失败项（证据先行）。
    """
    from app.routers.coverage import requirement_coverage_matrix
    try:
        matrix = await requirement_coverage_matrix(requirement_id, db)
    except Exception:  # noqa: BLE001
        return {"release_suggestion": "pass", "reasons": [], "summary": {}}

    rows = matrix.get("rows", [])
    p0_gap = [r for r in rows if r["coverage_status"] in ("not_covered", "failed") and r.get("priority") == "P0"]
    p1_gap = [r for r in rows if r["coverage_status"] in ("not_covered", "failed") and r.get("priority") == "P1"]
    failed = [r for r in rows if r["coverage_status"] == "failed"]

    reasons: list[str] = []
    for r in p0_gap:
        reasons.append(f"P0 覆盖项「{r['name']}」{ '失败' if r['coverage_status']=='failed' else '未覆盖' }"
                       + (f"（命中规则 {'、'.join(r['matched_rules'])}）" if r.get("matched_rules") else ""))
    for r in p1_gap[:5]:
        reasons.append(f"P1 覆盖项「{r['name']}」{ '失败' if r['coverage_status']=='failed' else '未覆盖' }")

    if p0_gap or failed:
        suggestion = "block"
    elif p1_gap:
        suggestion = "warn"
    else:
        suggestion = "pass"

    return {
        "requirement_id": requirement_id,
        "release_suggestion": suggestion,
        "reasons": reasons,
        "summary": matrix.get("summary", {}),
        "entry_coverage_matrix": matrix.get("entry_coverage_matrix", []),
    }
