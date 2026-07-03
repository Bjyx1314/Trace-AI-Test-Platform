"""质量看板聚合服务（架构文档第5章）。

把"按需求维度聚合质量指标"的核心逻辑从 router 抽到 service 层，
供 /api/dashboard/summary（六大汇总指标卡，5.2.3）与
/api/dashboard/requirements-quality（需求列表，5.2.4）共用，避免重复计算。
"""
from __future__ import annotations

from collections import Counter

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import TestCase, Requirement, Defect, Project
from app.services.severity import get_blocking_severity

# 缺陷状态归类（与 dashboard.requirements_quality 保持一致）
OPEN_DEFECT_STATUSES = ("draft", "confirmed", "ticket_created")
FIXED_DEFECT_STATUSES = ("ignored", "duplicate", "fixed")


async def collect_requirement_rows(
    db: AsyncSession,
    *,
    project_id: str | None = None,
    iteration: str | None = None,
    status: str | None = None,
    platform: str | None = None,
    owner: str | None = None,
) -> list[dict]:
    """按需求维度聚合质量指标，返回每条需求的指标行。

    每行字段与质量看板需求列表（5.2.4）一致：用例分布、缺陷分布、
    可发布判定（releasability）及阻断原因。筛选维度：项目/迭代/需求状态/端。
    """
    q_req = select(Requirement).order_by(Requirement.created_at.desc())
    if project_id:
        q_req = q_req.where(Requirement.project_id == project_id)
    if iteration:
        q_req = q_req.where(Requirement.iteration == iteration)
    if status:
        q_req = q_req.where(Requirement.status == status)
    # 归属人筛选落到切片：保留「需求 owner 或 任一切片 owner」匹配的需求，故移到下方按切片后过滤
    requirements = (await db.execute(q_req)).scalars().all()

    # 缺陷等级（按用户在枚举管理定义的 category=severity，sort_order 升序=由重到轻）
    # 最高等级作为「可发布」阻断判定级别（统一收口在 services.severity）
    blocking_level = await get_blocking_severity(db)

    project_cache: dict[str, str] = {}
    rows: list[dict] = []

    for req in requirements:
        if req.project_id not in project_cache:
            proj = await db.get(Project, req.project_id)
            project_cache[req.project_id] = proj.name if proj else ""

        cases = (await db.execute(
            select(TestCase).where(
                TestCase.requirement_id == req.id,
                TestCase.deleted_at.is_(None),
            )
        )).scalars().all()

        # 端筛选：需求名下有任一用例覆盖该端才保留
        if platform and not any(platform in (c.platforms or []) for c in cases):
            continue

        total = len(cases)
        passed = sum(1 for c in cases if c.last_status == "passed")
        failed = sum(1 for c in cases if c.last_status == "failed")
        skipped = sum(1 for c in cases if c.last_status == "skipped")
        not_run = sum(1 for c in cases if c.last_status == "not_run")
        pass_rate = (passed / total * 100) if total > 0 else 0.0
        p0_failed = sum(1 for c in cases if c.priority == "P0" and c.last_status == "failed")

        case_ids = [c.id for c in cases]
        if case_ids:
            defects = (await db.execute(
                select(Defect).where(Defect.test_case_id.in_(case_ids))
            )).scalars().all()
        else:
            defects = []

        open_defects = [d for d in defects if d.status in OPEN_DEFECT_STATUSES]
        fixed_defects = [d for d in defects if d.status in FIXED_DEFECT_STATUSES]
        # 按缺陷等级动态统计（缺陷等级由用户枚举定义）
        sev_total = Counter(d.severity for d in defects)
        sev_open = Counter(d.severity for d in open_defects)
        # 最高等级未关闭数 → 用于可发布阻断（替代旧的硬编码 P0）
        critical_open = sev_open.get(blocking_level, 0)
        # 兼容旧字段（部分旧逻辑/卡片仍引用）：P0/P1/P2 不存在时为 0
        p0_open, p1_open, p2_open = sev_open.get("P0", 0), sev_open.get("P1", 0), sev_open.get("P2", 0)
        p0_total, p1_total, p2_total = sev_total.get("P0", 0), sev_total.get("P1", 0), sev_total.get("P2", 0)

        releasability, blocking_reasons = _evaluate_releasability(
            total=total,
            not_run=not_run,
            pass_rate=pass_rate,
            p0_open=critical_open,
        )

        # 每切片(负责范围)指标，供质量看板展开行
        from app.models import RequirementSlice
        slice_rows = (await db.execute(
            select(RequirementSlice).where(RequirementSlice.requirement_id == req.id)
            .order_by(RequirementSlice.is_default.desc(), RequirementSlice.created_at.asc())
        )).scalars().all()
        cases_by_slice: dict = {}
        for c in cases:
            cases_by_slice.setdefault(c.slice_id, []).append(c)
        slices_out = []
        slice_owners = set()
        for sl in slice_rows:
            if sl.owner_name:
                slice_owners.add(sl.owner_name)
            sc = cases_by_slice.get(sl.id, [])
            s_total = len(sc)
            s_passed = sum(1 for c in sc if c.last_status == "passed")
            s_skipped = sum(1 for c in sc if c.last_status == "skipped")
            s_not_run = sum(1 for c in sc if c.last_status == "not_run")
            s_ids = {c.id for c in sc}
            s_def = [d for d in defects if d.test_case_id in s_ids]
            s_open = [d for d in s_def if d.status in OPEN_DEFECT_STATUSES]
            s_fixed = [d for d in s_def if d.status in FIXED_DEFECT_STATUSES]
            s_crit = sum(1 for d in s_open if d.severity == blocking_level)
            s_pr = (s_passed / s_total * 100) if s_total > 0 else 0.0
            s_rel, _ = _evaluate_releasability(total=s_total, not_run=s_not_run, pass_rate=s_pr, p0_open=s_crit)
            # 全文默认切片的「是否分析过」以需求本身为准(读取视图)；其它切片看自身
            analyzed = bool(req.analysis_result) if sl.is_default else bool(sl.analysis_result)
            slices_out.append({
                "id": sl.id, "scope_label": sl.scope_label, "owner_name": sl.owner_name,
                "is_default": sl.is_default, "status": sl.status, "analyzed": analyzed,
                "total_cases": s_total, "passed": s_passed, "skipped": s_skipped,
                "total_defects": len(s_def), "fixed_defects": len(s_fixed),
                "sev_open": dict(Counter(d.severity for d in s_open)),
                "releasability": s_rel,
            })

        # 归属人筛选(落到切片)：需求 owner 或 任一切片 owner 命中才保留
        if owner:
            if owner == "__unassigned__":
                if req.owner_name:
                    continue
            elif req.owner_name != owner and owner not in slice_owners:
                continue

        rows.append({
            "slices": slices_out,
            "id": req.id,
            "title": req.title,
            "status": req.status,
            "iteration": req.iteration,
            "owner_name": req.owner_name,
            "project_name": project_cache[req.project_id],
            "project_id": req.project_id,
            "total_cases": total,
            "passed": passed,
            "failed": failed,
            "skipped": skipped,
            "not_run": not_run,
            "pass_rate": round(pass_rate, 1),
            "p0_open": p0_open,
            "p1_open": p1_open,
            "p2_open": p2_open,
            "p0_total": p0_total,
            "p1_total": p1_total,
            "p2_total": p2_total,
            "total_defects": len(defects),
            "open_defects": len(open_defects),
            "fixed_defects": len(fixed_defects),
            "sev_total": dict(sev_total),
            "sev_open": dict(sev_open),
            "releasability": releasability,
            "blocking_reasons": blocking_reasons,
        })

    return rows


