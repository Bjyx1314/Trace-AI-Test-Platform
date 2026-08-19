"""展示字段/展示逻辑用例生成器（确定性模板，不靠 AI 推理）。

分工（与 testcase_generator 的 AI 生成互补）：
- 需求里「列表/卡片/详情/使用记录/列表列」这种**展示字段清单**是高度机械的：每个字段 → 一条
  「操作完后详情/列表页是否展示对」的正常展示校验。这部分用**模板确定性生成**：每字段必有用例、
  零截断、预期天然具体（含样式/位置/格式），根治 AI 生成「漏字段 / 写成"符合需求"」的老问题。
- **不造** 空值/超长/多值这类数据边界（那属于操作/输入字段，交给 AI）；只覆盖需求写明的展示规则
  （加粗/灰标签/日期格式/同行/倒序/"…等"/可复制/拼接/条件展示/映射/兜底/显隐/聚合）。

两步：
1) extract_display_blocks(content)：一次**小 AI 抽取**把字段段落转成结构化 JSON（字段名+展示属性+示例
   +区块类型+展示逻辑规则），AI 只做"抽字段/规则"这件小而稳的事，不写用例、不会截断。
2) emit_field_cases(blocks)：**纯模板**把结构化字段落成用例（title/steps/expected/check_points/covered_items）。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from .requirement_analyst import normalize_secondary_feature

# ── 抽取：小 AI 只产出结构化字段清单/展示逻辑规则 ─────────────────────────────
EXTRACT_SYSTEM = (
    "你是需求解析器。只从需求原文里抽取【展示字段清单】与【展示逻辑规则】——即列表/卡片/详情/"
    "使用记录/表格列这类「展示给用户看的字段」、字段样式，以及字段如何由条件/来源/计算/映射决定。"
    "不要抽取表单录入/操作类字段，不要编造原文没有的字段、样式或规则。\n"
    "每个展示区块(block)给出：block(区块名，如'资源列表卡片')、kind(list_card 列表卡片 / detail 详情 / "
    "record_list 使用记录等有序记录 / list_columns 表格列)、platform(所属端，若原文可判断，否则留空)、"
    "page_path(导航路径数组)、"
    "fields(字段数组，每个含 name 字段名、display 展示属性关键词数组[如 加粗/标题/突出/可复制/灰色/标签/"
    "日期/底部/与仓库同行 等，按原文]、example 原文给的示例值如'48V/100Ah'或'2026-08-10'，无则空)、"
    "rules(展示逻辑规则数组，每项是对象，含 type/target/condition/sources/expected/raw_text)。忠于原文，只抽不改。\n"
    "type 只能取：composition(字段拼接/组合)、conditional_display(条件展示)、mapping(枚举/状态映射)、"
    "fallback(空值/缺省兜底)、visibility(显隐/权限)、formatting(格式/样式)、aggregation(计算/统计)、"
    "ordering(排序)、multi_value_ellipsis(多值折叠显示…等)。\n"
    "【展示逻辑规则不能当普通字段丢掉】：凡原文写了字段取值/文案/显隐/格式由其他字段、状态、角色、计算、"
    "空值或条件决定，都必须抽成 rules。例："
    "「无法验机时展示原因，取无法验机结构化原因 + 补充说明拼装」→ "
    "{type:'composition', target:'原因', condition:'无法验机时', sources:['无法验机结构化原因','补充说明'], "
    "expected:'原因同时包含无法验机结构化原因和补充说明', raw_text:'原文片段'}。\n"
    "【page_path 很重要】：从端/平台一直到该展示所在页面的【完整导航路径，逐级列出，一个中间页面都不能跳过】。"
    "例：需求写「管理平台 | 资源中心」，其下「资源列表」页里有「打印预览/使用记录」→ 打印预览的 "
    "page_path=[\"管理平台\",\"资源中心\",\"资源列表\",\"打印预览\"]；【漏掉中间的「资源列表」是错的】。"
    "凡原文出现的层级(尤其列表页/详情页这种承载它的中间页)都必须按顺序保留；某层原文确实没有才不写。"
)

EXTRACT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "block": {"type": "string"},
                    "kind": {"type": "string", "enum": ["list_card", "detail", "record_list", "list_columns"]},
                    "platform": {"type": "string"},
                    "page_path": {"type": "array", "items": {"type": "string"}},
                    "fields": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "display": {"type": "array", "items": {"type": "string"}},
                                "example": {"type": "string"},
                            },
                            "required": ["name"],
                        },
                    },
                    "rules": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "type": {"type": "string", "enum": [
                                    "composition", "conditional_display", "mapping", "fallback",
                                    "visibility", "formatting", "aggregation", "ordering",
                                    "multi_value_ellipsis",
                                ]},
                                "target": {"type": "string"},
                                "condition": {"type": "string"},
                                "sources": {"type": "array", "items": {"type": "string"}},
                                "expected": {"type": "string"},
                                "raw_text": {"type": "string"},
                            },
                            "required": ["type", "target", "raw_text"],
                        },
                    },
                },
                "required": ["block", "fields"],
            },
        }
    },
    "required": ["blocks"],
}


async def extract_display_blocks(content: str) -> list[dict]:
    """小 AI 抽取展示字段区块/展示逻辑。失败/无展示规则则返回 []（不阻断，调用方照常走 AI）。"""
    from app.agents.llm import get_provider
    provider = get_provider()
    user = f"需求原文：\n{content}\n\n只输出展示字段清单的结构化结果。"
    try:
        out = await provider.tool(EXTRACT_SYSTEM, user, "submit_display_fields", EXTRACT_SCHEMA, 4096)
    except Exception:
        return []
    blocks = (out or {}).get("blocks") or []
    return [b for b in blocks if isinstance(b, dict) and (b.get("fields") or b.get("rules"))]


# ── 模板：展示属性关键词 → 可核对锚点（确定性映射）──────────────────────────
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
_COMPOSE_KW = ("拼接", "拼装", "组合", "合并")
_FALLBACK_KW = ("为空", "空值", "无值", "缺省", "默认", "--", "兜底")
_VISIBILITY_KW = ("隐藏", "不展示", "显示", "展示", "可见", "不可见", "权限")
_AGGREGATION_KW = ("完成率", "合计", "总数", "统计", "计算", "求和", "数量")


def _humanize(name: str, display: list[str], example: str | None) -> tuple[str, list[str]]:
    """由字段展示属性生成 (预期一句话, check_points 列表)。全部确定性，不调 AI。"""
    d = " ".join(display or [])
    cps: list[str] = []
    if example:
        cps.append(f"{name}展示具体值，如“{example}”")
    else:
        cps.append(f"{name}字段有值并正确展示")

    if "加粗" in d:
        cps.append(f"{name}文字加粗展示")
    if "标题" in d:
        cps.append(f"{name}位于卡片标题位置")
    if "突出" in d:
        cps.append(f"{name}视觉突出于其他普通字段")
    if any(k in d for k in ("可复制", "支持复制", "复制")):
        cps.append(f"{name}附近存在复制入口（复制按钮/图标/长按）")
        cps.append(f"执行复制后提示“已复制”或剪贴板内容为{name}的值")
    if "标签" in d:
        cps.append(f"{name}以标签样式展示")
    if any(k in d for k in ("灰色", "灰")):
        cps.append(f"{name}为灰色文字/标签")
    if "底部" in d:
        cps.append(f"{name}位于卡片底部信息行")
    if any(k in d for k in ("日期", "有效期")) or (example and _DATE_RE.search(example or "")):
        cps.append(f"{name}显示为日期格式 YYYY-MM-DD" + (f"（如“{example}”）" if example and _DATE_RE.search(example) else ""))
        cps.append(f"{name}不展示时分秒")
    m = re.search(r"与(.+?)同行", d)
    if m:
        cps.append(f"{name}与{m.group(1).strip()}在同一行展示")

    # 预期一句话：把展示属性凝练；无属性则给"正确展示"兜底
    attrs = "、".join([a for a in (display or []) if a.strip()])
    expected = f"{name}按需求正确展示" + (f"：{attrs}" if attrs else "")
    return expected, cps


_KIND_PAGE = {
    "list_card": "列表卡片",
    "detail": "详情页",
    "record_list": "详情页的记录列表",
    "list_columns": "列表",
}


def _norm_platform(s: Any) -> str:
    """端名归一化：去掉所有空白、统一大小写。

    需求原文里的端名和枚举 key 几乎从不逐字相等——"Admin"/"web-admin" 差大小写、"示例 App"/"Android App"
    差一个空格、"移动 App"/"移动端 App" 还少一个字。原来的匹配是大小写敏感的字面包含，这三种全都
    匹配不上。
    """
    return re.sub(r"\s+", "", str(s or "")).casefold()


def _is_subseq(short: str, long: str) -> bool:
    """short 的字符能否按序在 long 中找到（"移动 App" ⊂ "租户module"）。"""
    it = iter(long)
    return all(c in it for c in short)


def _valid_platform(block: dict, platform_keys: list[str]) -> list[str]:
    """把区块声明的端对到枚举 key 上。对不上返回 []（调用方回填"全部确认端"）。

    为什么必须尽力对准：对不上就回填全部确认端，再被 _fanout_by_platform 按端拆成 N 条单端
    用例——步骤里明明写着"进入 示例 App"的用例会被复制出 web-admin / 移动 App 版本，N-1 条是纯废
    用例，还会占执行批次。线上一批 8 个展示区块因此炸成 24 条、其中 16 条端是错的。

    四档匹配，逐档放宽，每档都要求【唯一命中】才采纳，宁可判不出走回填也不硬猜：
    归一化全等 → 归一化包含 → 子序列(容忍"业务模块"少字) → 用 page_path 首段再来一轮。
    """
    def _match(raw: Any) -> list[str]:
        p = _norm_platform(raw)
        if not p:
            return []
        norm = {k: _norm_platform(k) for k in platform_keys}
        exact = [k for k, n in norm.items() if n == p]
        if len(exact) == 1:
            return exact

        def _comparable(n: str) -> bool:
            # 长度相当才算数：短的至少要有长的一半。否则像 platform="商" 这种残缺值会被
            # 硬配给 用户门户(而 移动 App/Android App 同样说得通)——这种猜测正是错端用例的来源，
            # 宁可判不出、走"回填全部确认端"，也不赌一个。
            return min(len(p), len(n)) * 2 >= max(len(p), len(n))

        contain = [k for k, n in norm.items() if (p in n or n in p) and _comparable(n)]
        if len(contain) == 1:
            return contain
        if len(contain) > 1:
            # 多个候选时取长度最接近的，且必须比第二名更接近，否则算歧义不采纳
            contain.sort(key=lambda k: abs(len(norm[k]) - len(p)))
            if abs(len(norm[contain[0]]) - len(p)) < abs(len(norm[contain[1]]) - len(p)):
                return [contain[0]]
            return []
        sub = [k for k, n in norm.items() if _is_subseq(p, n) and _comparable(n)]
        return sub if len(sub) == 1 else []

    # 先信区块自己声明的 platform；它没给(或给了对不上的)就用导航路径首段——
    # 路径首段几乎总是端名("进入 示例 App → 首页 → 待办列表")。
    hit = _match(block.get("platform"))
    if hit:
        return hit
    path = [s for s in (block.get("page_path") or []) if isinstance(s, str) and s.strip()]
    return _match(path[0]) if path else []


def _covered_item(title: str, obj: str, expected: str) -> dict:
    return {
        "name": title, "object": obj, "action": "展示", "expected": expected,
        "scenario_type": "正常路径", "sources": ["requirement"], "risk_tags": [],
    }


# 有"展示逻辑/样式"的字段(值得单独一条)；纯展示无样式的合并成一条清单
_STYLE_KW = (
    "加粗", "标题", "突出", "复制", "标签", "灰", "底部", "同行", "日期", "颜色", "标红", "高亮",
    "结构化原因", "补充说明", *_COMPOSE_KW, *_FALLBACK_KW,
)


def _has_style(f: dict) -> bool:
    d = " ".join(f.get("display") or [])
    return any(k in d for k in _STYLE_KW) or bool(_DATE_RE.search(f.get("example") or ""))


def _nav_prefix(block: dict) -> str:
    """从 page_path 拼「进入 A → B → C，」导航前缀，逐级保留(不丢中间页，如资源中心→资源列表)。
    没给 page_path 则回退空前缀(保持旧行为，步骤只写"查看…"由执行侧自行找入口)。"""
    path = [s.strip() for s in (block.get("page_path") or []) if isinstance(s, str) and s.strip()]
    return ("进入 " + " → ".join(path) + "，") if path else ""


def _nav_precond(block: dict, fallback: str) -> str:
    path = [s.strip() for s in (block.get("page_path") or []) if isinstance(s, str) and s.strip()]
    return f"进入 {' → '.join(path)} 且有数据" if path else fallback


def _rule_text(rule: Any) -> str:
    if isinstance(rule, dict):
        return " ".join(
            str(rule.get(k) or "")
            for k in ("type", "target", "condition", "expected", "raw_text")
        ) + " " + " ".join(str(x) for x in (rule.get("sources") or []))
    return str(rule or "")


def _infer_rule_type(text: str) -> str:
    if "倒序" in text or "倒排" in text:
        return "ordering"
    if "等" in text and any(k in text for k in ("多", "折叠")):
        return "multi_value_ellipsis"
    if any(k in text for k in _COMPOSE_KW) or re.search(r"(取|由|包含).*[+＋]", text):
        return "composition"
    if any(k in text for k in _FALLBACK_KW):
        return "fallback"
    if "映射" in text or ("状态" in text and any(k in text for k in ("文案", "显示为", "展示为"))):
        return "mapping"
    if any(k in text for k in _AGGREGATION_KW):
        return "aggregation"
    if any(k in text for k in ("角色", "权限", "隐藏", "不展示", "仅")):
        return "visibility"
    if "时" in text and any(k in text for k in ("展示", "显示", "隐藏", "置灰")):
        return "conditional_display"
    return "formatting"


def _split_sources(text: str) -> list[str]:
    sources: list[str] = []
    for key in ("无法验机结构化原因", "结构化原因", "补充说明"):
        if key in text and key not in sources:
            sources.append(key)
    if sources:
        return sources
    rhs = text
    m = re.search(r"[=＝](.+)", text)
    if m:
        rhs = m.group(1)
    parts = [p.strip(" ：:。；;，,") for p in re.split(r"[+＋、/]", rhs)]
    return [p for p in parts if p and len(p) <= 20][:4]


def _target_from_text(text: str, fallback: str = "展示字段") -> str:
    m = re.search(r"([\u4e00-\u9fa5A-Za-z0-9_（）()]+)\s*[=＝]", text)
    if m:
        return m.group(1).strip()
    if "原因" in text:
        return "原因"
    m = re.search(r"[“\"']([^“”\"']+)[”\"']", text)
    if m:
        return m.group(1).strip()
    return fallback


def _normalize_rule(block: dict, raw_rule: Any) -> dict:
    text = _rule_text(raw_rule).strip()
    if isinstance(raw_rule, dict):
        rtype = raw_rule.get("type") or _infer_rule_type(text)
        target = (raw_rule.get("target") or _target_from_text(text)).strip()
        sources = [str(x).strip() for x in (raw_rule.get("sources") or []) if str(x).strip()]
        if not sources and rtype == "composition":
            sources = _split_sources(text)
        rule = {
            "type": rtype,
            "target": target or "展示字段",
            "condition": (raw_rule.get("condition") or "").strip(),
            "sources": sources,
            "expected": (raw_rule.get("expected") or "").strip(),
            "raw_text": (raw_rule.get("raw_text") or text).strip(),
        }
    else:
        rtype = _infer_rule_type(text)
        rule = {
            "type": rtype,
            "target": _target_from_text(text),
            "condition": "无法验机时" if "无法验机" in text else "",
            "sources": _split_sources(text) if rtype == "composition" else [],
            "expected": "",
            "raw_text": text,
        }
    identity = "|".join([
        block.get("block") or "",
        rule["type"],
        rule["target"],
        rule.get("condition") or "",
        "+".join(rule.get("sources") or []),
        rule.get("raw_text") or "",
    ])
    rule["rule_id"] = "DR_" + hashlib.sha1(identity.encode("utf-8")).hexdigest()[:12]
    return rule


def _rule_case(block_data: dict, block_name: str, feature: str, page: str, nav: str, plats: list[str],
               priority: str, raw_rule: Any) -> dict | None:
    rule = _normalize_rule(block_data, raw_rule)
    rtype = rule["type"]
    target = rule["target"]
    condition = rule.get("condition") or "满足规则条件时"
    sources = rule.get("sources") or []
    source_text = " + ".join(sources)
    raw_text = rule.get("raw_text") or rule.get("expected") or target

    if rtype == "ordering":
        key = "创建时间"
        mk = re.search(r"按(.+?)倒序", raw_text)
        if mk:
            key = mk.group(1).strip()
        expected = f"{block_name}记录按{key}从新到旧倒序排列"
        action = f"{nav}查看{page if nav else block_name}的记录顺序"
        cps = ["第一条记录时间晚于第二条", "第二条晚于第三条", "列表未出现升序排列"]
        title = f"{block_name}按{key}倒序展示"
    elif rtype == "multi_value_ellipsis":
        expected = "多值时按需求以“xx等”样式折叠展示"
        action = f"{nav}查看{page if nav else block_name}中含多个值的字段展示"
        cps = ["多值字段展示首个值加“等”，如“资源等”", "不逐个平铺全部值"]
        title = f"{block_name}多值时展示“…等”"
    elif rtype == "composition":
        if not source_text:
            source_text = "各来源字段"
        expected = rule.get("expected") or f"{condition}{target}按需求由{source_text}拼装展示"
        action = f"{nav}查看{page if nav else block_name}中{condition}的“{target}”字段"
        cps = [f"{target}字段展示{source_text}拼装后的内容", f"{target}字段不只展示{sources[0] if sources else '单一来源'}"]
        if len(sources) > 1:
            cps.append(f"{target}字段不遗漏{sources[-1]}")
        title = f"{block_name}-{target}拼装展示校验"
    elif rtype == "conditional_display":
        expected = rule.get("expected") or f"{condition}{target}按需求展示"
        action = f"{nav}查看{page if nav else block_name}中{condition}的“{target}”展示"
        cps = [f"当前记录满足条件：{condition}", f"{target}按条件展示结果与需求一致"]
        title = f"{block_name}-{target}条件展示校验"
    elif rtype == "mapping":
        expected = rule.get("expected") or f"{target}按需求映射为对应展示文案"
        action = f"{nav}查看{page if nav else block_name}中“{target}”的展示文案"
        cps = [f"{target}展示为需求定义的映射文案", f"{target}未直接展示后端编码/内部枚举值"]
        title = f"{block_name}-{target}映射展示校验"
    elif rtype == "fallback":
        expected = rule.get("expected") or f"{target}为空或无值时按需求展示缺省/兜底内容"
        action = f"{nav}查看{page if nav else block_name}中“{target}”为空或无值记录"
        cps = [f"{target}为空或无值时出现需求定义的兜底展示", f"{target}不展示为异常空白或原始 null"]
        title = f"{block_name}-{target}空值兜底展示校验"
    elif rtype == "visibility":
        expected = rule.get("expected") or f"{target}按角色/状态/权限规则控制显隐"
        action = f"{nav}查看{page if nav else block_name}中“{target}”的显隐状态"
        cps = [f"满足可见条件时{target}可见", f"满足隐藏条件时{target}不可见或不可操作"]
        title = f"{block_name}-{target}显隐规则校验"
    elif rtype == "aggregation":
        expected = rule.get("expected") or f"{target}按需求计算/统计后展示"
        action = f"{nav}查看{page if nav else block_name}中“{target}”统计展示"
        cps = [f"{target}展示值与来源数据计算结果一致", f"{target}不展示为未计算的原始明细值"]
        title = f"{block_name}-{target}计算展示校验"
    else:
        return None

    ci = _covered_item(f"{block_name}{target}{rtype}展示规则", feature, expected)
    ci["matched_rules"] = [rule["rule_id"]]
    return {
        "title": title,
        "platforms": plats, "priority": priority, "case_type": "ui",
        "preconditions": [_nav_precond(block_data, f"存在可验证{target}展示规则的数据")],
        "steps": [{"seq": 1, "action": action, "expected": expected, "check_points": cps}],
        "expected_result": expected,
        "source_issue_point": feature,
        "covered_items": [ci],
        "sources": ["requirement"],
        "tags": ["display_logic", f"display_rule:{rule['rule_id']}", f"display_rule_type:{rtype}"],
        "reason": f"需求规定{block_name}中{target}存在{rtype}展示逻辑，单独核对",
    }


def _case_text(case: dict) -> str:
    pieces: list[str] = []
    for key in ("title", "expected_result", "reason"):
        pieces.append(str(case.get(key) or ""))
    for tag in case.get("tags") or []:
        pieces.append(str(tag))
    for step in case.get("steps") or []:
        if isinstance(step, dict):
            pieces.extend(str(step.get(k) or "") for k in ("action", "expected"))
            pieces.extend(str(x) for x in (step.get("check_points") or []))
    for ci in case.get("covered_items") or []:
        if isinstance(ci, dict):
            pieces.extend(str(ci.get(k) or "") for k in ("name", "object", "action", "expected"))
            pieces.extend(str(x) for x in (ci.get("matched_rules") or []))
    return " ".join(pieces)


def _business_tokens(text: str) -> list[str]:
    known_terms = [
        "无法验机", "结构化原因", "无法验机结构化原因", "补充说明", "原因", "完成率",
        "状态", "映射", "空值", "缺省", "兜底", "权限", "隐藏", "显隐", "拼装", "拼接", "组合",
    ]
    tokens: list[str] = [term for term in known_terms if term in text]
    for raw in re.split(r"[^\u4e00-\u9fa5A-Za-z0-9_]+", text):
        token = raw.strip()
        if len(token) < 2:
            continue
        if any(noise in token for noise in ("display_rule", "display_logic", "display_rule_type")):
            continue
        tokens.append(token)
    unique: list[str] = []
    for token in tokens:
        if token not in unique:
            unique.append(token)
    return unique


def _case_covers_template(existing: dict, template: dict) -> bool:
    tpl_rules = {
        str(tag).split("display_rule:", 1)[1]
        for tag in (template.get("tags") or [])
        if str(tag).startswith("display_rule:")
    }
    existing_text = _case_text(existing)
    if tpl_rules and any(rule_id in existing_text for rule_id in tpl_rules):
        return True
    tpl_text = _case_text(template)
    unique = [
        token for token in _business_tokens(tpl_text)
        if token not in {"展示", "字段", "需求", "校验", "规则", "页面", "记录", "正确", "列表", "详情页"}
    ]
    # 用目标/条件/来源这些强 token 判重；AI 已生成同规则时通常会包含这些词。
    if len(unique) < 3:
        return False
    hits = sum(1 for token in unique if token in existing_text)
    return hits >= min(4, len(unique))


def append_display_rule_gap_cases(existing_cases: list[dict], template_cases: list[dict]) -> list[dict]:
    """追加模板用例时做一次覆盖审计：展示逻辑规则若已被 AI 覆盖则不重复，未覆盖则补 gap case。"""
    out = list(existing_cases or [])
    for case in template_cases or []:
        is_display_logic = any(str(t) == "display_logic" for t in (case.get("tags") or []))
        if is_display_logic and any(_case_covers_template(existing, case) for existing in out):
            continue
        out.append(case)
    return out


def emit_field_cases(blocks: list[dict], platform_keys: list[str] | None = None,
                     default_platforms: list[str] | None = None, priority: str = "P1") -> list[dict]:
    """把结构化展示字段区块落成用例（纯模板）。返回与 testcase_generator 同形的 case dict 列表。

    规则：有展示逻辑的字段(加粗/可复制/灰标签/日期/同行等)各一条；纯展示无样式的字段合并成一条清单，
    避免用例过碎。端(platforms)优先按区块判定，判不出用 default_platforms(需求确认的涉及端)回填。
    """
    platform_keys = platform_keys or []
    default_platforms = default_platforms or []
    cases: list[dict] = []
    for b in blocks:
        block = (b.get("block") or "展示").strip()
        block_context = " ".join([block] + [str(f.get("name") or "") for f in (b.get("fields") or [])])
        feature = normalize_secondary_feature(block, block_context) or block
        kind = b.get("kind") or "detail"
        page = _KIND_PAGE.get(kind, "页面")
        nav = _nav_prefix(b)   # 「进入 平台 → 资源中心 → 资源列表，」完整路径前缀(逐级不丢中间页)
        plats = _valid_platform(b, platform_keys) or list(default_platforms)
        # 去重同名字段(抽取偶尔重复给同一字段)，避免"存在X字段"出现两次
        _seen: set[str] = set()
        fields = []
        for f in (b.get("fields") or []):
            nm = (f.get("name") or "").strip()
            if nm and nm not in _seen:
                _seen.add(nm)
                fields.append(f)
        styled = [f for f in fields if _has_style(f)]
        plain = [f for f in fields if not _has_style(f)]

        # 1) 有展示逻辑的字段：各一条（样式/位置/格式可核对）
        for f in styled:
            name = f["name"].strip()
            expected, cps = _humanize(name, f.get("display") or [], f.get("example"))
            cases.append({
                "title": f"{block}-{name}展示校验",
                "platforms": plats, "priority": priority, "case_type": "ui",
                "preconditions": [_nav_precond(b, f"进入{block}所在{page}且有数据")],
                "steps": [{"seq": 1, "action": f"{nav}查看{page}中任一条的“{name}”字段", "expected": expected, "check_points": cps}],
                "expected_result": expected,
                "source_issue_point": feature,
                "covered_items": [_covered_item(f"{block}{name}展示正确", feature, expected)],
                "sources": ["requirement"], "reason": f"需求规定{block}中{name}的展示样式，单独核对",
            })

        # 2) 纯展示无样式的字段：合并一条清单（不要碎）
        if plain:
            cps = [f"存在“{f['name']}”字段且展示对应值" for f in plain]
            noun = "记录" if kind == "record_list" else "页面"
            field_list = "、".join(f["name"] for f in plain)
            # 预期里【逐个列出字段名】，不要写"完整展示14个字段"这种没法核对的话
            expected = f"{block}完整展示以下{len(plain)}个字段：{field_list}"
            cases.append({
                "title": f"{block}展示指定字段清单",
                "platforms": plats, "priority": priority, "case_type": "ui",
                "preconditions": [_nav_precond(b, f"存在可查看{block}的数据")],
                "steps": [{"seq": 1, "action": f"{nav}查看{page if nav else block}{noun}的全部字段", "expected": expected, "check_points": cps}],
                "expected_result": expected,
                "source_issue_point": feature,
                "covered_items": [_covered_item(f"{block}字段完整展示", feature, expected)],
                "sources": ["requirement"], "reason": f"需求列出{block}字段清单，合并核对展示完整性",
            })

        # 区块级展示规则：排序倒序 / 多值"等" / 拼接 / 条件 / 映射 / 兜底 / 显隐 / 聚合
        for rule in (b.get("rules") or []):
            case = _rule_case(b, block, feature, page, nav, plats, priority, rule)
            if case:
                cases.append(case)
    return cases
