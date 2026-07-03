"""通用外部任务系统 REST 客户端。

支持需求批量同步、项目列表、缺陷建单、附件上传和状态回写。外部系统只需
提供本文使用的 REST 契约；登录鉴权由独立的 SSO 换票流程处理。
"""
from __future__ import annotations
from typing import Optional

import httpx

from app.config import settings


class ExternalTaskError(Exception):
    """外部任务系统调用失败，message 可直接展示给用户。"""

    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


_SEVERITY_MAP = {
    "1级-致命": "blocker",
    "2级-严重": "major",
    "3级-一般": "minor",
    "4级-轻微": "minor",
}


def map_severity(platform_severity: str | None) -> str:
    return _SEVERITY_MAP.get(platform_severity or "", "major")


def is_configured() -> bool:
    return bool(settings.external_task_api_url and settings.external_task_api_key)


def _base() -> str:
    if not settings.external_task_api_url:
        raise ExternalTaskError("未配置外部任务系统地址（EXTERNAL_TASK_API_URL）")
    return settings.external_task_api_url.rstrip("/")


def _headers(extra: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {settings.external_task_api_key}"}
    if extra:
        headers.update(extra)
    return headers


def _http_error(status: int, body: str) -> str:
    if status == 401:
        return "外部任务系统鉴权失败：API Key 缺失、无效、已撤销或已过期"
    if status == 403:
        return "外部任务系统权限不足：API Key 缺少操作权限，或账号无项目权限"
    if status == 400:
        return f"外部任务系统拒绝请求：{body[:200]}"
    if status == 404:
        return "外部任务系统资源不存在"
    return f"外部任务系统接口错误（HTTP {status}）：{body[:200]}"


async def _request(method: str, path: str, *, json=None, params=None, files=None, data=None) -> dict | list:
    if not is_configured():
        raise ExternalTaskError("未配置外部任务系统 API Key（EXTERNAL_TASK_API_KEY）")
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.request(
                method,
                f"{_base()}{path}",
                headers=_headers(),
                json=json,
                params=params,
                files=files,
                data=data,
            )
    except httpx.HTTPError as exc:
        raise ExternalTaskError(f"无法连接外部任务系统：{exc}") from exc
    if response.status_code >= 400:
        raise ExternalTaskError(_http_error(response.status_code, response.text), response.status_code)
    try:
        return response.json()
    except Exception:
        return {}


async def fetch_requirements(project_id: str | None = None) -> list[dict]:
    """拉取当前凭据可见的需求。"""
    params = {"project_id": project_id} if project_id else None
    result = await _request("GET", "/api/requirements", params=params)
    return result if isinstance(result, list) else []


async def list_projects() -> list[dict]:
    """列出当前凭据可见的项目。"""
    result = await _request("GET", "/api/projects")
    return result if isinstance(result, list) else []


async def create_bug(
    *,
    title: str,
    description: str = "",
    severity: str | None = None,
    project_ids: Optional[list[str]] = None,
    related_requirement_id: str | None = None,
    reproduce_steps: str | None = None,
    found_stage: str | None = None,
    product_line_id: str | None = None,
) -> dict:
    """在外部任务系统创建缺陷单。"""
    payload: dict = {"type": "bug", "title": title, "severity": map_severity(severity)}
    if description:
        payload["description"] = description
    if project_ids:
        payload["project_ids"] = project_ids
    if related_requirement_id:
        payload["related_requirement_id"] = related_requirement_id
    if reproduce_steps:
        payload["reproduce_steps"] = reproduce_steps
    if found_stage:
        payload["found_stage"] = found_stage
    if product_line_id:
        payload["product_line_id"] = product_line_id
    result = await _request("POST", "/api/requirements", json=payload)
    return result if isinstance(result, dict) else {}


def bug_url(bug_id: str) -> str:
    return f"{_base()}/requirements/{bug_id}"


async def upload_bug_attachments(bug_id: str, files: list[tuple[str, bytes, str]]) -> list[dict]:
    multipart = [("files", (name, content, mime)) for name, content, mime in files]
    result = await _request("POST", f"/api/requirements/{bug_id}/attachments", files=multipart)
    return result if isinstance(result, list) else []


async def update_bug_status(bug_id: str, status: str, transition_note: str | None = None) -> dict:
    payload: dict = {"status": status}
    if transition_note:
        payload["transition_note"] = transition_note
    result = await _request("PATCH", f"/api/requirements/{bug_id}", json=payload)
    return result if isinstance(result, dict) else {}
