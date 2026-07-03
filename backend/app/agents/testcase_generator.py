"""Agent 2: 用例生成 — 根据问题点清单直接生成新schema形状的TestCase字段集。"""
from __future__ import annotations
import json
from .base_agent import BaseAgent

SYSTEM = """你是一名专业测试工程师。请【综合需求文档原文 与 确认后的需求分析内容】生成测试用例列表。

【最重要：目标级原子化 —— 一条用例只验证一个目标】
- 一条用例只覆盖【一个验证目标】(一个功能点/一条业务规则/一个场景)，对应一个明确、可判定的总预期；
- 判定拆分准则：【同一页面/同一流程、服务同一目标】的若干小检查 → 放在【同一条用例的多个步骤】里；
  【不同目标/不同页面/不同入口/不同模式】→ 必须拆成【不同用例】。
- 例：
  * "新建用户必填校验"是一个目标 → 一条用例，步骤里逐项检查(缺用户名→拦截、缺角色→拦截、都填→可提交)，不要每个必填项拆一条；
  * 但"列表展示"与"新建并提交"是两个目标、两个页面 → 拆成两条用例；
  * 正常流程 / 边界 / 异常 通常是不同目标 → 各自成条。
- 严禁把多个不相关目标塞进一条(例如"进列表+新建+必填+提交+日历可见"要按目标拆成多条)。
- 步骤(steps)只服务本条这一个目标：必要的导航/前置 + 针对该目标的核对，精简清晰。

【覆盖要求】
- 以需求文档原文为基础，覆盖其描述的所有功能点、业务规则、正常/边界/异常场景(用多条原子用例覆盖)；
- 充分落实"需求分析确认结果"及各问题点已确认的澄清结论(confirmation_points)，这些规则都要有对应原子用例；
- 不要遗漏原文中虽未被列入问题点、但属于需求范围的内容。宁可用例多而细，也不要一条混多点。

每个用例包含：
- title: 点明本条验证的【那一个点】
- modules(从给定模块枚举选), platforms(从给定端枚举选)：判端以本条用例【实际验证的对象】为准——
  只有当用例直接验证接口(后端API调用、接口入参/出参/字段、请求/响应报文、协议)时才标「接口」端；
  纯页面/交互/前端校验只标对应 PC/App/小程序端，不要给纯UI用例标接口端。
- priority: P0/P1/P2
- preconditions: 前置条件列表
- steps: [{seq, action, expected, check_points}] —— 只服务本条验证点；expected 必须填写，不能为空
  · check_points: 该步【可核对的判定锚点】，每条是一个具体、可见、客观的事实(界面上该出现/不该出现的文案、元素、状态、字段、数值)，
    用于执行时逐条核对，避免凭感觉判过。例如["页面标题显示『用户列表报表』","存在『导出』按钮","不出现旧文案『用户明细』"]。
    锚点要具体可观察，不要写"展示正常"这类无法核对的话。
- expected_result: 本条单一的总预期；case_type: 当前只生成「功能」或「UI」两类，不要生成性能/安全/兼容性/其他
- source_issue_point: 若该用例对应某个问题点则填其 issue_id；来自原文其他部分可留空
- tags: 可选标签列表
调用工具输出结果，字段为cases数组。优先保证"目标级原子化(一条一个目标，同屏同目标可多步、跨目标才拆条)"与对需求的完整覆盖。"""


def _build_tool_schema(module_keys: list[str], platform_keys: list[str]) -> dict:
    case_item = {
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "modules": {"type": "array", "items": {"type": "string", "enum": module_keys}},
            "platforms": {"type": "array", "items": {"type": "string", "enum": platform_keys}},
            "priority": {"type": "string", "enum": ["P0", "P1", "P2"]},
            "preconditions": {"type": "array", "items": {"type": "string"}},
            "steps": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "seq": {"type": "integer"},
                        "action": {"type": "string"},
                        "expected": {"type": "string"},
                        "check_points": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "本步可核对的判定锚点(具体可见的事实)，执行时逐条核对",
                        },
                    },
                    "required": ["seq", "action", "expected"],
                },
            },
            "expected_result": {"type": "string"},
            "source_issue_point": {"type": "string"},
            "case_type": {"type": "string", "enum": ["功能", "UI"]},
            "tags": {"type": ["array", "null"], "items": {"type": "string"}},
        },
        "required": [
            "title", "modules", "platforms", "priority", "preconditions",
            "steps", "expected_result", "case_type",
        ],
    }
    return {
        "type": "object",
        "properties": {"cases": {"type": "array", "items": case_item}},
        "required": ["cases"],
        "description": "测试用例列表",
    }


def _mock_cases(issue_points: list[dict], module_keys: list[str], platform_keys: list[str]) -> dict:
    cases = []
    for ip in (issue_points or [{"issue_id": "ISSUE-1", "description": "Mock 用例", "module": None, "platforms": ["web"]}]):
        is_api = "backend_api" in (ip.get("platforms") or [])
        cases.append({
            "title": f"验证: {ip.get('description', '')[:50]}",
            "modules": [ip["module"]] if ip.get("module") else [],
            "platforms": ip.get("platforms") or ["web"],
            "priority": "P1",
            "preconditions": ["测试环境已部署最新版本"],
            "steps": [
                {"seq": 1, "action": "准备测试数据", "expected": "数据准备完成"},
                {"seq": 2, "action": "执行操作", "expected": "操作成功响应"},
                {"seq": 3, "action": "验证结果", "expected": "结果符合预期"},
            ],
            "expected_result": "功能符合预期，无异常",
            "source_issue_point": ip.get("issue_id"),
            "case_type": "功能",
            "tags": None,
        })
    return {"cases": cases}


class TestCaseGeneratorAgent(BaseAgent):
    async def generate(
        self,
        requirement_title: str,
        requirement_content: str,
        issue_points: list[dict],
        analysis_confirmation: str | None,
        modules: list[dict],
        platforms: list[dict],
        images: list | None = None,
    ) -> list[dict]:
        module_keys = [m["key"] for m in modules]
        platform_keys = [p["key"] for p in platforms]

        if self.use_mock:
            return _mock_cases(issue_points, module_keys, platform_keys)["cases"]

        tool_schema = _build_tool_schema(module_keys, platform_keys)
        modules_text = "\n".join(f"- {m['key']}: {m['label']}" for m in modules)
        platforms_text = "\n".join(f"- {p['key']}: {p['label']}" for p in platforms)
        prompt = (
            f"需求: {requirement_title}\n\n"
            f"需求文档内容:\n{requirement_content}\n\n"
            + (f"需求分析确认结果:\n{analysis_confirmation}\n\n" if analysis_confirmation else "")
            + f"问题点清单:\n{json.dumps(issue_points, ensure_ascii=False, indent=2)}\n\n"
            f"可选模块枚举:\n{modules_text}\n\n"
            f"可选端枚举:\n{platforms_text}"
        )
        result = await self.call_claude_tool_multimodal(
            SYSTEM, prompt, images or [], "submit_test_cases", tool_schema,
            max_tokens=8192, mock_result=_mock_cases(issue_points, module_keys, platform_keys),
        )
        return result.get("cases", [])
