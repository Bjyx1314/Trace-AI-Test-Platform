"""覆盖项归并（方案 7.4，阶段四）——统一 coverage.py(name 键) 与 pipeline(struct_key) 两套口径。

归并策略：
1. 结构键粗筛：object+action 相同进入候选组；
2. 向量精筛：组内 embedding 相似度 ≥0.90 判定语义等价；
3. 边界仲裁：0.80–0.90 区间可交 LLM 判定（此处默认按 0.85 阈值合并，LLM 仲裁可选开启）；
4. 结果可追溯：保留全部 sources 并集 + merged_from 原始项；优先级取各来源最高。

无 embedding 时退化为纯 struct_key 粗筛 + 名称相似度（复用 pipeline._title_similarity 思路）。
"""
from __future__ import annotations

from app.services import embedding as emb

_EQUIV = 0.90     # 语义等价阈值
_ARBITRATE = 0.80  # 边界下限

_PRIO_RANK = {"P0": 0, "P1": 1, "P2": 2}


def _struct_key(it: dict) -> str:
    o = "".join(str(it.get("object") or "").lower().split())
    a = "".join(str(it.get("action") or "").lower().split())
    return f"{o}|{a}" if (o or a) else "".join(str(it.get("name") or "").lower().split())


def _name_sim(a: str, b: str) -> float:
    def bg(s: str) -> set[str]:
        s = "".join((s or "").lower().split())
        return {s[i:i + 2] for i in range(len(s) - 1)} if len(s) >= 2 else ({s} if s else set())
    x, y = bg(a), bg(b)
    return len(x & y) / len(x | y) if (x and y) else 0.0


def _higher_priority(a: str | None, b: str | None) -> str | None:
    if a is None:
        return b
    if b is None:
        return a
    return a if _PRIO_RANK.get(a, 9) <= _PRIO_RANK.get(b, 9) else b


def _merge_two(base: dict, other: dict) -> dict:
    base["sources"] = list(dict.fromkeys((base.get("sources") or []) + (other.get("sources") or [])))
    base["risk_tags"] = list(dict.fromkeys((base.get("risk_tags") or []) + (other.get("risk_tags") or [])))
    base["matched_rules"] = list(dict.fromkeys((base.get("matched_rules") or []) + (other.get("matched_rules") or [])))
    base["priority"] = _higher_priority(base.get("priority"), other.get("priority"))
    mf = list(base.get("merged_from") or [])
    if other.get("item_id"):
        mf.append(other["item_id"])
    base["merged_from"] = mf
    if not base.get("expected") and other.get("expected"):
        base["expected"] = other["expected"]
    return base


async def merge_items(items: list[dict], *, use_embedding: bool = True) -> list[dict]:
    """归并一组覆盖项（同来源或跨来源）。返回归并后列表（保留 merged_from/sources 并集）。"""
    items = [dict(i) for i in items if i]
    if len(items) <= 1:
        return items

    vecs: list | None = None
    if use_embedding and emb.embedding_available():
        vecs = await emb.embed_texts([_embed_input(i) for i in items])

    result: list[dict] = []
    result_vecs: list = []
    consumed: set[int] = set()
    for i, it in enumerate(items):
        if i in consumed:
            continue
        base = it
        base_vec = vecs[i] if vecs else None
        for j in range(i + 1, len(items)):
            if j in consumed:
                continue
            other = items[j]
            equiv = False
            if base_vec is not None and vecs and vecs[j] is not None:
                sim = emb.cosine(base_vec, vecs[j])
                equiv = sim >= _ARBITRATE if _struct_key(base) == _struct_key(other) else sim >= _EQUIV
            else:
                equiv = _struct_key(base) == _struct_key(other) or _name_sim(base.get("name", ""), other.get("name", "")) >= 0.85
            if equiv:
                base = _merge_two(base, other)
                consumed.add(j)
        result.append(base)
        result_vecs.append(base_vec)
    return result


def _embed_input(item: dict) -> str:
    return " ".join(str(x) for x in [item.get("name"), item.get("object"), item.get("action"), item.get("expected")] if x)
