"""系统设置路由（仅管理员）：自动化用例生成开关（按端）。

控制"执行测试通过后是否生成自动化用例"——按不同端(接口/Web/App/鸿蒙/小程序)
分别开关。读取与修改均要求管理员权限。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.dependencies import require_admin
from app.services import automation_switch
from app.services.app_settings import (
    SSO_URL_KEY, DEFAULT_EXTERNAL_SSO_URL, get_setting, set_setting, resolve_external_sso_url,
    AI_PROVIDER_KEY, AI_MODEL_KEY, AI_BASE_URL_KEY, AI_API_KEY_KEY, apply_ai_settings_to_runtime,
)

router = APIRouter(prefix="/api/system", tags=["system"])

# 支持的 AI provider(与 llm.py 一致)
AI_PROVIDERS = [
    {"value": "anthropic", "label": "Anthropic 官方 API"},
    {"value": "openai", "label": "OpenAI / 兼容协议中转(chat/completions)"},
    {"value": "openai_responses", "label": "OpenAI Responses API 中转"},
    {"value": "claude_cli", "label": "Claude CLI(订阅，无需 Key)"},
    {"value": "azure", "label": "Azure OpenAI"},
]


class SwitchUpdate(BaseModel):
    platform: str   # api / web / app / harmony / miniprogram
    enabled: bool


class SsoConfigUpdate(BaseModel):
    external_sso_url: str


class AiConfigUpdate(BaseModel):
    provider: str
    model: str
    base_url: str = ""
    api_key: str | None = None  # 留空=保持原值，不覆盖


@router.get("/automation-switches")
async def get_automation_switches(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    """列出各端自动化用例生成开关（管理员专用）。"""
    return await automation_switch.list_switches(db)


@router.put("/automation-switches")
async def update_automation_switch(
    body: SwitchUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: dict = Depends(require_admin),
):
    """设置某端自动化用例生成开关（管理员专用）。"""
    if body.platform not in automation_switch.VALID_PLATFORMS:
        raise HTTPException(
            400,
            f"端只能是 {', '.join(automation_switch.VALID_PLATFORMS)} 之一",
        )
    operator = current_admin.get("name") or current_admin.get("email") or current_admin.get("sub")
    return await automation_switch.set_switch(db, body.platform, body.enabled, operator)


@router.get("/ai-config")
async def get_ai_config(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    """读取 AI 模型配置(管理员专用)。api_key 仅返回脱敏，不回传明文。"""
    current = await apply_ai_settings_to_runtime(db)
    return {**current, "providers": AI_PROVIDERS}


@router.put("/ai-config")
async def update_ai_config(
    body: AiConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: dict = Depends(require_admin),
):
    """设置 AI 模型配置(管理员专用)。模型必填；api_key 留空=保持原值。"""
    valid = {p["value"] for p in AI_PROVIDERS}
    if body.provider not in valid:
        raise HTTPException(400, f"provider 只能是 {', '.join(valid)} 之一")
    model = body.model.strip()
    if not model:
        raise HTTPException(400, "AI 模型不能为空，平台不提供默认模型")
    base = (body.base_url or "").strip().rstrip("/")
    if base and not (base.startswith("http://") or base.startswith("https://")):
        raise HTTPException(400, "base_url 须以 http:// 或 https:// 开头")
    operator = current_admin.get("name") or current_admin.get("email") or current_admin.get("sub")
    await set_setting(db, AI_PROVIDER_KEY, body.provider, operator)
    await set_setting(db, AI_MODEL_KEY, model, operator)
    await set_setting(db, AI_BASE_URL_KEY, base, operator)
    # api_key 仅在传了非空值时更新，避免脱敏回显被当成新值覆盖
    if body.api_key:
        await set_setting(db, AI_API_KEY_KEY, body.api_key.strip(), operator)
    current = await apply_ai_settings_to_runtime(db)
    return {**current, "providers": AI_PROVIDERS}


@router.get("/sso-config")
async def get_sso_config(
    db: AsyncSession = Depends(get_db),
    _: dict = Depends(require_admin),
):
    """读取外部 SSO 地址。management = 后台配置原值;resolved = 实际生效值。"""
    return {
        "external_sso_url": await get_setting(db, SSO_URL_KEY) or "",
        "resolved": await resolve_external_sso_url(db),
        "default": DEFAULT_EXTERNAL_SSO_URL,
    }


@router.put("/sso-config")
async def update_sso_config(
    body: SsoConfigUpdate,
    db: AsyncSession = Depends(get_db),
    current_admin: dict = Depends(require_admin),
):
    """设置 SSO 对接认证地址(管理员专用)。留空=回落默认/环境变量;非空须为 http(s):// 开头。"""
    url = (body.external_sso_url or "").strip().rstrip("/")
    if url and not (url.startswith("http://") or url.startswith("https://")):
        raise HTTPException(400, "地址须以 http:// 或 https:// 开头")
    operator = current_admin.get("name") or current_admin.get("email") or current_admin.get("sub")
    await set_setting(db, SSO_URL_KEY, url, operator)
    return {
        "external_sso_url": url,
        "resolved": await resolve_external_sso_url(db),
        "default": DEFAULT_EXTERNAL_SSO_URL,
    }
