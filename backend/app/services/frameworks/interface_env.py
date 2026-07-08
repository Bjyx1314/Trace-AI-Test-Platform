"""接口框架 env_config 解析 —— 接口执行按 service 解析环境域名。"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

_ROOT = Path(os.environ.get("INTERFACE_FRAMEWORK_ROOT") or "/opt/framework-inter")
DEFAULT_ACCOUNT = "zxl"


def _load(env: str) -> dict:
    path = _ROOT / "config" / env / "env_config.yaml"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def host_map(env: str = "sit") -> dict[str, str]:
    return (_load(env).get("host") or {})


def resolve_service_base_url(service: str | None, env: str = "sit") -> str | None:
    """service 命中框架 host 配置则解析域名；http(s) 字面量直接透传。"""
    if not service:
        return None
    if service.startswith("http://") or service.startswith("https://"):
        return service.rstrip("/")
    url = host_map(env).get(service)
    return url.rstrip("/") if url else None


def login_url(login_key: str, env: str = "sit") -> str | None:
    return (_load(env).get("LoginUrl") or {}).get(login_key)


def account(env: str = "sit", name: str = DEFAULT_ACCOUNT) -> dict:
    return (_load(env).get(name) or {})
