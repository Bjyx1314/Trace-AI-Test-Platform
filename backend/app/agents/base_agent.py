"""Base class shared by all AI-powered agents.

实际「调哪个大模型」收口在 app.agents.llm 的多 provider 层（anthropic / openai / claude_cli 订阅）。
本类只保留各 Agent 用到的三个方法签名 + mock 逻辑，非 mock 时委派给当前 provider，
所以各 Agent 无需改动，切换 provider/模型只改配置。

mock 策略：仅在 settings.mock_allowed(=allow_mock，需显式 ALLOW_MOCK=true)时生效；
否则(含本地仅 MOCK_MODE=true 的调试态)缺 Key 或 provider 调用失败一律抛错，绝不回退 mock。
"""
from __future__ import annotations
import logging
from typing import Optional

from app.config import settings
from app.agents.llm import get_provider, provider_needs_key, _resolved_key

logger = logging.getLogger(__name__)

class BaseAgent:
    # 上一次多模态调用中"有图但视觉识别失败"的图片数(0=正常)。调用方据此给结果加醒目标注。
    last_vision_failed_images: int = 0

    @property
    def use_mock(self) -> bool:
        """是否走 mock：仅本地(mock_allowed)且缺少可用 provider 时为真；服务器永不 mock。"""
        if not settings.mock_allowed:
            return False
        if provider_needs_key() and not _resolved_key():
            return True
        return False

    def _on_provider_error(self, e: Exception, where: str, mock_value):
        """provider 调用失败：本地回退 mock，服务器直接抛错(不产生 mock 数据)。"""
        if settings.mock_allowed:
            logger.warning("AI provider %s 调用失败，本地回退 mock：%s", where, e)
            return mock_value
        raise

    async def call_claude(self, system: str, user: str, max_tokens: int = 4096) -> str:
        if self.use_mock:
            return self._mock_response(user)
        try:
            return await get_provider().text(system, user, max_tokens)
        except Exception as e:
            return self._on_provider_error(e, "text", self._mock_response(user))

    def _mock_response(self, prompt: str) -> str:
        return f"[MOCK] Response for: {prompt[:80]}..."

    async def call_claude_tool(
        self,
        system: str,
        user: str,
        tool_name: str,
        tool_schema: dict,
        max_tokens: int = 4096,
        mock_result: Optional[dict] = None,
    ) -> dict:
        """强制结构化输出，返回结果 dict。本地 mock 模式或 provider 失败时返回 mock_result；服务器失败抛错。"""
        if self.use_mock:
            return mock_result if mock_result is not None else {}
        try:
            result = await get_provider().tool(system, user, tool_name, tool_schema, max_tokens)
            return result or (mock_result if mock_result is not None else {})
        except Exception as e:
            return self._on_provider_error(e, "tool", mock_result if mock_result is not None else {})

    async def call_claude_tool_multimodal(
        self,
        system: str,
        user: str,
        images: list,  # [(base64, media_type), ...]
        tool_name: str,
        tool_schema: dict,
        max_tokens: int = 8192,
        mock_result: Optional[dict] = None,
    ) -> dict:
        """文本 + 多张图片的多模态结构化输出。
        两步走(规避部分中转"视觉+强制工具"会卡死/超时)：
          1) 视觉→文字：让模型先客观描述图片内容(不带工具)；
          2) 把图片描述拼进 prompt，再做纯文本的强制结构化输出。
        无图片时直接走纯文本工具调用。"""
        self.last_vision_failed_images = 0
        if not images:
            return await self.call_claude_tool(system, user, tool_name, tool_schema, max_tokens, mock_result)
        if self.use_mock:
            return mock_result if mock_result is not None else {}

        desc = ""
        try:
            desc = await get_provider().text_multi(
                "你是图像理解助手。客观、详细地描述图片中与软件需求相关的信息："
                "界面/页面、字段名、表格列、按钮、状态、规则、示例数据，尽量完整列出文字。",
                "请描述以下需求相关图片的内容。",
                images, 1500,
            )
        except Exception as e:
            # 视觉识别失败：不阻断整体，降级为纯文本继续(非 mock，仅丢失图片信息)
            logger.warning("AI 视觉识别失败，降级为纯文本分析：%s", e)
        # 视觉抛错或返回空 → 视为失败：记录图片数，供调用方在结果上加醒目标注
        if not (desc or "").strip():
            self.last_vision_failed_images = len(images)
        logger.warning("多模态: 发送图片 %d 张, 视觉识别返回 %d 字, 视觉失败=%s",
                       len(images), len(desc or ""), bool(self.last_vision_failed_images))

        user2 = user + (f"\n\n【需求图片内容(AI 识别)】：\n{desc}" if desc else "")
        return await self.call_claude_tool(system, user2, tool_name, tool_schema, max_tokens, mock_result)

    async def call_claude_tool_vision(
        self,
        system: str,
        user_text: str,
        image_base64: str,
        image_media_type: str,
        tool_name: str,
        tool_schema: dict,
        max_tokens: int = 4096,
        mock_result: Optional[dict] = None,
    ) -> dict:
        """同 call_claude_tool，但附带一张图片（base64）用于视觉解析。"""
        if self.use_mock:
            return mock_result if mock_result is not None else {}
        try:
            result = await get_provider().tool_vision(
                system, user_text, image_base64, image_media_type, tool_name, tool_schema, max_tokens
            )
            return result or (mock_result if mock_result is not None else {})
        except Exception as e:
            return self._on_provider_error(e, "tool_vision", mock_result if mock_result is not None else {})
