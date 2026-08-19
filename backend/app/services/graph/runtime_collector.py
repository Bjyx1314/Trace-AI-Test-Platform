"""运行时数据回补（方案 9.6 第三步 + 9.10）。

1. Page 节点冷启动：page_structure_caches 存量页面导入为 Page 节点种子（回填 graph_node_id）；
2. 执行边回补：TestResult.actual_visited_pages / actual_api_calls →
   Case→visits→Page、Page→calls→API 边（source=runtime_capture）。
冲突消解：runtime_capture > static_scan > llm_inferred（builder.upsert_edge 幂等按 source 区分）。
"""
from __future__ import annotations
import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import PageStructureCache, Project, TestResult, TestCase
from app.services.graph import nodes, builder
from app.services.page_cache_service import normalize_url

logger = logging.getLogger(__name__)


async def seed_pages_from_cache(db: AsyncSession, project_id: str | None = None) -> int:
    """把页面缓存导入为 Page 节点种子（方案 9.10-②）。返回导入数。"""
    stmt = select(PageStructureCache, Project.name).join(Project, PageStructureCache.project_id == Project.id)
    if project_id:
        stmt = stmt.where(PageStructureCache.project_id == project_id)
    rows = (await db.execute(stmt)).all()
    n = 0
    for cache, proj_name in rows:
        nid = nodes.page_id(cache.project_id, cache.url_pattern)
        await builder.upsert_node(
            db, node_id=nid, node_type="page", name=cache.page_name or cache.url_pattern,
            repo=proj_name, attrs={"url_pattern": cache.url_pattern, "hit_count": cache.hit_count},
            seen_in_version="seed",
        )
        cache.graph_node_id = nid
        n += 1
    await db.commit()
    logger.info("Page 节点冷启动：导入 %d 个页面种子", n)
    return n


async def collect_from_result(db: AsyncSession, result_id: str) -> dict:
    """从一条执行结果回补运行时边。返回 {page_edges, api_edges}。"""
    r = await db.get(TestResult, result_id)
    if not r:
        return {"page_edges": 0, "api_edges": 0}
    tc = await db.get(TestCase, r.test_case_id)
    if not tc:
        return {"page_edges": 0, "api_edges": 0}
    case_node = f"case:{tc.id}"
    await builder.upsert_node(db, node_id=case_node, node_type="component", name=tc.title,
                              repo=None, attrs={"case_id": tc.case_id}, seen_in_version="runtime")

    page_edges = api_edges = 0
    visited = r.actual_visited_pages or []
    last_page = None
    for p in visited:
        url = (p or {}).get("url") if isinstance(p, dict) else None
        if not url:
            continue
        pid = nodes.page_id(tc.project_id or "_", url)
        await builder.upsert_node(db, node_id=pid, node_type="page",
                                  name=(p.get("page_name") or normalize_url(url)), seen_in_version="runtime")
        await builder.upsert_edge(db, from_node=case_node, to_node=pid, edge_type="visits",
                                  source="runtime_capture", confidence=0.95,
                                  evidence=f"exec {r.execution_id}", seen_in_version="runtime")
        page_edges += 1
        last_page = pid

    for a in (r.actual_api_calls or []):
        if not isinstance(a, dict) or not a.get("url"):
            continue
        aid = nodes.api_id(a.get("method") or "GET", a["url"])
        await builder.upsert_node(db, node_id=aid, node_type="api", name=f"{a.get('method', 'GET')} {a['url']}", seen_in_version="runtime")
        await builder.upsert_edge(db, from_node=case_node, to_node=aid, edge_type="calls",
                                  source="runtime_capture", confidence=0.9,
                                  evidence=f"exec {r.execution_id}", seen_in_version="runtime")
        # 页面→接口边（若有当前页）
        if last_page:
            await builder.upsert_edge(db, from_node=last_page, to_node=aid, edge_type="calls",
                                      source="runtime_capture", confidence=0.8, seen_in_version="runtime")
        api_edges += 1

    await db.commit()
    return {"page_edges": page_edges, "api_edges": api_edges}
