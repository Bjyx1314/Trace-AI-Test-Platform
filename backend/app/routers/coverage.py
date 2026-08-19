"""覆盖矩阵与质量报告 router（方案 13）。

覆盖矩阵是平台核心页面之一：把某需求下所有用例的 covered_items 按质量点聚合，
展示 覆盖项|来源|优先级|命中规则|关联用例|执行状态|证据|风险，分 covered/failed/not_covered 三区。

MVP：covered_items 内嵌 TestCase.covered_items(JSONB)，此处做 JSONB 聚合，不建 CoveredItem 表。
与旧 requirements coverage(标题比对) 并存：矩阵为主口径，需求覆盖率= sources 含 requirement 的项计算。
"""
from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import TestCase, TestResult, Execution, ChangeImpactRecord

router = APIRouter(prefix="/api", tags=["coverage"])

# 覆盖状态合并优先级：covered 优先（乐观），无 covered 才看 failed
_STATUS_RANK = {"covered": 3, "failed": 2, "not_covered": 1, None: 0}


def _merge_status(cur: str | None, new: str | None) -> str:
    return cur if _STATUS_RANK.get(cur, 0) >= _STATUS_RANK.get(new, 0) else (new or cur or "not_covered")


async def _latest_result_evidence(db: AsyncSession, case_id: str) -> list:
    """取用例最近一次执行的 checked_points 作为证据。"""
    row = (await db.execute(
        select(TestResult)
        .where(TestResult.test_case_id == case_id)
        .order_by(TestResult.created_at.desc())
        .limit(1)
    )).scalar_one_or_none()
    return (row.checked_points or []) if row else []


@router.get("/requirements/{req_id}/coverage-matrix")
async def requirement_coverage_matrix(req_id: str, db: AsyncSession = Depends(get_db)):
    """聚合需求下所有用例的 covered_items 为覆盖矩阵。"""
    cases = (await db.execute(
        select(TestCase).where(
            TestCase.requirement_id == req_id, TestCase.deleted_at.is_(None),
        )
    )).scalars().all()

    # 按覆盖项 name 归并（同一质量点跨用例合并）
    rows: dict[str, dict] = {}
    for tc in cases:
        evidence_by_item: dict[str, list] = {}
        for cp in await _latest_result_evidence(db, tc.id):
            evidence_by_item.setdefault(cp.get("item_id") or cp.get("covered_item"), []).append(cp)
        for ci in tc.covered_items or []:
            key = (ci.get("name") or ci.get("item_id") or "").strip()
            if not key:
                continue
            row = rows.get(key)
            if row is None:
                row = {
                    "item_id": ci.get("item_id"),
                    "name": key,
                    "object": ci.get("object"),
                    "action": ci.get("action"),
                    "sources": [],
                    "priority": ci.get("priority"),
                    "matched_rules": [],
                    "risk_tags": [],
                    "related_cases": [],
                    "coverage_status": "not_covered",
                    "evidence": [],
                }
                rows[key] = row
            for s in ci.get("sources") or []:
                if s not in row["sources"]:
                    row["sources"].append(s)
            for r in ci.get("matched_rules") or []:
                if r not in row["matched_rules"]:
                    row["matched_rules"].append(r)
            for t in ci.get("risk_tags") or []:
                if t not in row["risk_tags"]:
                    row["risk_tags"].append(t)
            # 优先级取最高（P0<P1<P2 数字小=高）
            if ci.get("priority") and (row["priority"] is None or ci["priority"] < row["priority"]):
                row["priority"] = ci["priority"]
            row["related_cases"].append({"id": tc.id, "case_id": tc.case_id, "title": tc.title})
            row["coverage_status"] = _merge_status(row["coverage_status"], ci.get("coverage_status"))
            ev = evidence_by_item.get(ci.get("item_id")) or []
            row["evidence"].extend(ev)

    matrix = list(rows.values())
    for row in matrix:
        # 风险粗判：failed→high，未覆盖且 P0/P1→high，未覆盖→mid，covered→low
        st, pri = row["coverage_status"], row.get("priority")
        if st == "failed":
            row["risk_level"] = "high"
        elif st == "not_covered":
            row["risk_level"] = "high" if pri in ("P0", "P1") else "mid"
        else:
            row["risk_level"] = "low"

    covered = sum(1 for r in matrix if r["coverage_status"] == "covered")
    failed = sum(1 for r in matrix if r["coverage_status"] == "failed")
    not_covered = sum(1 for r in matrix if r["coverage_status"] == "not_covered")
    total = len(matrix)

    # 待确认区：来自最近一次代码影响分析的 entry_coverage_matrix
    impact = (await db.execute(
        select(ChangeImpactRecord)
        .where(ChangeImpactRecord.requirement_id == req_id,
               ChangeImpactRecord.status.in_(("done", "degraded")))
        .order_by(ChangeImpactRecord.created_at.desc()).limit(1)
    )).scalar_one_or_none()
    entry_matrix = (impact.impact_json or {}).get("entry_coverage_matrix", []) if impact and impact.impact_json else []

    return {
        "requirement_id": req_id,
        "rows": matrix,
        "entry_coverage_matrix": entry_matrix,
        "summary": {
            "total": total,
            "covered": covered,
            "failed": failed,
            "not_covered": not_covered,
            "verify_rate": round(covered / total, 4) if total else 0.0,
            # 需求覆盖率（旧口径还原）：sources 含 requirement 的覆盖项 covered 占比
            "requirement_coverage": _requirement_coverage(matrix),
        },
    }


def _requirement_coverage(matrix: list[dict]) -> float:
    req_items = [r for r in matrix if "requirement" in (r.get("sources") or [])]
    if not req_items:
        return 0.0
    covered = sum(1 for r in req_items if r["coverage_status"] == "covered")
    return round(covered / len(req_items), 4)


@router.get("/requirements/{req_id}/release-report")
async def requirement_release_report(req_id: str, db: AsyncSession = Depends(get_db)):
    """需求级发布报告（方案 13.2/13.3）：覆盖矩阵 + 命中规则 + 剩余风险 → release_suggestion。"""
    from app.services.quality_gate_engine import build_release_report
    return await build_release_report(db, req_id)


@router.get("/executions/{exec_id}/coverage")
async def execution_coverage(exec_id: str, db: AsyncSession = Depends(get_db)):
    """按执行批次汇总 checked_points 证据。"""
    execution = await db.get(Execution, exec_id)
    if not execution:
        raise HTTPException(404, "执行批次不存在")
    results = (await db.execute(
        select(TestResult).where(TestResult.execution_id == exec_id)
    )).scalars().all()
    items: list[dict] = []
    for r in results:
        for cp in r.checked_points or []:
            items.append({
                "test_case_id": r.test_case_id,
                "item_id": cp.get("item_id"),
                "covered_item_name": cp.get("covered_item_name") or cp.get("covered_item"),
                "status": cp.get("status"),
                "evidence": cp.get("evidence"),
                "screenshot_url": cp.get("screenshot_url"),
            })
    passed = sum(1 for i in items if i["status"] == "passed")
    return {
        "execution_id": exec_id,
        "checked_points": items,
        "summary": {
            "total": len(items),
            "passed": passed,
            "verify_rate": round(passed / len(items), 4) if items else 0.0,
        },
    }
