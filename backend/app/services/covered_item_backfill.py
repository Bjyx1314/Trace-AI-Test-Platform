"""存量用例覆盖项批量回填（方案 10.4-②）。

现状：存量用例没有 covered_items，导致阶段 B 复用检索(B5 比对)检索不到、重复生成。
本任务用 LLM 从存量用例 title/steps/expected_results 提取覆盖项，sources=["backfill"] 低置信标注。

- 幂等：只处理 covered_items 为空的用例；
- 分批：按项目分批、优先在库用例(in_library=true)；
- 失败跳过不阻塞：单条回填失败记录日志继续下一条。
"""
from __future__ import annotations
import logging

from sqlalchemy import select, or_, func as sqlfunc
from sqlalchemy.orm.attributes import flag_modified

from app.database import AsyncSessionLocal
from app.models import TestCase
from app.agents import TestCaseGeneratorAgent

logger = logging.getLogger(__name__)


async def backfill_project(
    project_id: str | None = None,
    limit: int = 200,
    only_in_library: bool = False,
) -> dict:
    """回填一批 covered_items 为空的用例。返回统计。

    project_id 空=全部项目；limit 单次上限；only_in_library=优先在库用例。
    """
    agent = TestCaseGeneratorAgent()
    scanned = filled = failed = 0

    async with AsyncSessionLocal() as db:
        stmt = select(TestCase).where(
            TestCase.deleted_at.is_(None),
            # covered_items 为空数组或 null
            or_(TestCase.covered_items == [], TestCase.covered_items.is_(None)),
        )
        if project_id:
            stmt = stmt.where(TestCase.project_id == project_id)
        if only_in_library:
            stmt = stmt.where(TestCase.in_library.is_(True))
        stmt = stmt.limit(limit)
        cases = (await db.execute(stmt)).scalars().all()

        for tc in cases:
            scanned += 1
            try:
                items = await agent.backfill_covered_items(
                    tc.title, tc.steps, tc.expected_result,
                )
                if not items:
                    continue
                tc.covered_items = items
                flag_modified(tc, "covered_items")
                # 用例级 sources 并入 backfill
                srcs = list(tc.sources or [])
                if "backfill" not in srcs:
                    srcs.append("backfill")
                tc.sources = srcs
                # 汇总 risk_tags
                tags = list(tc.risk_tags or [])
                for ci in items:
                    for t in ci.get("risk_tags") or []:
                        if t not in tags:
                            tags.append(t)
                tc.risk_tags = tags
                filled += 1
            except Exception as e:  # noqa: BLE001 单条失败不阻塞整批
                failed += 1
                logger.warning("用例 %s 覆盖项回填失败：%s", tc.case_id, e)

        await db.commit()
        # 阶段四：回填后同步向量索引
        try:
            from app.services.covered_item_vec import sync_case_vectors
            for tc in cases:
                if tc.covered_items:
                    await sync_case_vectors(db, tc)
        except Exception:  # noqa: BLE001
            pass

    logger.info("覆盖项回填完成：扫描 %d 填充 %d 失败 %d", scanned, filled, failed)
    return {"scanned": scanned, "filled": filled, "failed": failed}


async def count_pending(project_id: str | None = None) -> int:
    """统计还有多少用例待回填。"""
    async with AsyncSessionLocal() as db:
        stmt = select(sqlfunc.count()).select_from(TestCase).where(
            TestCase.deleted_at.is_(None),
            or_(TestCase.covered_items == [], TestCase.covered_items.is_(None)),
        )
        if project_id:
            stmt = stmt.where(TestCase.project_id == project_id)
        return (await db.execute(stmt)).scalar_one()
