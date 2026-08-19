"""影响面扩散（方案 9.7）：从命中节点双向 BFS + 边权 + 置信度连乘剪枝 + stale 过滤。

默认最大 2 跳（P0 变更放宽 3）；边权 handled_by/calls=1.0、belongs_to/defines=0.8、llm_inferred 边×0.5；
路径置信度（边 confidence 连乘）<0.3 停止；stale 节点不扩散。输出影响节点 + 每节点证据链。
"""
from __future__ import annotations

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GraphNode, GraphEdge

_EDGE_WEIGHT = {"handled_by": 1.0, "calls": 1.0, "visits": 0.9, "belongs_to": 0.8, "defines": 0.8, "accesses": 0.7}
_MIN_PATH_CONF = 0.3


async def expand(db: AsyncSession, seed_nodes: list[str], *, max_hops: int = 2) -> dict:
    """双向 BFS 扩散。返回 {nodes:[{node_id,node_type,name,path_conf,evidence_chain}], edges:[...]}。"""
    seeds = [s for s in seed_nodes if s]
    if not seeds:
        return {"nodes": [], "edges": [], "truncated": []}

    visited: dict[str, dict] = {}
    truncated: list[str] = []
    frontier = [(s, 1.0, []) for s in seeds]  # (node, path_conf, chain)
    for s in seeds:
        visited[s] = {"node_id": s, "path_conf": 1.0, "evidence_chain": []}

    for hop in range(max_hops):
        if not frontier:
            break
        next_frontier = []
        node_ids = [n for n, _, _ in frontier]
        # 取与当前 frontier 相连的边（双向），排除 stale
        rows = (await db.execute(
            select(GraphEdge).where(
                or_(GraphEdge.from_node.in_(node_ids), GraphEdge.to_node.in_(node_ids)),
                GraphEdge.status == "active",
            )
        )).scalars().all()
        for node, conf, chain in frontier:
            for e in rows:
                if e.from_node == node:
                    nb = e.to_node
                elif e.to_node == node:
                    nb = e.from_node
                else:
                    continue
                w = _EDGE_WEIGHT.get(e.edge_type, 0.6)
                if e.source == "llm_inferred":
                    w *= 0.5
                new_conf = conf * w * float(e.confidence or 1.0)
                if new_conf < _MIN_PATH_CONF:
                    if nb not in visited:
                        truncated.append(nb)
                    continue
                new_chain = chain + [f"{node} —{e.edge_type}→ {nb}" + (f" [{e.evidence}]" if e.evidence else "")]
                if nb not in visited or visited[nb]["path_conf"] < new_conf:
                    visited[nb] = {"node_id": nb, "path_conf": round(new_conf, 3), "evidence_chain": new_chain}
                    next_frontier.append((nb, new_conf, new_chain))
        frontier = next_frontier

    # 附上节点类型/名字（过滤 stale/removed 节点）
    ids = list(visited.keys())
    meta = {}
    if ids:
        for n in (await db.execute(select(GraphNode).where(GraphNode.node_id.in_(ids)))).scalars().all():
            if n.status in ("stale", "removed"):
                visited.pop(n.node_id, None)
                continue
            meta[n.node_id] = {"node_type": n.node_type, "name": n.name, "repo": n.repo}
    out_nodes = []
    for nid, info in visited.items():
        if nid in seeds and not meta.get(nid):
            continue  # seed 若无节点记录也跳过展示
        out_nodes.append({**info, **meta.get(nid, {"node_type": "unknown", "name": nid})})
    out_nodes.sort(key=lambda x: x["path_conf"], reverse=True)
    return {"nodes": out_nodes, "seeds": seeds, "truncated": truncated}


async def expand_pages_apis(db: AsyncSession, seed_nodes: list[str], *, max_hops: int = 2) -> dict:
    """扩散并按类型汇总受影响的页面/接口（供影响面合并去重）。"""
    res = await expand(db, seed_nodes, max_hops=max_hops)
    pages = [n["name"] for n in res["nodes"] if n.get("node_type") == "page"]
    apis = [n["name"] for n in res["nodes"] if n.get("node_type") == "api"]
    return {"affected_pages": pages, "affected_apis": apis, "detail": res}
