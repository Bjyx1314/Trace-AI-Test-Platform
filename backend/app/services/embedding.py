"""向量嵌入通道（经验召回 / 覆盖项归并的语义通道）。方案 6.2.3 / 7.4。

独立于 chat provider：anthropic / claude_cli 无 embeddings API，统一走 OpenAI 兼容端点。
缺 key / 关闭 / 调用失败一律返回 None —— 调用方回退标签精确 + 结构键匹配，绝不阻塞主流程。

用 pgvector 存储；相似度既可在 DB 侧(cosine 距离)也可在内存(cosine 函数)算。
"""
from __future__ import annotations
import logging
import math
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


def _resolved_embed_key() -> Optional[str]:
    return settings.embed_api_key or settings.ai_api_key or settings.anthropic_api_key


def _resolved_embed_base() -> str:
    base = settings.embed_base_url or settings.ai_base_url or "https://api.openai.com/v1"
    return base.rstrip("/")


def embedding_available() -> bool:
    return bool(settings.embed_enabled and _resolved_embed_key())


async def embed_texts(texts: list[str]) -> Optional[list[list[float]]]:
    """批量嵌入。缺 key / 关闭 / 失败 → None（调用方降级）。"""
    if not settings.embed_enabled:
        return None
    key = _resolved_embed_key()
    if not key:
        return None
    clean = [(t or "").strip()[:8000] for t in texts]
    if not any(clean):
        return None
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{_resolved_embed_base()}/embeddings",
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                json={"model": settings.embed_model, "input": clean},
            )
            resp.raise_for_status()
            data = resp.json()
        # OpenAI 返回按 index 排序的 data 数组
        items = sorted(data.get("data", []), key=lambda d: d.get("index", 0))
        return [it["embedding"] for it in items]
    except Exception as e:  # noqa: BLE001 嵌入失败不阻塞
        logger.warning("embedding 调用失败，降级为无向量：%s", e)
        return None


async def embed_text(text: str) -> Optional[list[float]]:
    res = await embed_texts([text])
    return res[0] if res else None


def cosine(a: Optional[list[float]], b: Optional[list[float]]) -> float:
    """内存 cosine 相似度；任一为空返回 0。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
