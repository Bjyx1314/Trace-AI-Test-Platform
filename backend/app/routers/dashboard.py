"""Quality dashboard aggregation endpoint."""
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.database import get_db
from app.models import Execution, TestResult, TestCase, Requirement, Defect, Project
from app.services.quality_gate_engine import evaluate_gate
from app.services.dashboard_metrics import collect_requirement_rows, build_summary_cards
from app.services.severity import get_blocking_severity
from app.dependencies import get_current_user

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("/summary")
async def dashboard_summary(
    project_id: str | None = None,
    iteration: str | None = None,
    status: str | None = None,
    platform: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """质量看板汇总指标卡（架构文档 5.2.3）。

    随筛选条件（项目/迭代/需求状态/端）实时联动，返回六大指标卡（含颜色判定），
    并保留执行趋势与基础计数，兼容旧版前端调用。
    """
    rows = await collect_requirement_rows(
        db, project_id=project_id, iteration=iteration, status=status, platform=platform
    )
    cards = build_summary_cards(rows)

    def _where(q, model):
        if project_id:
            return q.where(model.project_id == project_id)
        return q

    total_cases = (await db.execute(_where(select(func.count()).select_from(TestCase), TestCase))).scalar()
    total_reqs = (await db.execute(_where(select(func.count()).select_from(Requirement), Requirement))).scalar()
    total_execs = (await db.execute(_where(select(func.count()).select_from(Execution), Execution))).scalar()

    last_exec_q = select(Execution).order_by(Execution.created_at.desc()).limit(1)
    if project_id:
        last_exec_q = last_exec_q.where(Execution.project_id == project_id)
    last_exec = (await db.execute(last_exec_q)).scalars().first()

    # trend: last 7 executions pass rates
    trend_q = select(Execution.name, Execution.pass_rate, Execution.created_at).order_by(
        Execution.created_at.desc()
    ).limit(7)
    if project_id:
        trend_q = trend_q.where(Execution.project_id == project_id)
    trend_rows = (await db.execute(trend_q)).all()
    trend = [{"name": r[0], "pass_rate": r[1], "date": r[2].isoformat() if r[2] else None}
             for r in reversed(trend_rows)]

    # defect counts
    defect_q = select(func.count()).select_from(TestResult).where(
        TestResult.defect_status == "confirmed"
    )
    confirmed_defects = (await db.execute(defect_q)).scalar()

    return {
        # 六大汇总指标卡（5.2.3）
        "cards": cards,
        # 以下为兼容字段 + 执行趋势
        "total_cases": total_cases,
        "total_requirements": total_reqs,
        "total_executions": total_execs,
        "last_pass_rate": last_exec.pass_rate if last_exec else None,
        "last_execution_status": last_exec.status if last_exec else None,
        "confirmed_defects": confirmed_defects,
        "trend": trend,
    }


@router.get("/quality-gate")
async def quality_gate_check(project_id: str, db: AsyncSession = Depends(get_db)):
    """CI/CD质量门禁检查 —— 基于最近一次完成的执行，调用规则引擎评估。"""
    proj = await db.get(Project, project_id)
    if not proj:
        return {"gate": "skip", "reason": "project not found"}

    q = select(Execution).where(
        Execution.project_id == project_id, Execution.status == "done"
    ).order_by(Execution.created_at.desc()).limit(1)
    last_exec = (await db.execute(q)).scalars().first()
    if not last_exec:
        return {"gate": "skip", "reason": "no completed execution"}

    gate_result = await evaluate_gate(db, last_exec)
    await db.commit()

    return {
        "gate": "pass" if gate_result["releasable"] else "fail",
        "execution_id": last_exec.id,
        "pass_rate": last_exec.pass_rate,
        **gate_result,
    }


@router.get("/breakdown")
async def dashboard_breakdown(project_id: str | None = None, db: AsyncSession = Depends(get_db)):
    """按产品线/模块/端(含backend_api)/用例类型统计用例数，按严重程度/状态统计缺陷数。

    用例统计口径与「用例库(用例列表)」保持一致：只算未删除且已入库(in_library)的用例，
    保证用例看板概览数据与用例库列表全量一致（否则看板算全量、列表只算已入库会对不上）。
    """
    # 用例统计统一作用域：未删除 + 已入库（与 testcases 列表 library_only 一致）
    def only_library(qq):
        return qq.where(TestCase.deleted_at.is_(None), TestCase.in_library.is_(True))

    # 一次性取出各分组维度 + 执行方式判定字段，Python 侧聚合（每维度带 自动/手动 拆分）。
    # 执行方式(只看方式不看结果)：manual=手动测试(manual_*)，auto=执行测试自动跑(其它已执行)，notrun=未执行。
    from collections import defaultdict

    def _kind(last_status):
        if last_status in ("manual_passed", "manual_failed"):
            return "manual"
        if last_status and last_status != "not_run":
            return "auto"
        return "notrun"

    rows_q = only_library(select(
        TestCase.product_line, TestCase.modules, TestCase.platforms,
        TestCase.case_type, TestCase.priority, TestCase.last_status, TestCase.is_automated,
    ))
    if project_id:
        rows_q = rows_q.where(TestCase.project_id == project_id)
    rows = (await db.execute(rows_q)).all()

    # 每个维度：key -> [count, auto, manual]
    dim_pl, dim_mod, dim_plat, dim_ct, dim_pri = (defaultdict(lambda: [0, 0, 0]) for _ in range(5))
    cases_total = cases_automated = cases_auto_executed = 0
    for product_line, modules, platforms, case_type, priority, last_status, is_automated in rows:
        cases_total += 1
        if is_automated:
            cases_automated += 1
        k = _kind(last_status)
        ai = 1 if k == "auto" else 0
        mi = 1 if k == "manual" else 0
        if k == "auto":
            cases_auto_executed += 1
        for acc, key in ((dim_pl, product_line), (dim_ct, case_type), (dim_pri, priority)):
            cell = acc[key]; cell[0] += 1; cell[1] += ai; cell[2] += mi
        for m in (modules or []):
            cell = dim_mod[m]; cell[0] += 1; cell[1] += ai; cell[2] += mi
        for p in (platforms or []):
            cell = dim_plat[p]; cell[0] += 1; cell[1] += ai; cell[2] += mi

    def _items(acc):
        items = [{"key": k, "count": v[0], "auto": v[1], "manual": v[2]} for k, v in acc.items()]
        items.sort(key=lambda x: x["count"], reverse=True)
        return items

    by_product_line = _items(dim_pl)
    by_module = _items(dim_mod)
    by_platform = _items(dim_plat)
    by_case_type = _items(dim_ct)
    by_priority = _items(dim_pri)

    # 缺陷：按 severity
    q = select(Defect.severity, func.count()).group_by(Defect.severity)
    if project_id:
        q = q.join(Execution, Defect.execution_id == Execution.id).where(Execution.project_id == project_id)
    by_defect_severity = [{"key": r[0], "count": r[1]} for r in (await db.execute(q)).all()]

    # 缺陷：按 status
    q = select(Defect.status, func.count()).group_by(Defect.status)
    if project_id:
        q = q.join(Execution, Defect.execution_id == Execution.id).where(Execution.project_id == project_id)
    by_defect_status = [{"key": r[0], "count": r[1]} for r in (await db.execute(q)).all()]

    # 用例：按项目
    q = only_library(
        select(Project.id, Project.name, func.count(TestCase.id))
        .join(TestCase, TestCase.project_id == Project.id)
    ).group_by(Project.id, Project.name)
    by_project = [{"key": r[1], "id": r[0], "count": r[2]} for r in (await db.execute(q)).all()]

    return {
        "cases_by_project": by_project,
        "cases_by_product_line": by_product_line,
        "cases_by_module": by_module,
        "cases_by_platform": by_platform,
        "cases_by_case_type": by_case_type,
        "cases_by_priority": by_priority,
        "cases_total": cases_total,
        "cases_automated": cases_automated,
        "cases_auto_executed": cases_auto_executed,
        "defects_by_severity": by_defect_severity,
        "defects_by_status": by_defect_status,
    }


@router.get("/requirements-quality")
async def requirements_quality(
    project_id: str | None = None,
    iteration: str | None = None,
    status: str | None = None,
    platform: str | None = None,
    owner: str | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    """按需求维度聚合质量指标（第5章质量看板），支持项目/迭代/状态/端/归属人筛选。

    聚合逻辑统一收口在 services.dashboard_metrics，与 /summary 共用。
    数据可见范围：普通用户只看自己归属的需求(强制 owner=本人)，管理员不受限。
    """
    from app.services.data_scope import enforce_owner
    owner = await enforce_owner(db, current_user, owner)
    rows = await collect_requirement_rows(
        db, project_id=project_id, iteration=iteration, status=status, platform=platform, owner=owner
    )

    total_req = len(rows)
    done_req = sum(1 for r in rows if r["status"] == "done")
    blocked_req = sum(1 for r in rows if r["releasability"] == "block")
    total_cases = sum(r["total_cases"] for r in rows)
    total_passed = sum(r["passed"] for r in rows)
    total_skipped = sum(r["skipped"] for r in rows)
    # 测试进度 =（通过 + 跳过）/ 总用例数
    test_progress = round(((total_passed + total_skipped) / total_cases * 100) if total_cases > 0 else 0.0, 1)
    total_defects = sum(r["total_defects"] for r in rows)
    total_fixed = sum(r["fixed_defects"] for r in rows)

    # 按缺陷等级（用户枚举）动态聚合
    from collections import Counter
    sev_total_all: Counter = Counter()
    sev_open_all: Counter = Counter()
    for r in rows:
        sev_total_all.update(r.get("sev_total") or {})
        sev_open_all.update(r.get("sev_open") or {})
    severity_breakdown = {
        k: {"total": int(sev_total_all.get(k, 0)), "open": int(sev_open_all.get(k, 0))}
        for k in set(sev_total_all) | set(sev_open_all)
    }

    return {
        "requirements": rows,
        "summary": {
            "total_requirements": total_req,
            "done_requirements": done_req,
            "blocked_requirements": blocked_req,
            "test_progress": test_progress,
            "total_cases": total_cases,
            "total_defects": total_defects,
            "p0_open_defects": sum(r["p0_open"] for r in rows),
            "p1_open_defects": sum(r["p1_open"] for r in rows),
            "p2_open_defects": sum(r["p2_open"] for r in rows),
            "p0_total_defects": sum(r["p0_total"] for r in rows),
            "p1_total_defects": sum(r["p1_total"] for r in rows),
            "p2_total_defects": sum(r["p2_total"] for r in rows),
            "fixed_defects": total_fixed,
            "severity_breakdown": severity_breakdown,
        },
    }


class RequirementsGateRequest(BaseModel):
    requirement_ids: list[str]


@router.post("/requirements-gate")
async def requirements_gate(body: RequirementsGateRequest, db: AsyncSession = Depends(get_db)):
    """CI/CD门禁接口：接收需求ID列表，返回整体可发布状态和阻断需求列表（第5.5节）。"""
    blocking_level = await get_blocking_severity(db)
    blocked_reqs = []
    for req_id in body.requirement_ids:
        req = await db.get(Requirement, req_id)
        if not req:
            continue
        cases = (await db.execute(
            select(TestCase).where(
                TestCase.requirement_id == req_id,
                TestCase.deleted_at.is_(None),
            )
        )).scalars().all()
        total = len(cases)
        if total == 0:
            continue
        passed = sum(1 for c in cases if c.last_status == "passed")
        pass_rate = passed / total * 100
        case_ids = [c.id for c in cases]
        critical_open = 0
        if case_ids:
            critical_open = (await db.execute(
                select(func.count()).select_from(Defect).where(
                    Defect.test_case_id.in_(case_ids),
                    Defect.status.in_(("draft", "confirmed", "ticket_created")),
                    Defect.severity == blocking_level,
                )
            )).scalar() or 0

        reasons = []
        if pass_rate < 100:
            reasons.append(f"测试进度{pass_rate:.0f}%，未达到100%")
        if critical_open > 0:
            reasons.append(f"「{blocking_level}」未关闭缺陷{critical_open}个")
        if reasons:
            blocked_reqs.append({"req_id": req_id, "title": req.title, "reasons": reasons})

    return {
        "releasable": len(blocked_reqs) == 0,
        "blocked_reqs": blocked_reqs,
    }
