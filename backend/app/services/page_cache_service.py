"""页面结构缓存逻辑层（架构文档 7.3）。

本模块只负责缓存的"决策与数据加工"，不做真实浏览器 / DOM 操作：
DOM 区块及其元素由调用方（未来的真实 Playwright 执行引擎，或 mock）传入，
这里实现 url_pattern 归一化匹配、区块 hash 计算、命中决策（全/部分/无）、
差异判定与失效计算。全部为可单测的纯函数；带 db 的部分集中在 router/上层。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta, timezone

# 缓存超 N 天未被命中则视为过期（7.3.6）
STALE_AFTER_DAYS = 30

# URL 路径段归一化规则：把易变段替换为占位符，使 /admin/users/123 与
# /admin/users/456 命中同一 pattern（动态路由 pattern 匹配）。
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I
)
_LONG_HEX_RE = re.compile(r"^[0-9a-f]{16,}$", re.I)
_NUMERIC_RE = re.compile(r"^\d+$")


def normalize_url(url: str) -> str:
    """把具体 URL 归一化为可匹配的 pattern。

    - 去掉查询串与 fragment
    - 纯数字段 → {id}
    - UUID 段 → {uuid}
    - 长 hex 段（>=16）→ {hash}
    - 末尾斜杠去除（根路径除外）
    例： /admin/users/123?tab=1 → /admin/users/{id}
    """
    if not url:
        return "/"
    # 去掉 scheme+host（若传入完整 URL）
    url = re.sub(r"^[a-z]+://[^/]+", "", url, flags=re.I)
    path = url.split("?", 1)[0].split("#", 1)[0]
    if not path.startswith("/"):
        path = "/" + path

    segments = [s for s in path.split("/") if s != ""]
    normalized: list[str] = []
    for seg in segments:
        if _NUMERIC_RE.match(seg):
            normalized.append("{id}")
        elif _UUID_RE.match(seg):
            normalized.append("{uuid}")
        elif _LONG_HEX_RE.match(seg):
            normalized.append("{hash}")
        else:
            normalized.append(seg)

    return "/" + "/".join(normalized) if normalized else "/"


def _canonical(obj) -> str:
    """把任意结构稳定序列化（键排序），用于 hash 计算的确定性。"""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def compute_region_hash(region: dict) -> str:
    """对单个区块按其 selector + elements 结构算稳定 hash（7.3.2 区块级 hash）。

    只取影响定位的结构字段（selector / elements 的 name/selector/type），
    忽略 region 自身的 name 等展示性字段，避免无关改动导致 hash 漂移。
    """
    elements = region.get("elements", []) or []
    normalized_elements = sorted(
        (
            {
                "name": e.get("name"),
                "selector": e.get("selector"),
                "type": e.get("type"),
            }
            for e in elements
        ),
        key=lambda e: (e["name"] or "", e["selector"] or ""),
    )
    payload = {
        "selector": region.get("selector"),
        "elements": normalized_elements,
    }
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def compute_region_hashes(regions: list[dict] | None) -> dict[str, str]:
    """对一组区块逐块算 hash，返回 {region_name: hash}（非整页）。"""
    result: dict[str, str] = {}
    for region in regions or []:
        name = region.get("name")
        if not name:
            continue
        result[name] = compute_region_hash(region)
    return result


async def upsert_from_execution(db, *, project_id: str, url: str, page_name: str,
                                regions: list[dict], base_url: str | None = None) -> bool:
    """执行时遇到页面 → 自动写入/刷新共享页面结构缓存（设计 7.3.1/7.3.6 自动补充）。
    按归一化 url_pattern 去重：无则新建、有则刷新结构与 hash。返回是否新建。
    """
    from datetime import datetime
    from sqlalchemy import select
    from app.models import PageStructureCache

    pattern = normalize_url(url)
    dom_hash = compute_region_hashes(regions)
    entry = (await db.execute(select(PageStructureCache).where(
        PageStructureCache.project_id == project_id,
        PageStructureCache.url_pattern == pattern,
    ))).scalars().first()
    if entry is None:
        db.add(PageStructureCache(
            project_id=project_id, base_url=base_url, url_pattern=pattern,
            page_name=page_name or pattern, dom_hash=dom_hash, regions=regions, status="active",
        ))
        return True
    # 不覆盖已有页面名(探索时人工命名的中文名优先)，仅在原本为空时补
    if not (entry.page_name or "").strip():
        entry.page_name = page_name or entry.page_name
    entry.dom_hash = dom_hash
    entry.regions = regions
    entry.status = "active"
    entry.updated_at = datetime.now()
    if base_url and not entry.base_url:
        entry.base_url = base_url
    return False


def match_cache(
    cached_hash: dict[str, str] | None,
    current_hash: dict[str, str],
) -> dict:
    """执行时缓存命中决策（7.3.3）。

    入参：
    - cached_hash: 缓存中按 URL pattern 命中的条目的 {region: hash}，无匹配传 None
    - current_hash: 本次执行实时计算出的 {region: hash}

    返回 result ∈ {full_hit, partial_hit, no_cache}，并给出命中 / 未命中区块。
    - no_cache：无 pattern 匹配 → 需完整探索
    - full_hit：所有当前区块都在缓存且 hash 一致 → 直接用缓存
    - partial_hit：部分区块 hash 不一致或缓存缺该区块 → 仅未命中区块需局部探索
    """
    if not cached_hash:
        return {
            "result": "no_cache",
            "hit_regions": [],
            "miss_regions": list(current_hash.keys()),
            "changed_regions": [],
        }

    hit_regions: list[str] = []
    miss_regions: list[str] = []
    changed_regions: list[str] = []

    for region, h in current_hash.items():
        cached = cached_hash.get(region)
        if cached is None:
            miss_regions.append(region)  # 缓存未记录该区块（新增区块）
        elif cached == h:
            hit_regions.append(region)
        else:
            miss_regions.append(region)
            changed_regions.append(region)  # hash 变化（区块改了）

    if not miss_regions:
        result = "full_hit"
    else:
        result = "partial_hit"

    return {
        "result": result,
        "hit_regions": hit_regions,
        "miss_regions": miss_regions,
        "changed_regions": changed_regions,
    }


def is_stale(last_hit_at: datetime | None, now: datetime | None = None) -> bool:
    """判断缓存是否超 STALE_AFTER_DAYS 天未命中（7.3.6 自动清理依据）。

    last_hit_at 为空表示从未命中，按 captured 起算由调用方传入合适的基准时间。
    """
    if last_hit_at is None:
        return False  # 命中时间缺失交由调用方处理，避免误删刚录入的缓存
    now = now or datetime.now(timezone.utc)
    # 兼容 naive datetime（SQLite 存储常为 naive UTC）
    if last_hit_at.tzinfo is None:
        last_hit_at = last_hit_at.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - last_hit_at) > timedelta(days=STALE_AFTER_DAYS)
