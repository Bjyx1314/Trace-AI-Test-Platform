"""Guardian envoy MCP 客户端(HTTP)：查代码「合入态图谱」的确定性影响半径。

用途：代码变更影响分析时，把「改动文件在 master 上有谁依赖」这类**确定性事实**补进 LLM 上下文，
替代 LLM 靠 diff 猜调用方。Guardian 只读、图谱按 master 更新——提测分支的新代码不进图，
这里查的是「改动的存量文件在合入态有哪些下游」。

**降级安全铁律**：总开关关 / 未配置 / 不可达 / 未接入(空图) / 超时 —— 一律返回 None，绝不抛，
不改变影响分析的现有行为。envoy 是 MCP Streamable HTTP：JSON-RPC over POST，响应为 SSE 文本。
"""
from __future__ import annotations

import json
import logging

import httpx

logger = logging.getLogger(__name__)


async def _config() -> dict | None:
    """读生效配置(后台 > env)。未启用返回 None。自开会话，调用方无需持有 db。"""
    try:
        from app.database import AsyncSessionLocal
        from app.services.app_settings import resolve_guardian_config
        async with AsyncSessionLocal() as db:
            cfg = await resolve_guardian_config(db)
        return cfg if cfg.get("enabled") else None
    except Exception as e:  # noqa: BLE001
        logger.info("Guardian 配置解析失败(降级跳过)：%s", e)
        return None


def _parse_sse(text: str) -> dict | None:
    """MCP Streamable HTTP 响应是 SSE：取 `data:` 行的 JSON；也兼容纯 JSON。"""
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("data:"):
            try:
                return json.loads(s[5:].strip())
            except Exception:  # noqa: BLE001
                continue
    try:
        return json.loads(text)
    except Exception:  # noqa: BLE001
        return None


async def _call_tool(cfg: dict, name: str, args: dict) -> dict | None:
    """调一个 MCP 工具，返回其结构化结果 dict；任何失败/错误 → None。"""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
               "params": {"name": name, "arguments": args}}
    headers = {
        "authorization": f"Bearer {cfg['pat']}",
        "content-type": "application/json",
        "accept": "application/json, text/event-stream",
    }
    try:
        async with httpx.AsyncClient(timeout=cfg["timeout"]) as c:
            r = await c.post(cfg["base_url"], json=payload, headers=headers)
    except Exception as e:  # noqa: BLE001 不可达
        logger.info("Guardian 调用 %s 失败(降级)：%s", name, e)
        return None
    if r.status_code != 200:
        logger.info("Guardian 工具 %s HTTP %s(降级)", name, r.status_code)
        return None
    msg = _parse_sse(r.text)
    if not msg or msg.get("error"):
        logger.info("Guardian 工具 %s 返回错误/空(多为未接入)：%s", name, (msg or {}).get("error"))
        return None
    result = msg.get("result") or {}
    # MCP 工具正文在 content[].text（impactHandler 输出 JSON 字符串）
    for item in result.get("content") or []:
        if item.get("type") == "text":
            try:
                return json.loads(item["text"])
            except Exception:  # noqa: BLE001
                return {"text": item["text"]}
    return result.get("structuredContent") or None


def _path_variants(path: str) -> list[str]:
    """图谱路径口径可能带/不带 './' 前缀(取决于 ingest 的 projectDir)。平台传的是 git diff 路径
    (无 './')，两种都试，命中即用。也兼容去掉仓库子目录前缀的情况可后续扩展。"""
    p = (path or "").strip().lstrip("/")
    out = [p]
    if p.startswith("./"):
        out.append(p[2:])
    else:
        out.append("./" + p)
    return out


async def _impact_one(cfg: dict, path: str) -> dict | None:
    """单文件影响：按路径口径试多种变体，返回首个有下游的结果(全空则返回最后一次非 None)。"""
    last: dict | None = None
    for v in _path_variants(path):
        res = await _call_tool(cfg, "guardian_impact", {"path": v})
        if res is not None:
            last = res
            if res.get("downstream"):
                return res
    return last


def _clean_dependents(downstream: list[dict], self_path: str) -> list[dict]:
    """从 downstream 里抽出「文件级依赖者」(去掉符号节点/自身)，给出干净可读的路径列表。
    符号节点形如 'scip-typescript npm ... `x.ts`/sym'；文件节点形如 './src/apis/index.ts'。"""
    self_norm = self_path.strip().lstrip("./").lstrip("/")
    out: list[dict] = []
    seen: set[str] = set()
    for d in downstream:
        node = str(d.get("node") or "")
        if node.startswith("scip-typescript") or node.startswith("http") or not node:
            continue  # 符号节点 / component 等，非文件
        p = node.lstrip("./").lstrip("/")
        if p.endswith(".vue.ts"):
            p = p[:-3]  # .vue 页面的抽取 shim 还原成真实文件名(deviceList/x.vue)
        if not p or p == self_norm or p in seen:
            continue
        seen.add(p)
        out.append({"file": p, "depth": d.get("depth"), "confidence": d.get("confidence")})
    return out


async def impact_of(paths: list[str]) -> dict | None:
    """逐改动文件查影响半径并聚合。返回
        {items:[{path, downstream:[{node,depth,confidence}], hotness, gaps}], queried, total}
    或 None（未启用/全部不可用——由调用方走"LLM 推断"降级）。"""
    if not paths:
        return None
    cfg = await _config()
    if not cfg:
        return None
    items: list[dict] = []
    for p in paths[: cfg["max_files"]]:
        res = await _impact_one(cfg, p)
        if res is None:
            continue
        ds = res.get("downstream") or []
        items.append({
            "path": p,
            "dependents": _clean_dependents(ds, p),   # 文件级依赖者(干净可读)
            "downstream_count": len(ds),               # 原始下游规模(含符号)
            "hotness": res.get("hotness"),
            "gaps": res.get("gaps") or [],
        })
    if not items:  # 全查空/失败：多半是该 product 未接入传感器 → 交调用方降级
        return None
    return {"items": items, "queried": min(len(paths), cfg["max_files"]), "total": len(paths)}


async def ping() -> dict:
    """联调用：探测 Guardian 连通性。返回 {ok, detail}。"""
    cfg = await _config()
    if not cfg:
        return {"ok": False, "detail": "未启用或未配置(base_url/PAT/开关)"}
    res = await _call_tool(cfg, "guardian_ping", {})
    if res is None:
        return {"ok": False, "detail": f"连接失败或鉴权失败：{cfg['base_url']}"}
    return {"ok": True, "detail": res}
