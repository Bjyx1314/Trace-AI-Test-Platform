"""缺陷生命周期：执行失败生成待复核缺陷；再次通过/手动通过自动复核为「已解决」。

状态：draft=待复核, confirmed=待处理, ticket_created=已建单, ignored/duplicate=无需处理, fixed=已解决
"""
from __future__ import annotations
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Defect, TestResult, TestCase
from app.services.severity import DEFAULT_SEVERITY

# 未关闭(仍需关注)的缺陷状态
OPEN_DEFECT_STATUSES = ("draft", "confirmed", "ticket_created")


async def create_defect_for_failure(db: AsyncSession, test_result: TestResult, test_case: TestCase) -> Defect | None:
    """用例执行失败 → 生成/刷新一条待复核缺陷。

    - 无未关闭缺陷：新建 status=draft。
    - 已有「待复核(draft)」缺陷：刷新为本次最新失败信息(关联结果/执行、摘要、复现步骤)，
      不新建重复——这样每次重新执行都会刷新缺陷复核内容。
    - 已有「待处理/已建单」缺陷：已进入处理流程，不打扰，返回 None。
    """
    steps = []
    for s in (test_case.steps or []):
        action = s.get("action") if isinstance(s, dict) else str(s)
        if action:
            steps.append(action)
    draft = {
        "summary": test_result.error_message or "用例执行未通过",
        "reproduction_steps": steps,
        "affected_scope": "、".join(test_case.modules or []) or (test_case.product_line or ""),
        "type": "functional",
    }

    # 取该用例最近一条缺陷(任意状态)，决定刷新 / 新建 / 跳过
    recent = (await db.execute(
        select(Defect).where(Defect.test_case_id == test_case.id)
        .order_by(Defect.created_at.desc())
    )).scalars().first()
    if recent is not None:
        if recent.status == "draft":
            # 未确认未忽略 → 刷新现有缺陷内容(保留人工已编辑的严重程度)
            recent.test_result_id = test_result.id
            recent.execution_id = test_result.execution_id
            recent.draft_ticket = draft
            return recent
        # 已确认/已建单/已忽略：与现有内容一致则不重复；不一致才新建一条
        same = (recent.draft_ticket or {}).get("summary") == draft["summary"]
        if same and recent.status in ("confirmed", "ticket_created", "ignored", "duplicate"):
            return None
        # 内容不一致(已确认/忽略) 或 已解决后再次失败(回归) → 落到下方新建

    defect = Defect(
        test_result_id=test_result.id,
        execution_id=test_result.execution_id,
        test_case_id=test_case.id,
        title=f"{test_case.title} 执行失败",
        severity=DEFAULT_SEVERITY,
        confidence="MEDIUM",
        status="draft",
        draft_ticket=draft,
    )
    db.add(defect)
    return defect


async def resolve_open_defects_for_case(db: AsyncSession, test_case_id: str, note: str = "") -> int:
    """用例再次通过/手动通过 → 把该用例所有未关闭缺陷自动复核为「已解决」(fixed)。
    若缺陷已建到外部任务系统，尽力回写为 accepted。返回处理数量。"""
    open_defects = (await db.execute(
        select(Defect).where(
            Defect.test_case_id == test_case_id,
            Defect.status.in_(OPEN_DEFECT_STATUSES),
        )
    )).scalars().all()
    if not open_defects:
        return 0

    from app.services import external_tasks
    for d in open_defects:
        d.status = "fixed"
        if note:
            base = dict(d.draft_ticket or {})
            base["resolution_note"] = note
            d.draft_ticket = base
        if d.external_ticket_id and external_tasks.is_configured():
            try:
                await external_tasks.update_bug_status(d.external_ticket_id, "accepted", note or "测试通过，缺陷已解决")
            except Exception:
                pass
    return len(open_defects)
