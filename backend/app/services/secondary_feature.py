"""二级功能提取 —— 从需求【原文】提炼页面级功能清单，并把用例归入其一。

用途：用例脑图「需求 → 二级功能 → 用例」的中间层分组。
要点（和用例生成解耦，不影响生成逻辑）：
- 二级功能【来自需求原文】(它涉及哪些页面/入口/业务对象)，不是来自需求分析的 issue_points；
- 页面级/业务对象级粗粒度、去重(2~12字)；字段/搜索/筛选/排序等一律并入所在页面，不单独成组；
- 结果只回填 test_cases.secondary_feature 分组字段，【绝不动 source_issue_point】(那是增量重生成去重依据)。
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_SYSTEM = (
    "你是资深测试架构师。给你一份【需求原文】和一批【测试用例标题】。请：\n"
    "1) 先【只依据需求原文】提炼这份需求涉及的「二级功能」清单——粒度=用户实际进入或操作的"
    "【页面/入口/业务对象】(如“报修处理页”“设备列表”“项目详情”“首页待办”)；\n"
    "   · 粗粒度、去重：同一页面/入口下的字段展示、搜索、筛选、排序、分页、按钮、弹窗、某个时间/字段组件"
    "都【并入该页面】，不要单独拆成“XX字段”“XX时间组件”“XX搜索”这类碎功能；\n"
    "   · 名称 2~12 字，用页面/入口/业务对象名，不要塞长句；语义相同的不同叫法要统一成一个名。\n"
    "2) 再把每条用例归到【其中恰好一个】二级功能(用 case_id 对应)。找不到贴切的就归到最接近的页面，"
    "不要为单条用例新造碎功能。\n"
    "只输出工具结果。"
)

_SCHEMA = {
    "type": "object",
    "properties": {
        "features": {
            "type": "array",
            "items": {"type": "string"},
            "description": "从需求原文提炼的二级功能清单(页面/入口/业务对象级，去重)",
        },
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "case_id": {"type": "string"},
                    "feature": {"type": "string", "description": "必须是 features 里的某一个"},
                },
                "required": ["case_id", "feature"],
            },
        },
    },
    "required": ["features", "assignments"],
    "description": "二级功能清单 + 用例归类",
}


async def extract_and_assign(
    req_title: str, req_content: str, cases: list[dict], *, max_tokens: int = 4096,
) -> dict[str, str]:
    """返回 {case_id: 二级功能名}。失败返回 {}（调用方保持原样，不阻断）。

    cases: [{"id","title"}]（可含 object 辅助，但归类只需 id+title）。
    """
    cases = [c for c in (cases or []) if c.get("id")]
    if not cases or not (req_content or "").strip():
        return {}
    from app.agents.llm import get_provider

    case_lines = "\n".join(f"- {c['id']}｜{(c.get('title') or '').strip()}" for c in cases)
    prompt = (
        f"需求标题：{req_title}\n\n需求原文：\n{req_content}\n\n"
        f"测试用例(共{len(cases)}条，格式 case_id｜标题)：\n{case_lines}\n\n"
        "请先从需求原文提炼二级功能清单，再把每条用例(按 case_id)归入其一。"
    )
    try:
        out = await get_provider().tool(_SYSTEM, prompt, "submit_features", _SCHEMA, max_tokens)
    except Exception as e:  # noqa: BLE001
        logger.warning("二级功能提取失败：%s", e)
        return {}
    feats = {str(f).strip() for f in (out or {}).get("features", []) if str(f).strip()}
    mapping: dict[str, str] = {}
    valid_ids = {c["id"] for c in cases}
    for a in (out or {}).get("assignments", []):
        if not isinstance(a, dict):
            continue
        cid = str(a.get("case_id") or "").strip()
        feat = str(a.get("feature") or "").strip()
        if cid in valid_ids and feat:
            # 容错：归类给了不在清单里的名字也接受(模型偶尔自造)，但优先用清单里的
            mapping[cid] = feat if feat in feats or not feats else feat
    return mapping


async def recompute_for_requirement(db, req) -> int:
    """对一个需求重算二级功能并回填 test_cases.secondary_feature。返回回填条数。"""
    from sqlalchemy import select
    from app.models import TestCase

    cases = (await db.execute(
        select(TestCase).where(TestCase.requirement_id == req.id, TestCase.deleted_at.is_(None))
    )).scalars().all()
    if not cases:
        return 0
    mapping = await extract_and_assign(
        req.title or "", req.content or "",
        [{"id": c.id, "title": c.title} for c in cases],
    )
    n = 0
    for c in cases:
        feat = mapping.get(c.id)
        if feat and feat != c.secondary_feature:
            c.secondary_feature = feat
            n += 1
    await db.commit()
    logger.info("需求 %s 二级功能重算：%d/%d 条回填", req.id, n, len(cases))
    return n


async def backfill_all(db) -> dict:
    """对所有【有用例】的需求重算二级功能。返回 {requirements, updated}。"""
    from sqlalchemy import select, func
    from app.models import TestCase, Requirement

    req_ids = (await db.execute(
        select(TestCase.requirement_id, func.count(TestCase.id))
        .where(TestCase.deleted_at.is_(None), TestCase.requirement_id.is_not(None))
        .group_by(TestCase.requirement_id)
    )).all()
    total_reqs = 0
    total_updated = 0
    for rid, _cnt in req_ids:
        req = (await db.execute(select(Requirement).where(Requirement.id == rid))).scalar_one_or_none()
        if not req:
            continue
        total_reqs += 1
        try:
            total_updated += await recompute_for_requirement(db, req)
        except Exception as e:  # noqa: BLE001
            logger.warning("需求 %s 二级功能重算失败：%s", rid, e)
    return {"requirements": total_reqs, "updated": total_updated}
