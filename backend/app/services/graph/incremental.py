"""MR 增量更新（方案 9.6 第二步）。

MR 改动文件 → 命中 File/Component/Service 节点 → rename 检测(git -M)节点合并 →
重扫改动文件出边 → 打新 seen_in_version → 生成本次影响图快照（挂 ChangeImpactRecord）。

MVP-完整版：增量扫描复用 code-graph-scan skill（限定到改动文件），产出增量节点/边，
经 builder.import_scan_output 幂等合并；rename 用 git 的 -M 检测由 skill 侧提供 old_path→new_path 映射。
"""
from __future__ import annotations
import logging

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import GraphNode, GraphEdge
from app.services.graph import builder

logger = logging.getLogger(__name__)


async def merge_rename(db: AsyncSession, old_node_id: str, new_node_id: str) -> None:
    """rename 节点合并：把指向 old 的边改指向 new，old 标 removed（保留 alias）。"""
    old = await db.get(GraphNode, old_node_id)
    new = await db.get(GraphNode, new_node_id)
    if not old:
        return
    if not new:
        # 新节点不存在则直接改 id 属性
        old.attrs = {**(old.attrs or {}), "renamed_to": new_node_id}
        await db.commit()
        return
    from sqlalchemy import update
    await db.execute(update(GraphEdge).where(GraphEdge.from_node == old_node_id).values(from_node=new_node_id))
    await db.execute(update(GraphEdge).where(GraphEdge.to_node == old_node_id).values(to_node=new_node_id))
    new.attrs = {**(new.attrs or {}), "alias": (new.attrs or {}).get("alias", []) + [old_node_id]}
    old.status = "removed"
    await db.commit()
    logger.info("图谱 rename 合并：%s → %s", old_node_id, new_node_id)


async def apply_incremental(db: AsyncSession, scan: dict, *, version: str) -> dict:
    """增量应用扫描输出（含 renames）。renames=[{old,new}]。"""
    for rn in scan.get("renames") or []:
        if rn.get("old") and rn.get("new"):
            await merge_rename(db, rn["old"], rn["new"])
    return await builder.import_scan_output(db, scan, version=version)
