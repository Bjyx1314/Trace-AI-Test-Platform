"""Agent: 从需求图片中提取标题与正文内容，供需求上传流程使用。"""
from __future__ import annotations

import base64

from .base_agent import BaseAgent

SYSTEM = """你是一名需求文档整理专家。给定一张需求相关的图片（如需求评审截图、原型图标注、聊天记录截图等），
请提取/整理出一份结构化的需求文档：
- title: 需求标题（简明扼要，适合作为列表标题）
- content: 需求正文内容（用Markdown整理图片中的关键信息：背景、功能点、交互细节、边界条件等，尽量保留原始信息，不要遗漏）
调用工具输出结果。"""

EXTRACT_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "content": {"type": "string"},
    },
    "required": ["title", "content"],
    "description": "从图片中提取的需求标题与正文内容",
}


class RequirementExtractorAgent(BaseAgent):
    async def extract_from_image(self, image_bytes: bytes, media_type: str, filename: str) -> dict:
        mock_result = {
            "title": f"[图片需求] {filename}",
            "content": (
                f"（MOCK模式：未调用AI视觉解析，以下为占位内容）\n\n"
                f"原始图片文件名: {filename}\n\n"
                f"请配置 ANTHROPIC_API_KEY 并关闭 MOCK_MODE 后重新上传，"
                f"以获取AI从图片中提取的真实需求内容。"
            ),
        }
        if self.use_mock:
            return mock_result

        image_b64 = base64.b64encode(image_bytes).decode("ascii")
        return await self.call_claude_tool_vision(
            SYSTEM,
            "请从这张图片中提取需求标题和正文内容。",
            image_b64, media_type,
            "submit_requirement_extraction", EXTRACT_TOOL_SCHEMA,
            max_tokens=4096, mock_result=mock_result,
        )
