"""覆盖项向量索引维护与检索（方案 7.4，阶段四）。

covered_items 内嵌 TestCase.covered_items(JSONB)；本模块把它们的向量副本同步进 covered_item_vecs，
供归并(covered_item_merge)与复用判断(case_reuse)做语义检索。无 embedding key 时 embedding 留空，
检索退化为 struct_key/名称匹配（不阻塞）。
"""
from __future__ import annotations
import logging

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CoveredItemVec, TestCase
from app.services import embedding as emb

logger = logging.getLogger(__name__)


def struct_key(item: dict) -> str:
    o = "".join(str(item.get("object") or "").lower().split())
    a = "".join(str(item.get("action") or "").lower().split())
    return f"{o}|{a}" if (o or a) else "".join(str(item.get("name") or "").lower().split())


def _embed_input(item: dict) -> str:
    return " ".join(str(x) for x in [item.get("name"), item.get("object"), item.get("action"), item.get("expected")] if x)


async def sync_case_vectors(db: AsyncSession, tc: TestCase) -> int:
    """把一条用例的 covered_items 同步进 covered_item_vecs（全量替换该用例的行）。返回同步数。"""
    items = tc.covered_items or []
    # 删旧
    await db.execute(delete(CoveredItemVec).where(CoveredItemVec.case_id == tc.id))
    if not items:
        await db.commit()
        return 0
    # 批量算向量（缺 key → None 列表）
    vecs = await emb.embed_texts([_embed_input(i) for i in items])
    for idx, item in enumerate(items):
        db.add(CoveredItemVec(
            item_id=item.get("item_id") or f"CI_{idx}",
            case_id=tc.id,
            requirement_id=tc.requirement_id,
            project_id=tc.project_id,
            name=item.get("name") or "",
            struct_key=struct_key(item),
            embedding=(vecs[idx] if vecs else None),
        ))
    await db.commit()
    return len(items)


async def search_similar(
    db: AsyncSession, *, project_id: str | None, query_item: dict,
    exclude_case_id: str | None = None, top_k: int = 5, min_sim: float = 0.80,
) -> list[dict]:
    """检索与 query_item 语义/结构相近的已有覆盖项。

    优先向量(pgvector cosine 距离)；无向量时回退 struct_key 精确 + 名称包含。
    返回 [{case_id, item_id, name, sim}]。
    """
    qvec = await emb.embed_text(_embed_input(query_item))
    sk = struct_key(query_item)

    if qvec is not None:
        # pgvector：cosine 距离 <=> ，sim = 1 - distance
        stmt = (
            select(
                CoveredItemVec.case_id, CoveredItemVec.item_id, CoveredItemVec.name,
                (1 - CoveredItemVec.embedding.cosine_distance(qvec)).label("sim"),
            )
            .where(CoveredItemVec.embedding.isnot(None))
        )
        if project_id:
            stmt = stmt.where(CoveredItemVec.project_id == project_id)
        if exclude_case_id:
            stmt = stmt.where(CoveredItemVec.case_id != exclude_case_id)
        stmt = stmt.order_by(CoveredItemVec.embedding.cosine_distance(qvec)).limit(top_k)
        rows = (await db.execute(stmt)).all()
        return [{"case_id": r.case_id, "item_id": r.item_id, "name": r.name, "sim": float(r.sim)}
                for r in rows if float(r.sim) >= min_sim]

    # 降级：struct_key 精确 + 名称包含
    stmt = select(CoveredItemVec).where(CoveredItemVec.struct_key == sk)
    if project_id:
        stmt = stmt.where(CoveredItemVec.project_id == project_id)
    if exclude_case_id:
        stmt = stmt.where(CoveredItemVec.case_id != exclude_case_id)
    rows = (await db.execute(stmt.limit(top_k))).scalars().all()
    return [{"case_id": r.case_id, "item_id": r.item_id, "name": r.name, "sim": 1.0} for r in rows]
