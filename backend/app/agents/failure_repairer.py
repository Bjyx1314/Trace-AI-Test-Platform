"""Agent 4: 失败修复 — 分析失败测试结果，输出根因/修复建议/failure_type分类。"""
from __future__ import annotations
from .base_agent import BaseAgent

SYSTEM = """你是一名自动化测试维护专家。给定失败测试的错误信息和脚本，分析失败原因并给出修复建议。
- root_cause: 根本原因描述
- fix_type: selector_change/timing/data/environment/logic/unknown
- failure_type: 该失败应归类为 script_error(脚本自身问题)/env_error(环境问题)/real_defect(产品真实缺陷)
  参考：selector_change/timing/logic/unknown通常是script_error；environment通常是env_error；
  data问题需结合上下文判断，可能是script_error或real_defect
- suggestion: 具体修复建议
- fixed_script: 修复后的完整脚本（无法直接修复则为null）
调用工具输出结果。"""

REPAIR_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "root_cause": {"type": "string"},
        "fix_type": {"type": "string", "enum": ["selector_change", "timing", "data", "environment", "logic", "unknown"]},
        "failure_type": {"type": "string", "enum": ["script_error", "env_error", "real_defect"]},
        "suggestion": {"type": "string"},
        "fixed_script": {"type": ["string", "null"]},
    },
    "required": ["root_cause", "fix_type", "failure_type", "suggestion"],
    "description": "失败测试的根因分析与修复建议",
}


class FailureRepairerAgent(BaseAgent):
    async def analyze_and_repair(
        self,
        error_message: str,
        script: str,
        test_case_title: str,
    ) -> dict:
        mock_result = {
            "root_cause": "元素定位器已过时，页面结构发生变化",
            "fix_type": "selector_change",
            "failure_type": "script_error",
            "suggestion": "更新CSS选择器或使用更稳定的属性定位（如data-testid）",
            "fixed_script": None,
        }
        if self.use_mock:
            return mock_result

        prompt = (
            f"测试用例: {test_case_title}\n\n"
            f"错误信息:\n{error_message}\n\n"
            f"当前脚本:\n{script}"
        )
        return await self.call_claude_tool(
            SYSTEM, prompt, "submit_repair_analysis", REPAIR_TOOL_SCHEMA,
            max_tokens=2048, mock_result=mock_result,
        )
