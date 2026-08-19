"""经验召回（方案 6.2.3）：标签精确匹配 + 语义检索混合。

召回 = 精确通道(标签/节点 ID 交集，权重高) ∪ 语义通道(embedding 近邻)。
排序分 = confidence × (α·标签命中数归一 + β·向量相似度)。
无 embedding key 时自动退化为纯标签通道（不阻塞）。
每条命中带 hit_reason，供前端展示「为什么召回它」。
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Experience
from app.services import embedding as emb

_ALPHA = 0.6  # 标签命中权重
_BETA = 0.4   # 语义相似度权重


def _tag_overlap(query_tags: set[str], ctx: dict) -> tuple[int, list[str]]:
    ctx_tags = set(ctx.get("risk_tags") or []) | set(ctx.get("affected_features") or [])
    ctx_tags |= set(ctx.get("service_node_ids") or []) | set(ctx.get("api_node_ids") or [])
    hit = query_tags & ctx_tags
    return len(hit), sorted(hit)


async def recall(
    db: AsyncSession,
    *,
    project_id: str | None = None,
    query_text: str = "",
    risk_tags: list[str] | None = None,
    service_node_ids: list[str] | None = None,
    api_node_ids: list[str] | None = None,
    top_n: int = 5,
) -> list[dict]:
    """混合召回 Top-N。返回 [{experience, score, hit_reason, channel}]。"""
    query_tags = set(risk_tags or []) | set(service_node_ids or []) | set(api_node_ids or [])

    stmt = select(Experience).where(Experience.status.in_(("active", "candidate")))
    if project_id:
        stmt = stmt.where((Experience.project_id == project_id) | (Experience.project_id.is_(None)))
    candidates = list((await db.execute(stmt)).scalars().all())
    if not candidates:
        return []

    # 语义通道：对 query_text 求向量，与各经验 embedding 内存 cosine（DB ivfflat 亦可，这里量小走内存稳）
    qvec = await emb.embed_text(query_text) if query_text.strip() else None

    scored: list[dict] = []
    for exp in candidates:
        n_hit, hit_tags = _tag_overlap(query_tags, exp.trigger_context or {})
        tag_score = min(1.0, n_hit / 3.0) if query_tags else 0.0
        sem_score = emb.cosine(qvec, exp.embedding) if (qvec and exp.embedding) else 0.0
        if n_hit == 0 and sem_score < 0.75:
            continue  # 既无标签命中又语义不近 → 不召回
        score = float(exp.confidence) * (_ALPHA * tag_score + _BETA * sem_score)
        channel = "tag+semantic" if (n_hit and sem_score) else ("tag" if n_hit else "semantic")
        reasons = []
        if hit_tags:
            reasons.append(f"标签/节点命中：{'、'.join(hit_tags)}")
        if sem_score >= 0.75:
            reasons.append(f"语义相近（{sem_score:.2f}）")
        bug = (exp.evidence or {}).get("found_bug")
        if bug:
            reasons.append("历史曾发现 Bug")
        scored.append({
            "experience_id": exp.id,
            "title": exp.title,
            "source": exp.source,
            "confidence": round(float(exp.confidence), 2),
            "status": exp.status,
            "suggested_covered_items": exp.suggested_covered_items or [],
            "found_bug": bool(bug),
            "score": round(score, 4),
            "channel": channel,
            "hit_reason": "；".join(reasons) or "候选经验",
            "stats": exp.stats or {},
        })

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:top_n]


def build_experience_prompt_block(hits: list[dict], limit: int = 8) -> str:
    """把召回经验拼成注入生成提示词的『历史教训』段。空则返回空串（保证零回归）。"""
    picked = (hits or [])[:limit]
    if not picked:
        return ""
    lines = [
        "历史教训（以下是本项目过往漏测/纠正沉淀的质量点，若适用于当前需求，"
        "必须生成对应覆盖项，勿遗漏）：",
    ]
    for h in picked:
        items = "、".join(h.get("suggested_covered_items") or []) or h.get("title", "")
        tag = "【曾发现线上/缺陷】" if h.get("found_bug") else ""
        why = h.get("hit_reason") or ""
        lines.append(f"- {tag}{items}（因：{why}）")
    return "\n".join(lines)


def match_adopted_experiences(hits: list[dict], generated_item_names: list[str]) -> list[str]:
    """生成结果里出现与经验建议覆盖项同名/互为子串的项 → 视为该经验被采纳。返回 experience_id 列表。"""
    gen = [(_g or "").strip() for _g in (generated_item_names or []) if (_g or "").strip()]
    adopted: list[str] = []
    for h in hits or []:
        eid = h.get("experience_id")
        if not eid:
            continue
        for s in h.get("suggested_covered_items") or []:
            s = (s or "").strip()
            if s and any(s == g or s in g or g in s for g in gen):
                adopted.append(eid)
                break
    return adopted


async def recall_for_generation(
    db: AsyncSession, *, project_id: str | None, query_text: str = "",
    risk_tags: list[str] | None = None, api_node_ids: list[str] | None = None,
    limit: int = 8,
) -> list[dict]:
    """生成前召回：复用 recall()，只取足够可信的经验用于回灌。空则空。"""
    hits = await recall(
        db, project_id=project_id, query_text=query_text,
        risk_tags=risk_tags, api_node_ids=api_node_ids, top_n=limit,
    )
    # 只回灌 active，或 candidate 中置信度≥0.6（避免弱信号污染生成）
    return [h for h in hits if h.get("status") == "active" or float(h.get("confidence", 0)) >= 0.6]
