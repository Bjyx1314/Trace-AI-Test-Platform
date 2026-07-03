"""PC 执行环境定义（SIT / 开发 …）与「环境 → 枚举分类」映射。

地址存于枚举管理：默认环境 sit 存于历史分类 `base_url`(key=端名, label=URL，兼容既有数据)，
其它环境存于 `base_url_<env>`(如 base_url_dev)。这样每个(分类,端名)天然唯一，无需改表/改唯一约束。
执行器与枚举接口都从这里取口径，保证一致。"""
from __future__ import annotations

DEFAULT_ENV = "sit"

# 顺序即前端列/切换按钮顺序；扩展环境只需在此追加一项（对应分类 base_url_<key> 自动生效）。
ENVIRONMENTS: list[dict[str, str]] = [
    {"key": "sit", "label": "SIT"},
    {"key": "dev", "label": "dev"},
]

_ENV_KEYS = {e["key"] for e in ENVIRONMENTS}


def normalize_env(env: str | None) -> str:
    """归一化环境 key，未知/空一律回默认 sit。"""
    e = (env or "").strip().lower()
    return e if e in _ENV_KEYS else DEFAULT_ENV


def env_category(env: str | None) -> str:
    """环境 → 枚举分类。sit→base_url(兼容历史)，其它→base_url_<env>。"""
    e = normalize_env(env)
    return "base_url" if e == DEFAULT_ENV else f"base_url_{e}"


def env_label(env: str | None) -> str:
    e = normalize_env(env)
    return next((x["label"] for x in ENVIRONMENTS if x["key"] == e), e.upper())
