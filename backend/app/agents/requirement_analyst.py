"""Agent 1: 需求分析 — 输出"问题点清单"(AnalysisResult形状)，供Agent2直接消费。"""
from __future__ import annotations
from .base_agent import BaseAgent

SYSTEM = """你是一名资深测试架构师。给定需求文档，找出文档中表述不清晰、存在歧义或需要与需求方进一步确认的内容点(issue_points)。
每个问题点包含：
- issue_id: "ISSUE-1","ISSUE-2"...(从1开始顺序编号)
- description: 直接引用/摘录需求文档原文中存在争议或表述不清的内容片段，尽量使用原文表述，不要改写为泛化描述
- module: 该问题点所属功能模块，必须从给定模块枚举中选择；无法确定时设为null（仅供测试用例生成内部使用，不会展示给用户）
- platforms: 涉及的端列表，必须从给定端枚举(下方 key: label 清单)中选择，仅供内部使用。
  判端以需求/确认点【实际给出的内容】为准：只有当内容明确涉及接口(后端API调用、接口路径、入参/出参/字段、请求或响应报文、协议、前后端联调等)时，才纳入标签为「接口」的端；
  纯前端页面/交互/业务规则只标对应的 PC/App/小程序端，不要给纯UI内容标接口端。一个问题点可同时涉及多端。
- confirmation_points: 针对该问题点列出1-3条需要向需求方确认的具体问题(字符串数组)
调用工具输出结果，source_req_id和product_line直接复用输入值。"""


def _build_tool_schema(module_keys: list[str], platform_keys: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "source_req_id": {"type": "string"},
            "product_line": {"type": ["string", "null"]},
            "issue_points": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "issue_id": {"type": "string"},
                        "description": {"type": "string"},
                        "module": {"type": ["string", "null"], "enum": module_keys + [None]},
                        "platforms": {"type": "array", "items": {"type": "string", "enum": platform_keys}},
                        "confirmation_points": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["issue_id", "description", "module", "platforms", "confirmation_points"],
                },
            },
        },
        "required": ["source_req_id", "product_line", "issue_points"],
        "description": "需求分析结果：问题点清单及待确认点",
    }


def _wrap_confirmation_points(points: list[str], issue_idx: int) -> list[dict]:
    return [
        {
            "point_id": f"CP-{issue_idx}-{i + 1}",
            "content": content,
            "status": "pending_confirmation",
            "confirmation": None,
            "no_confirmation_needed": False,
        }
        for i, content in enumerate(points)
    ]


COVERAGE_SYSTEM = """你是资深测试架构师，做"需求覆盖分析(漏测检测)"。
给你：需求文档原文、需求分析确认结论、以及【现有测试用例标题清单】。
请先在心里把需求拆成应测的功能点/规则全集，再逐一判断每个功能点是否已被现有用例覆盖：
- covered_points: 已被现有用例覆盖的功能点(简述)
- uncovered_points: 【未被任何现有用例覆盖】的功能点(漏测)，每条简述该点及为什么算漏测
- coverage_percent: 覆盖率 = 已覆盖功能点数 / 功能点总数 * 100，取整
理论上应接近 100%；不到 100% 时务必把缺的功能点列进 uncovered_points。调用工具输出结果。"""


def _coverage_tool_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "coverage_percent": {"type": "integer"},
            "total_points": {"type": "integer"},
            "covered_points": {"type": "array", "items": {"type": "string"}},
            "uncovered_points": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["coverage_percent", "uncovered_points"],
        "description": "需求覆盖分析结果",
    }


class RequirementAnalystAgent(BaseAgent):
    async def analyze_coverage(self, title: str, content: str, confirmation: str | None,
                              case_titles: list[str]) -> dict:
        """按需的覆盖分析：对比需求与现有用例标题，给出覆盖率与未覆盖功能点。一次 AI 调用，不落库。"""
        if self.use_mock:
            return {"coverage_percent": 100, "total_points": len(case_titles),
                    "covered_points": case_titles, "uncovered_points": []}
        prompt = (
            f"需求标题: {title}\n\n需求内容:\n{content}\n\n"
            + (f"需求分析确认结论:\n{confirmation}\n\n" if confirmation else "")
            + "现有测试用例标题清单:\n" + ("\n".join(f"- {t}" for t in case_titles) or "(暂无用例)")
        )
        return await self.call_claude_tool(
            COVERAGE_SYSTEM, prompt, "submit_coverage", _coverage_tool_schema(),
            max_tokens=3000, mock_result={"coverage_percent": 0, "uncovered_points": []},
        )

    async def analyze(
        self,
        requirement_title: str,
        requirement_content: str,
        source_req_id: str,
        product_line: str | None,
        modules: list[dict],
        platforms: list[dict],
        images: list | None = None,
    ) -> dict:
        module_keys = [m["key"] for m in modules]
        platform_keys = [p["key"] for p in platforms]

        mock_result = {
            "source_req_id": source_req_id,
            "product_line": product_line,
            "issue_points": [
                {
                    "issue_id": "ISSUE-1",
                    "description": "用户名密码正确时可成功登录",
                    "module": module_keys[0] if module_keys else None,
                    "platforms": [platform_keys[0]] if platform_keys else [],
                    "confirmation_points": ["密码错误次数限制的具体策略是什么（锁定阈值/锁定时长）？"],
                },
                {
                    "issue_id": "ISSUE-2",
                    "description": "登录接口在并发/超时场景下的稳定性要求未明确",
                    "module": module_keys[0] if module_keys else None,
                    "platforms": ["backend_api"] if "backend_api" in platform_keys else platform_keys[:1],
                    "confirmation_points": [
                        "并发量级（如10/100）下的预期响应时间是多少？",
                        "请求超时后是否需要自动重试？",
                    ],
                },
            ],
        }

        if self.use_mock:
            result = mock_result
        else:
            tool_schema = _build_tool_schema(module_keys, platform_keys)
            modules_text = "\n".join(f"- {m['key']}: {m['label']}" for m in modules)
            platforms_text = "\n".join(f"- {p['key']}: {p['label']}" for p in platforms)
            prompt = (
                f"需求标题: {requirement_title}\n\n"
                f"需求内容:\n{requirement_content}\n\n"
                f"source_req_id: {source_req_id}\n"
                f"product_line: {product_line}\n\n"
                f"可选模块枚举:\n{modules_text}\n\n"
                f"可选端枚举:\n{platforms_text}"
            )
            result = await self.call_claude_tool_multimodal(
                SYSTEM, prompt, images or [], "submit_analysis_result", tool_schema,
                max_tokens=4096, mock_result=mock_result,
            )

        for idx, ip in enumerate(result.get("issue_points", []), start=1):
            ip["confirmation_points"] = _wrap_confirmation_points(ip.get("confirmation_points", []), idx)

        # 有图但 AI 视觉识别失败：保留分析结果，但加醒目标注，避免用户误以为"文档里真没内容"
        if not self.use_mock and self.last_vision_failed_images:
            n = self.last_vision_failed_images
            result["vision_warning"] = {
                "image_count": n,
                "message": f"本次有 {n} 张需求图片未能被 AI 识别（视觉服务暂不可用），"
                           f"以下分析可能不完整、图片中的内容未纳入，请人工核对或稍后重试。",
            }
        return result
