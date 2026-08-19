"""第一层硬规则引擎（确定性，方案 6.1）。

MVP 最简：纯内存标签匹配，无 LLM。对每个 covered_item：
  covered_item.risk_tags ∩ rule.match_tags 非空 → 命中：
    - 抬优先级到 rule.min_priority（取更高者）；
    - 追加 rule.required_covered_items 到必测项（用例级 required_items 汇总）；
    - 在 covered_item.matched_rules 与用例 matched_rules 留痕。

规则命中必须留痕（方案 6.1 硬要求1），供质量报告输出「命中规则 R-012：支付路径变更强制 P0」这类解释。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import QualityRule

# 优先级高低：P0 > P1 > P2（数字小=高）
_PRIO_RANK = {"P0": 0, "P1": 1, "P2": 2}


def _higher_priority(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if _PRIO_RANK.get(a, 9) <= _PRIO_RANK.get(b, 9) else b


async def load_active_rules(db: AsyncSession) -> list[QualityRule]:
    return list((await db.execute(
        select(QualityRule).where(QualityRule.active.is_(True))
    )).scalars().all())


def apply_rules_to_item(item: dict, rules: list[QualityRule]) -> dict:
    """对单个 covered_item 应用规则，就地更新并返回。"""
    tags = set(item.get("risk_tags") or [])
    matched = list(item.get("matched_rules") or [])
    for rule in rules:
        if tags & set(rule.match_tags or []):
            if rule.id not in matched:
                matched.append(rule.id)
            item["priority"] = _higher_priority(item.get("priority"), rule.min_priority)
    item["matched_rules"] = matched
    return item


def apply_rules_to_case(case: dict, rules: list[QualityRule]) -> dict:
    """对一条用例（dict，含 covered_items）应用规则：逐项打标 + 抬用例优先级 + 汇总 matched_rules/必测项。"""
    case_matched: list[str] = list(case.get("matched_rules") or [])
    required_items: list[str] = []
    top_priority = case.get("priority")
    for ci in case.get("covered_items") or []:
        apply_rules_to_item(ci, rules)
        for rid in ci.get("matched_rules") or []:
            if rid not in case_matched:
                case_matched.append(rid)
            rule = next((r for r in rules if r.id == rid), None)
            if rule and rule.required_covered_items:
                for req in rule.required_covered_items:
                    name = req if isinstance(req, str) else req.get("name")
                    if name and name not in required_items:
                        required_items.append(name)
        top_priority = _higher_priority(top_priority, ci.get("priority"))
    case["matched_rules"] = case_matched
    if top_priority:
        case["priority"] = top_priority
    if required_items:
        case["required_covered_items"] = required_items
    return case
