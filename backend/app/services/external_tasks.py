"""external task system 对外 REST API 客户端（API Key / Bearer 鉴权，服务端调用）。

对接其《External Task System 对外 API》规格：需求批量同步、缺陷建单/附件/状态回写、项目列表。
登录鉴权(模式B SSO)不在此实现。失败抛 ExternalTaskError(中文)。
"""
from __future__ import annotations
from typing import Optional

import httpx

from app.config import settings


class ExternalTaskError(Exception):
    """external task system 接口调用失败，message 可直接展示给用户。"""
    def __init__(self, message: str, code: int | None = None):
        super().__init__(message)
        self.code = code


# 缺陷等级：平台 4 级中文 → external task system 3 级英文
_SEVERITY_MAP = {
    "1级-致命": "blocker",
    "2级-严重": "major",
    "3级-一般": "minor",
    "4级-轻微": "minor",  # external task system 无第 4 级，降级并入 minor
}


def map_severity(platform_severity: str | None) -> str:
    return _SEVERITY_MAP.get(platform_severity or "", "major")


def is_configured() -> bool:
    return bool(settings.external_task_api_key)


def _base() -> str:
    return settings.external_task_api_url.rstrip("/")


def _headers(extra: dict | None = None) -> dict:
    h = {"Authorization": f"Bearer {settings.external_task_api_key}"}
    if extra:
        h.update(extra)
    return h


def _zh_http_error(status: int, body: str) -> str:
    if status == 401:
        return "external task system 鉴权失败：API Key 缺失/无效/已撤销/已过期"
    if status == 403:
        return "external task system 权限不足：API Key 缺少该操作的 scope，或账号无该项目权限"
    if status == 400:
        return f"external task system 请求被拒绝：{body[:200]}"
    if status == 404:
        return "external task system 资源不存在"
    return f"external task system 接口错误（HTTP {status}）：{body[:200]}"


async def _request(method: str, path: str, *, json=None, params=None, files=None, data=None) -> dict | list:
    if not is_configured():
        raise ExternalTaskError("未配置 external task system API Key（EXTERNAL_TASK_API_KEY）")
    url = f"{_base()}{path}"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.request(
                method, url, headers=_headers(), json=json, params=params, files=files, data=data
            )
    except httpx.HTTPError as e:
        raise ExternalTaskError(f"无法连接 external task system：{e}")
    if resp.status_code >= 400:
        raise ExternalTaskError(_zh_http_error(resp.status_code, resp.text), resp.status_code)
    try:
        return resp.json()
    except Exception:
        return {}


# ── 需求批量同步 ───────────────────────────────────────────────────────────────

async def fetch_requirements(project_id: str | None = None) -> list[dict]:
    """GET /api/requirements：拉取该账号可见项目的需求与缺陷（type 区分）。"""
    params = {"project_id": project_id} if project_id else None
    data = await _request("GET", "/api/requirements", params=params)
    return data if isinstance(data, list) else []


# ── 项目列表 ───────────────────────────────────────────────────────────────────

async def list_projects() -> list[dict]:
    """GET /api/projects：该账号可见项目，用于选择同步源。"""
    data = await _request("GET", "/api/projects")
    return data if isinstance(data, list) else []


# ── 缺陷建单 / 附件 / 状态回写 ─────────────────────────────────────────────────

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
    """POST /api/requirements (type='bug')：在 external task system 建缺陷单，返回缺陷对象(含 id)。"""
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
    data = await _request("POST", "/api/requirements", json=payload)
    return data if isinstance(data, dict) else {}


def bug_url(bug_id: str) -> str:
    """缺陷单据可访问 URL（按规格约定 {base}/requirements/{id}）。"""
    return f"{_base()}/requirements/{bug_id}"


async def upload_bug_attachments(bug_id: str, files: list[tuple[str, bytes, str]]) -> list[dict]:
    """POST /api/requirements/{id}/attachments：上传截图/日志。files=[(filename, content, mime)]。"""
    multipart = [("files", (name, content, mime)) for name, content, mime in files]
    data = await _request("POST", f"/api/requirements/{bug_id}/attachments", files=multipart)
    return data if isinstance(data, list) else []


async def update_bug_status(bug_id: str, status: str, transition_note: str | None = None) -> dict:
    """PATCH /api/requirements/{id}：回写缺陷工作流状态(accepted/archived/rejected 等)。"""
    payload: dict = {"status": status}
    if transition_note:
        payload["transition_note"] = transition_note
    data = await _request("PATCH", f"/api/requirements/{bug_id}", json=payload)
    return data if isinstance(data, dict) else {}