def _evaluate_releasability(
    *,
    total: int,
    not_run: int,
    pass_rate: float,
    p0_open: int,
) -> tuple[str, list[str]]:
    """单条需求的可发布判定。返回 (releasability, blocking_reasons)。

    固定规则：测试进度100% + 2级bug（P0）数为0才可发布。
    """
    if total == 0 or not_run == total:
        return "not_started", []

    blocking_reasons: list[str] = []

    if pass_rate < 100:
        blocking_reasons.append(f"测试进度{pass_rate:.0f}%，未达到100%")
    if p0_open > 0:
        blocking_reasons.append(f"2级（P0）未关闭缺陷{p0_open}个")

    if blocking_reasons:
        return "block", blocking_reasons
    return "pass", []


def build_summary_cards(rows: list[dict]) -> dict:
    """从需求指标行汇总出五大指标卡（5.2.3），每张卡带数值与颜色判定。

    颜色规则严格按文档 5.2.3：
    - 需求完成进度：纯进度，无颜色
    - 测试进度（=（通过+跳过）/ 总用例）：≥95 绿 / 80~95 橙 / <80 红
    - 缺陷总数：P0>0 红 / P1>0 橙 / 全 P2 蓝
    - 缺陷修复进度：100 绿 / ≥50 橙 / <50 红
    - 阻塞中需求：>0 红
    """
    total_req = len(rows)
    done_req = sum(1 for r in rows if r["status"] == "done")
    blocked_req = sum(1 for r in rows if r["releasability"] == "block")

    total_cases = sum(r["total_cases"] for r in rows)
    total_passed = sum(r["passed"] for r in rows)
    total_failed = sum(r["failed"] for r in rows)
    total_skipped = sum(r["skipped"] for r in rows)
    # 测试进度 =（通过 + 跳过）/ 总用例数
    test_progress = round(((total_passed + total_skipped) / total_cases * 100) if total_cases > 0 else 0.0, 1)

    total_defects = sum(r["total_defects"] for r in rows)
    p0_open = sum(r["p0_open"] for r in rows)
    p1_open = sum(r["p1_open"] for r in rows)
    p2_open = sum(r["p2_open"] for r in rows)
    fixed_defects = sum(r["fixed_defects"] for r in rows)
    fix_rate = round((fixed_defects / total_defects * 100) if total_defects > 0 else 0.0, 1)

    completion = round((done_req / total_req * 100) if total_req > 0 else 0.0, 1)

    return {
        "requirement_completion": {
            "done": done_req,
            "total": total_req,
            "rate": completion,
            "color": "none",  # 纯进度展示
        },
        "test_progress": {
            "passed": total_passed,
            "failed": total_failed,
            "skipped": total_skipped,
            "total": total_cases,
            "rate": test_progress,
            "color": _pass_rate_color(test_progress, total_cases),
        },
        "defect_total": {
            "total": total_defects,
            "p0_open": p0_open,
            "p1_open": p1_open,
            "p2_open": p2_open,
            "color": _defect_total_color(p0_open, p1_open, total_defects),
        },
        "defect_fix_progress": {
            "fixed": fixed_defects,
            "total": total_defects,
            "rate": fix_rate,
            "color": _fix_rate_color(fix_rate, total_defects),
        },
        "blocked_requirements": {
            "count": blocked_req,
            "color": "red" if blocked_req > 0 else "none",
        },
    }


def _pass_rate_color(rate: float, total_cases: int) -> str:
    if total_cases == 0:
        return "none"
    if rate >= 95:
        return "green"
    if rate >= 80:
        return "orange"
    return "red"


def _defect_total_color(p0_open: int, p1_open: int, total_defects: int) -> str:
    if total_defects == 0:
        return "none"
    if p0_open > 0:
        return "red"
    if p1_open > 0:
        return "orange"
    return "blue"  # 全 P2


def _fix_rate_color(rate: float, total_defects: int) -> str:
    if total_defects == 0:
        return "none"
    if rate >= 100:
        return "green"
    if rate >= 50:
        return "orange"
    return "red"
