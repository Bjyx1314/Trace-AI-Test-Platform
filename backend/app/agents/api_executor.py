"""ApiExecutorAgent —— AI 直连接口执行(免脚本)。

给 AI 接口用例(标题/步骤/预期)+ base_url，让它产出要真实发送的 HTTP 请求序列与断言；
平台用 httpx 真发请求、按断言判定。AI 只负责"构造请求/断言"，是否通过基于真实响应判定，
不由 AI 假装通过。与 web/App 的 AI 执行同思路：免脚本、免框架。
"""
from __future__ import annotations
from typing import Any

from app.agents.base_agent import BaseAgent

_SYSTEM = """你是接口测试执行助手。根据给定的接口测试用例和被测系统基础地址(base_url)，
产出需要【真实发送】的 HTTP 请求序列(可含前置鉴权)以及判定该用例是否通过的断言。

规则：
- 只输出能真实发送的请求。URL 用 base_url 拼接相对路径；用例里若已含完整 URL 则直接用。
- 相对路径写成 /xxx 即可，平台会自动与 base_url 拼接；也可用占位 {base_url}。
- 多步：如需先登录拿 token，把前置请求排在前面，用 extract 从其响应取值(JSON 路径如 data.token)，
  后续请求在 headers/body 里用 {{变量名}} 引用。
- 断言要可机器判定，尽量体现用例的"预期结果"：
  · status_equals(value=整数状态码) / status_lt(value=整数)
  · jsonpath_equals(path=如 data.code, value=期望值)
  · body_contains(value=应包含的子串)
- 不要编造无法从用例推断的关键字段；信息不足时给最合理的请求，并在 note 里说明你的假设。"""

_TOOL_SCHEMA = {
    "type": "object",
    "description": "接口执行计划：要真实发送的请求序列 + 判定断言",
    "properties": {
        "requests": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "method": {"type": "string", "description": "GET/POST/PUT/DELETE…"},
                    "url": {"type": "string", "description": "完整 URL 或相对 base_url 的路径(如 /api/login)"},
                    "headers": {"type": ["object", "null"]},
                    "params": {"type": ["object", "null"], "description": "query 参数"},
                    "json_body": {"type": ["object", "null"], "description": "JSON 请求体"},
                    "extract": {
                        "type": ["object", "null"],
                        "description": "从本请求响应里抽取变量供后续步骤引用：{变量名: JSON路径}",
                    },
                },
                "required": ["method", "url"],
            },
        },
        "asserts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {
                        "type": "string",
                        "enum": ["status_equals", "status_lt", "jsonpath_equals", "body_contains"],
                    },
                    "path": {"type": ["string", "null"], "description": "jsonpath_equals 用，如 data.code"},
                    "value": {"description": "期望值"},
                },
                "required": ["type"],
            },
        },
        "note": {"type": ["string", "null"], "description": "假设/说明"},
    },
    "required": ["requests"],
}


class ApiExecutorAgent(BaseAgent):
    """把接口用例翻成可发送的 HTTP 请求计划。"""

    async def build_plan(self, case: dict[str, Any], base_url: str | None) -> dict:
        steps = case.get("steps") or []
        step_txt = "\n".join(
            f"- 步骤{s.get('seq', i + 1)}: {s.get('action', '')}  预期: {s.get('expected', '')}"
            for i, s in enumerate(steps)
        ) or "(无明细步骤)"
        pre = case.get("preconditions") or []
        pre_txt = "；".join(pre) if pre else "(无)"
        user = (
            f"【用例标题】{case.get('title', '')}\n"
            f"【被测 base_url】{base_url or '(未配置，请尽量从用例推断完整 URL)'}\n"
            f"【前置条件】{pre_txt}\n"
            f"【步骤与分步预期】\n{step_txt}\n"
            f"【总预期结果】{case.get('expected_result', '') or '(未单独给出)'}\n\n"
            "请产出 api_plan：真实可发送的请求序列 + 可机器判定的断言。"
        )
        return await self.call_claude_tool(
            _SYSTEM, user, "api_plan", _TOOL_SCHEMA, max_tokens=2000
        )
