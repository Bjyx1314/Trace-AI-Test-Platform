"""Dispatcher —— 按用例的 (case_type, platforms) 路由到对应 Runner 类型（详设第 2 章）。

路由只产出 runner 类型字符串；具体实例化与「环境未就绪回退 Mock」由上层（队列/worker）
根据配置开关决定，保持本函数为纯函数、易测。
"""
from __future__ import annotations

from typing import Any

# 兼容通用/历史平台标识；业务自定义端优先走枚举 platform.parent_key 注入。
_GENERIC_PLATFORM_GROUP = {
    "api": "api",
    "backend_api": "api",
    "接口": "api",
    # 兼容旧执行口径标识
    "web": "pc",
    "android": "app",
    "ios": "app",
    "harmony": "app",
    "miniprogram": "miniprogram",
}


def resolve_runner_type(case: Any, platform_group_map: dict[str, str] | None = None) -> str:
    """返回 api/web/android/miniprogram 之一。

    口径与前端 categorizeCaseByPlatform 一致：
    1) case_type == "api" 或含接口端 → "api"
    2) 含 App 端 → "android"(真机直连执行)
    3) 含小程序端 → "miniprogram"
    4) 默认 "web"(PC)
    """
    case_type = getattr(case, "case_type", None)
    platforms = getattr(case, "platforms", None) or []
    group_map = dict(_GENERIC_PLATFORM_GROUP)
    if platform_group_map:
        group_map.update(platform_group_map)
    groups = [group_map.get(p) for p in platforms if group_map.get(p)]

    if case_type == "api" or "api" in groups or "backend_api" in platforms:
        return "api"
    if "app" in groups:
        return "android"
    if "miniprogram" in groups:
        return "miniprogram"
    return "web"
