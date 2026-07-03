"""通用键值配置(app_settings)。当前承载 SSO 对接认证地址。仅管理员可改。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting

# SSO 对接认证地址的配置键。
SSO_URL_KEY = "external_sso_url"
# 开源发行版不绑定任何组织的 SSO。管理员可在后台或环境变量中配置。
DEFAULT_EXTERNAL_SSO_URL = ""

# AI 模型配置键(后台可改，覆盖 .env)。
AI_PROVIDER_KEY = "ai_provider"
AI_MODEL_KEY = "ai_model"
AI_BASE_URL_KEY = "ai_base_url"
AI_API_KEY_KEY = "ai_api_key"


async def get_setting(db: AsyncSession, key: str) -> str | None:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    return row.value if row and row.value else None


async def set_setting(db: AsyncSession, key: str, value: str, operator: str | None) -> str:
    row = (await db.execute(select(AppSetting).where(AppSetting.key == key))).scalar_one_or_none()
    if row is None:
        row = AppSetting(key=key, value=value, updated_by=operator)
        db.add(row)
    else:
        row.value = value
        row.updated_by = operator
    await db.commit()
    return value


async def apply_ai_settings_to_runtime(db: AsyncSession) -> dict:
    """把后台配置的 AI 模型设置覆写到内存 settings(get_provider 读 settings.ai_*，配置变更会自动重建)。
    后台留空的项保持 .env 原值。返回当前生效值(api_key 脱敏)。
    """
    for key, attr in (
        (AI_PROVIDER_KEY, "ai_provider"),
        (AI_MODEL_KEY, "ai_model"),
        (AI_BASE_URL_KEY, "ai_base_url"),
        (AI_API_KEY_KEY, "ai_api_key"),
    ):
        val = await get_setting(db, key)
        if val:
            setattr(settings, attr, val)
    return {
        "provider": settings.ai_provider or "anthropic",
        "model": settings.ai_model or "",
        "base_url": settings.ai_base_url or "",
        "api_key_set": bool(settings.ai_api_key or settings.anthropic_api_key),
        "api_key_masked": _mask(settings.ai_api_key or settings.anthropic_api_key),
    }


def _mask(key: str | None) -> str:
    if not key:
        return ""
    return (key[:5] + "***" + key[-4:]) if len(key) > 10 else "***"


async def resolve_external_sso_url(db: AsyncSession) -> str:
    """解析外部 SSO 地址。
    优先级:后台配置(app_settings) > 环境变量 EXTERNAL_TASK_API_URL(config)。
    """
    configured = await get_setting(db, SSO_URL_KEY)
    if configured:
        return configured.rstrip("/")
    return (settings.external_task_api_url or DEFAULT_EXTERNAL_SSO_URL).rstrip("/")
