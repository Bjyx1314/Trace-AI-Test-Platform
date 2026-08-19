"""经验生命周期（方案 6.2.4）：转正/升权/降权/合并/失效。

- 转正：候选被采纳 ≥2 次，或直接关联已确认 Bug → active（沉淀时已处理 Bug 情形）；
- 升/降权：confidence = Wilson 平滑采纳率 adopt/(adopt+reject)；采纳后发现 Bug 额外上调；
- 休眠：连续 N 次「本次不适用」→ dormant；
- 合并：向量相似度 ≥0.92 + LLM 仲裁（这里用向量阈值，LLM 仲裁可选）→ 合并保留 merged_from；
- 失效：trigger_context 引用的节点被删/长期未确认 → stale（阶段五图谱联动）。
"""
from __future__ import annotations
import logging
import math

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Experience
from app.services import embedding as emb

logger = logging.getLogger(__name__)

_ADOPT_TO_ACTIVE = 2      # 采纳≥2 转正
_NOT_APPLICABLE_TO_DORMANT = 5  # 连续不适用休眠
_MERGE_THRESHOLD = 0.92


def _wilson_lower(adopt: int, total: int, z: float = 1.96) -> float:
    """Wilson 平滑采纳率下界，样本少时保守。total=0 时回 0.5 中性。"""
    if total == 0:
        return 0.5
    p = adopt / total
    denom = 1 + z * z / total
    center = p + z * z / (2 * total)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * total)) / total)
    return max(0.0, (center - margin) / denom)


def _recompute_confidence(exp: Experience) -> None:
    st = exp.stats or {}
    adopt, reject = int(st.get("adopt_count", 0)), int(st.get("reject_count", 0))
    conf = _wilson_lower(adopt, adopt + reject)
    if (exp.evidence or {}).get("found_bug"):
        conf = min(1.0, conf + 0.2)  # 关联 Bug 上调
    exp.confidence = round(conf, 4)


async def record_signal(db: AsyncSession, exp_id: str, signal: str, *, not_applicable_streak: bool = False) -> Experience | None:
    """记录一次采纳信号。signal ∈ adopt / reject / hit / not_applicable。"""
    exp = await db.get(Experience, exp_id)
    if not exp:
        return None
    st = dict(exp.stats or {"hit_count": 0, "adopt_count": 0, "reject_count": 0})
    if signal == "adopt":
        st["adopt_count"] = st.get("adopt_count", 0) + 1
        st["hit_count"] = st.get("hit_count", 0) + 1
    elif signal in ("reject", "not_applicable"):
        st["reject_count"] = st.get("reject_count", 0) + 1
        st["hit_count"] = st.get("hit_count", 0) + 1
    elif signal == "hit":
        st["hit_count"] = st.get("hit_count", 0) + 1
    exp.stats = st
    flag_modified(exp, "stats")
    _recompute_confidence(exp)
    # 转正
    if exp.status == "candidate" and st.get("adopt_count", 0) >= _ADOPT_TO_ACTIVE:
        exp.status = "active"
    # 休眠：连续不适用（简化：reject 累计达阈值且 adopt=0）
    if signal == "not_applicable" and st.get("adopt_count", 0) == 0 and st.get("reject_count", 0) >= _NOT_APPLICABLE_TO_DORMANT:
        exp.status = "dormant"
    await db.commit()
    return exp


async def run_merge_maintenance(db: AsyncSession, project_id: str | None = None) -> int:
    """向量近重经验合并（阈值 0.92）。返回合并数。无 embedding 时跳过。"""
    stmt = select(Experience).where(Experience.status.in_(("active", "candidate")))
    if project_id:
        stmt = stmt.where(Experience.project_id == project_id)
    exps = [e for e in (await db.execute(stmt)).scalars().all() if e.embedding]
    merged = 0
    consumed: set[str] = set()
    for i, a in enumerate(exps):
        if a.id in consumed:
            continue
        for b in exps[i + 1:]:
            if b.id in consumed:
                continue
            if emb.cosine(a.embedding, b.embedding) >= _MERGE_THRESHOLD:
                # 合并 b → a：并集建议项、累加 stats、记 merged_from
                a.suggested_covered_items = list(dict.fromkeys((a.suggested_covered_items or []) + (b.suggested_covered_items or [])))
                sa_, sb = a.stats or {}, b.stats or {}
                a.stats = {k: sa_.get(k, 0) + sb.get(k, 0) for k in ("hit_count", "adopt_count", "reject_count")}
                flag_modified(a, "stats")
                a.merged_from = list(a.merged_from or []) + [b.id]
                _recompute_confidence(a)
                b.status = "stale"
                consumed.add(b.id)
                merged += 1
    if merged:
        await db.commit()
    logger.info("经验合并维护完成：合并 %d 条", merged)
    return merged
