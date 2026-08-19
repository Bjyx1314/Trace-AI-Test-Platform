"""陈旧性治理（方案 9.8）：与页面缓存 STALE_AFTER_DAYS 对齐。

- 每轮全量扫描打统一 seen_in_version；增量只给触达的节点/边续期；
- 连续 2 个全量版本未确认的节点/边 → stale（扩散/召回时过滤）；
- stale 超 90 天 → removed（软删，保留历史引用完整性）；
- 节点转 stale 时联动：引用它的 Experience 标 stale。
"""
from __future__ import annotations
import logging

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GraphNode, GraphEdge, Experience

logger = logging.getLogger(__name__)


async def mark_stale(db: AsyncSession, current_version: str, previous_versions: list[str]) -> dict:
    """把 seen_in_version 不在 [current] + previous_versions 的活跃节点/边标 stale。"""
    keep = set([current_version] + (previous_versions or []))
    # 节点
    nodes = (await db.execute(
        select(GraphNode).where(GraphNode.status == "active")
    )).scalars().all()
    stale_nodes = 0
    stale_ids: list[str] = []
    for n in nodes:
        if n.seen_in_version not in keep and n.seen_in_version not in ("seed", "runtime"):
            n.status = "stale"
            stale_nodes += 1
            stale_ids.append(n.node_id)
    # 边
    edges = (await db.execute(select(GraphEdge).where(GraphEdge.status == "active"))).scalars().all()
    stale_edges = 0
    for e in edges:
        if e.seen_in_version not in keep and e.seen_in_version not in ("seed", "runtime"):
            e.status = "stale"
            stale_edges += 1
    # 联动经验：trigger_context 引用 stale 节点 → 经验 stale（简化：node_ids 交集）
    if stale_ids:
        exps = (await db.execute(select(Experience).where(Experience.status.in_(("active", "candidate"))))).scalars().all()
        stale_set = set(stale_ids)
        for exp in exps:
            ctx = exp.trigger_context or {}
            refs = set(ctx.get("service_node_ids") or []) | set(ctx.get("api_node_ids") or [])
            if refs & stale_set:
                exp.status = "stale"
    await db.commit()
    logger.info("陈旧性治理[ver=%s]：节点 stale %d，边 stale %d", current_version, stale_nodes, stale_edges)
    return {"stale_nodes": stale_nodes, "stale_edges": stale_edges}
