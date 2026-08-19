"""通用键值配置(app_settings)。当前承载 SSO 对接认证地址。仅管理员可改。"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import AppSetting

# SSO 对接认证地址(external task system 地址)的配置键。
SSO_URL_KEY = "external_task_sso_url"
# 缺省值:对接 external task system 的默认地址。管理员可在后台改。
DEFAULT_EXTERNAL_TASK_URL = "http://sso.example.test"

# AI 模型配置键(后台可改，覆盖 .env)。
AI_PROVIDER_KEY = "ai_provider"
AI_MODEL_KEY = "ai_model"
AI_BASE_URL_KEY = "ai_base_url"
AI_API_KEY_KEY = "ai_api_key"

# Guardian 集成配置键(后台可改，覆盖 .env)。
GUARDIAN_ENABLED_KEY = "guardian_enabled"
GUARDIAN_BASE_URL_KEY = "guardian_base_url"
GUARDIAN_PAT_KEY = "guardian_pat"


async def resolve_guardian_config(db: AsyncSession) -> dict:
    """Guardian 集成生效配置。优先级：后台配置(app_settings) > .env(config)。
    返回 {enabled, base_url, pat, product, timeout, max_files, pat_masked}(pat 不回明文)。
    """
    enabled_raw = await get_setting(db, GUARDIAN_ENABLED_KEY)
    enabled = (enabled_raw == "1") if enabled_raw is not None else bool(settings.guardian_enabled)
    base_url = (await get_setting(db, GUARDIAN_BASE_URL_KEY)) or settings.guardian_base_url or ""
    pat = (await get_setting(db, GUARDIAN_PAT_KEY)) or settings.guardian_pat or ""
    return {
        "enabled": bool(enabled and base_url and pat),
        "base_url": base_url.rstrip("/"),
        "pat": pat,
        "pat_masked": _mask(pat),
        "product": settings.guardian_product,
        "timeout": settings.guardian_timeout_sec,
        "max_files": settings.guardian_max_impact_files,
    }


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


async def resolve_external_task_url(db: AsyncSession) -> str:
    """SSO 对接的 external task system 地址。
    优先级:后台配置(app_settings) > 环境变量 EXTERNAL_TASK_API_URL(config) > 默认 sso.example.test。
    """
    configured = await get_setting(db, SSO_URL_KEY)
    if configured:
        return configured.rstrip("/")
    # config.external_task_api_url 默认即 sso.example.test;dev 可用 env 覆盖为 localhost:3000。
    return (settings.external_task_api_url or DEFAULT_EXTERNAL_TASK_URL).rstrip("/")
