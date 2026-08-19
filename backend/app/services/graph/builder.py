"""图谱构建：消费 code-graph-scan skill 输出，全量事实导入节点/边（方案 9.6 第一步）。

skill 输出结构（约定）：
{
  "schema_version": "1.0",
  "repo": "order-web",
  "nodes": [{"node_id","node_type","name","attrs"}...],
  "edges": [{"from","to","edge_type","source","confidence","evidence"}...]
}
节点/边幂等 upsert；打统一 seen_in_version（本轮扫描版本），供陈旧性治理续期。
"""
from __future__ import annotations
import logging

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GraphNode, GraphEdge

logger = logging.getLogger(__name__)


async def upsert_node(db: AsyncSession, *, node_id: str, node_type: str, name: str,
                      repo: str | None = None, attrs: dict | None = None,
                      seen_in_version: str | None = None) -> None:
    stmt = pg_insert(GraphNode).values(
        node_id=node_id, node_type=node_type, name=name, repo=repo,
        attrs=attrs or {}, seen_in_version=seen_in_version, status="active",
    ).on_conflict_do_update(
        index_elements=[GraphNode.node_id],
        set_={"name": name, "repo": repo, "attrs": attrs or {},
              "seen_in_version": seen_in_version, "status": "active"},
    )
    await db.execute(stmt)


async def upsert_edge(db: AsyncSession, *, from_node: str, to_node: str, edge_type: str,
                      source: str = "static_scan", confidence: float = 1.0,
                      evidence: str | None = None, seen_in_version: str | None = None) -> None:
    stmt = pg_insert(GraphEdge).values(
        from_node=from_node, to_node=to_node, edge_type=edge_type, source=source,
        confidence=confidence, evidence=evidence, seen_in_version=seen_in_version, status="active",
    ).on_conflict_do_update(
        constraint="uq_graph_edge",
        set_={"confidence": confidence, "evidence": evidence,
              "seen_in_version": seen_in_version, "status": "active"},
    )
    await db.execute(stmt)


async def import_scan_output(db: AsyncSession, scan: dict, *, version: str) -> dict:
    """全量导入 code-graph-scan 输出。返回 {nodes, edges} 计数。"""
    nodes = scan.get("nodes") or []
    edges = scan.get("edges") or []
    repo = scan.get("repo")
    for n in nodes:
        nid = n.get("node_id")
        if not nid:
            continue
        await upsert_node(db, node_id=nid, node_type=n.get("node_type", "file"),
                          name=n.get("name") or nid, repo=n.get("repo") or repo,
                          attrs=n.get("attrs") or {}, seen_in_version=version)
    # 先提交节点，再建边（边的 from/to 需节点存在，虽无 FK 但语义上先节点）
    await db.commit()
    valid_ids = set((await db.execute(select(GraphNode.node_id))).scalars().all())
    edge_n = 0
    for e in edges:
        f, t = e.get("from"), e.get("to")
        if not f or not t or f not in valid_ids or t not in valid_ids:
            continue
        await upsert_edge(db, from_node=f, to_node=t, edge_type=e.get("edge_type", "calls"),
                          source=e.get("source", "static_scan"), confidence=e.get("confidence", 1.0),
                          evidence=e.get("evidence"), seen_in_version=version)
        edge_n += 1
    await db.commit()
    logger.info("图谱全量导入[repo=%s ver=%s]：节点 %d 边 %d", repo, version, len(nodes), edge_n)
    return {"nodes": len(nodes), "edges": edge_n}
