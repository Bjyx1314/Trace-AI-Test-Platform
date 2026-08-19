"""Agent 1: 需求分析 — 输出"问题点清单"(AnalysisResult形状)，供Agent2直接消费。"""
from __future__ import annotations

import re

from .base_agent import BaseAgent

SYSTEM = """你是一名资深测试架构师。给定需求文档，找出文档中表述不清晰、存在歧义或需要与需求方进一步确认的内容点(issue_points)。
每个问题点包含：
- issue_id: "ISSUE-1","ISSUE-2"...(从1开始顺序编号)
- description: 直接引用/摘录需求文档原文中存在争议或表述不清的内容片段，尽量使用原文表述，不要改写为泛化描述
- feature: 该问题点所属【二级功能】名称——用于把测试用例归类分组。要求按【页面/操作入口/业务对象】归一，简洁精准。划分规则：
  · 注意：feature 只是「二级功能分组标签」，不是测试用例拆分粒度；不要因为 feature 收敛就合并问题点或减少后续用例。
  · feature 不是泛泛的大模块，也不要细到字段/筛选项/搜索条件；优先取用户实际进入或操作的稳定页面、入口、业务对象。
  · 同一页面或同一入口下的字段展示、搜索、筛选、排序、分页、按钮状态、空态、权限展示等，都必须共用同一个 feature。
    例如"资源列表搜索""资源列表筛选""资源列表资源名称字段""资源列表卡片展示"都归为"资源列表"；
    "设备列表仓库搜索条件""设备列表仓库字段展示"都归为"设备列表"。
  · 只有确实是不同页面/不同业务流程/不同入口时才拆开，例如"资源列表""资源详情""扫码入库""业务单据列表"可以分开；
    首页多个不同待办入口可按入口分开，如"首页报修待办入口""首页报停待办入口"。
  · 同一个二级功能下的多个问题点【必须共用同一个 feature 名】；名称控制在 2~12 字，通常用页面/入口/业务对象名，别把原文长句塞进来。
- module: 该问题点所属功能模块，必须从给定模块枚举中选择；无法确定时设为null（仅供测试用例生成内部使用，不会展示给用户）
- platforms: 涉及的端列表，必须从给定端枚举(下方 key: label 清单)中选择，仅供内部使用。
  判端以需求/确认点【实际给出的内容】为准：只有当内容明确涉及接口(后端API调用、接口路径、入参/出参/字段、请求或响应报文、协议、前后端联调等)时，才纳入标签为「接口」的端；
  纯前端页面/交互/业务规则只标对应的 PC/App/小程序端，不要给纯UI内容标接口端。一个问题点可同时涉及多端。
- confirmation_points: 针对该问题点列出1-3条需要向需求方确认的具体问题(字符串数组)
【只依据原文，绝不臆造】issue_points 与 confirmation_points 只能针对需求原文【实际出现】的内容提出——对原文写了但表述不清/有歧义/有多种理解的地方提确认；
  【绝不】凭空发明原文【完全没提及】的规则/字段/约束/数值/流程。例：原文只说"新建业务单据生成对应数量的资源编码"、没说编码的编号规则，就【不要】造出"资源编码是否按连续流水/统一流水生成"这类原文没有的确认点；原文没写的展示样式/排序规则/校验规则同理不要编。宁可少提，也不要无中生有——凭空造出的确认点会被下游生成成"需求根本没有的用例"。
  【区分：边界/异常追问 要提，不算臆造】需求写了输入项/字段/表单/操作，但没写边界、取值范围、长度上限、必填与否、空值/超长/非法输入如何处理、异常/失败流程——这些【对"原文已出现功能"的边界与异常追问】正是该提的确认点(如"数量是否有上下限/最大值""哪些必填""非法输入怎么提示")，属于正常测试维度，要照常提。禁止的只是【发明一个原文根本没出现过的功能或业务规则】(如原文没提编号规则却造"连续流水编号")。一句话：对"原文有的功能"追问边界/异常=可以；对"原文没有的规则"凭空发明=禁止。
【先通读全文，再决定问不问——只问"全文都找不到答案"的点】提出任何一条 confirmation_point 之前，必须先【通读整篇需求全文】，并在【全文范围内】检索该问题是否已经有答案：答案常常不在该功能描述的紧邻段落，而在需求的【后文、其他章节、交互/规则说明、状态或字段表、流程图、附录、图注】里。只要原文【任意位置】已明确给出答案(哪怕在别处、在更靠后的段落)，就【绝不】把它列为确认点——那不是"待确认"，而是"已明确"，应当作为已知规则直接用于后续用例，不要再拎出来问。
  【模糊说法被后文细化，以细化为准】若原文某处先给了"A 或 B""前端过滤或置灰"这类模糊/二选一的说法，但同句或后文/别处又进一步指定了具体做法(如"添加按钮置灰并提示不在可用资源范围")，以【更具体、更明确的那处】为准，视为已明确，不要再就前面的模糊说法提"到底用 A 还是 B"。
  【必须收掉的两类真实反例，照此判定，绝不再问】
    · 原文"对不在可用范围内的资源，前端过滤掉或置灰。…不可用资源限制-添加按钮置灰-增加提示：不在当前机型可用资源范围"——后半句已把做法定死为【置灰并提示】，判定为已明确，【绝不】再问"最终是过滤还是置灰"。
    · 原文"三级品类：下拉选项，支持多选，多选筛选可评估额外开发成本，改为单选"——已明确【改为单选】，判定为已明确，【绝不】再问"最终多选还是单选"。
    凡是"先给两可/模糊说法、同句或紧邻文字里又给了确切结论(改为X、最终按X、置灰并提示…)"的，一律以确切结论为准、不产出确认点。
  只有【通读全文后仍找不到答案】，或【原文多处表述自相矛盾/冲突】，或【确实存在多种理解且原文无处判定】的点，才作为 confirmation_point 提出。宁可先在全文找答案，也不要把"其实文档已写清、只是分散在别处"的内容当成待确认点抛给需求方。
  【输出前逐条自检】每条 confirmation_point 在写入前，回读它所属 issue_point 的 description 及需求原文：若这条要问的答案其实已经出现在其中(包括"A或B后又指定了B""先说X又改为Y"这类)，就【删掉这条，不要输出】。最终只保留"回读后确认原文确实没给答案/确实自相矛盾/确实多解无法判定"的确认点。
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
                        "feature": {"type": "string", "description": "所属二级功能名——按页面/入口/业务对象归一(如\"资源列表\";同页搜索/筛选/字段展示共用同一名称)"},
                        "module": {"type": ["string", "null"], "enum": module_keys + [None]},
                        "platforms": {"type": "array", "items": {"type": "string", "enum": platform_keys}},
                        "confirmation_points": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["issue_id", "description", "feature", "module", "platforms", "confirmation_points"],
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


_LIST_SUFFIX_RE = re.compile(
    r"(.+?列表)(?:页|页面|卡片|表格|列)?(?:[-—_：:、\s]*)?"
    r"(?:搜索|查询|筛选|过滤|排序|分页|字段|列|展示|显示|完整度|条件|入口|按钮|状态|空态|权限|校验|验证).*$"
)
_PAGE_SUFFIX_RE = re.compile(
    r"(.+?(?:详情页|详情|页面|页|弹窗|卡片|表格|入口))(?:[-—_：:、\s]*)?"
    r"(?:搜索|查询|筛选|过滤|排序|分页|字段|列|展示|显示|条件|按钮|状态|空态|权限|校验|验证).*$"
)


def normalize_secondary_feature(value: str | None, context: str | None = None) -> str:
    """把二级功能归一到页面/入口/业务对象粒度，避免搜索/筛选/字段拆出碎分组。"""
    raw = (value or "").strip()
    text = f"{raw} {context or ''}"
    if not raw:
        return raw

    # 常见列表页：搜索/筛选/字段/卡片展示都归到列表本身。
    for pattern in (_LIST_SUFFIX_RE, _PAGE_SUFFIX_RE):
        match = pattern.search(raw)
        if match:
            return match.group(1).strip("-—_：:、 ")

    # 需求里常见"字段名"被抽成 feature 时，结合上下文收敛回所在列表/详情。
    page_match = re.search(r"([\u4e00-\u9fa5A-Za-z0-9]+?(?:列表|详情页|详情|页面|页|弹窗|卡片|表格|入口))", text)
    if page_match and any(k in text for k in ("搜索", "查询", "筛选", "字段", "列", "展示", "显示", "卡片")):
        page = page_match.group(1)
        if page.endswith("卡片") and "列表" in page:
            return page[:page.index("列表") + len("列表")]
        return page

    return raw


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
                    "feature": "登录",
                    "module": module_keys[0] if module_keys else None,
                    "platforms": [platform_keys[0]] if platform_keys else [],
                    "confirmation_points": ["密码错误次数限制的具体策略是什么（锁定阈值/锁定时长）？"],
                },
                {
                    "issue_id": "ISSUE-2",
                    "description": "登录接口在并发/超时场景下的稳定性要求未明确",
                    "feature": "登录",
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
            ip["feature"] = normalize_secondary_feature(ip.get("feature"), ip.get("description"))
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
