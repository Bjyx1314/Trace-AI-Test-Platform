"""稳定节点 ID 生成（方案 9.3）。标识体系是图谱与经验/覆盖项引用的地基。

Page 复用 page_cache_service.normalize_url() 的归一化结果，保证缓存/执行/图谱三方页面标识天然一致。
"""
from __future__ import annotations

from app.services.page_cache_service import normalize_url


def page_id(project: str, url: str) -> str:
    return f"page:{project}:{normalize_url(url)}"


def api_id(method: str, path: str) -> str:
    return f"api:{(method or 'GET').upper()}:{normalize_url_path(path)}"


def normalize_url_path(path: str) -> str:
    # 接口 path 也做动态段归一（复用 normalize_url，但接口传的是纯 path）
    return normalize_url(path) if path.startswith("/") else path


def service_id(repo: str, class_name: str) -> str:
    return f"svc:{repo}:{class_name}"


def component_id(repo: str, name: str) -> str:
    return f"comp:{repo}:{name}"


def file_id(repo: str, path: str) -> str:
    return f"file:{repo}:{path}"


def db_id(schema_table: str) -> str:
    return f"db:{schema_table}"


def mq_id(topic: str) -> str:
    return f"mq:{topic}"
