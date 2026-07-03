"""Agent 5: 缺陷诊断 — 判断是否为真实缺陷，输出可直接落地Defect表的诊断信息。"""
from __future__ import annotations
import json
from .base_agent import BaseAgent

SYSTEM = """你是一名缺陷管理专家。分析失败测试结果，判断是否为真实缺陷，并给出可直接写入缺陷管理系统的诊断信息。
- is_real_defect: 是否为真实产品缺陷（false=脚本误报/环境问题等，不应创建工单）
- severity: 缺陷等级，取值 1级-致命 / 2级-严重 / 3级-一般 / 4级-轻微
- confidence: HIGH/MEDIUM/LOW（诊断置信度）
- title: 缺陷标题（适合作为工单标题）
- summary: 一句话描述问题现象
- type: functional/ui/performance/security/compatibility/other
- reproduction_steps: 复现步骤列表
- affected_scope: 影响范围描述
调用工具输出结果。"""

DIAGNOSIS_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "is_real_defect": {"type": "boolean"},
        "severity": {"type": "string", "enum": ["1级-致命", "2级-严重", "3级-一般", "4级-轻微"]},
        "confidence": {"type": "string", "enum": ["HIGH", "MEDIUM", "LOW"]},
        "title": {"type": "string"},
        "summary": {"type": "string"},
        "type": {"type": "string", "enum": ["functional", "ui", "performance", "security", "compatibility", "other"]},
        "reproduction_steps": {"type": "array", "items": {"type": "string"}},
        "affected_scope": {"type": "string"},
    },
    "required": ["is_real_defect", "severity", "confidence", "title", "summary", "type"],
    "description": "缺陷诊断结果，用于创建Defect记录",
}


class DefectDiagnosticianAgent(BaseAgent):
    async def diagnose(
        self,
        test_case_title: str,
        error_message: str,
        execution_context: dict | None = None,
    ) -> dict:
        mock_result = {
            "is_real_defect": True,
            "severity": "2级-严重",
            "confidence": "MEDIUM",
            "title": f"{test_case_title}: 接口在并发请求下返回异常",
            "summary": "登录接口在并发请求下返回500错误",
            "type": "functional",
            "reproduction_steps": ["同时发起10个并发登录请求", "观察响应状态码"],
            "affected_scope": "所有需要认证的功能模块",
        }
        if self.use_mock:
            return mock_result

        ctx = json.dumps(execution_context or {}, ensure_ascii=False)
        prompt = (
            f"测试用例: {test_case_title}\n\n"
            f"错误信息:\n{error_message}\n\n"
            f"执行上下文:\n{ctx}"
        )
        return await self.call_claude_tool(
            SYSTEM, prompt, "submit_defect_diagnosis", DIAGNOSIS_TOOL_SCHEMA,
            max_tokens=1024, mock_result=mock_result,
        )
