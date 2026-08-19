"""Agent 2: 用例生成 — 根据问题点清单直接生成新schema形状的TestCase字段集。"""
from __future__ import annotations
import json
import uuid
from .base_agent import BaseAgent
from .requirement_analyst import normalize_secondary_feature

SYSTEM = """你是一名专业测试工程师。请【综合需求文档原文 与 确认后的需求分析内容】生成测试用例列表。

【最重要：目标级原子化 —— 一条用例只验证一个目标】
- 一条用例只覆盖【一个验证目标】(一个功能点/一条业务规则/一个场景)，对应一个明确、可判定的总预期；
- 判定拆分准则：【同一页面/同一流程、服务同一目标】的若干小检查 → 放在【同一条用例的多个步骤】里；
  【不同目标/不同页面/不同入口/不同模式】→ 必须拆成【不同用例】。
- 【同页多目标·免重复导航的合并例外】即便是【不同验证目标】，只要它们【落在同一入口/同一页面/同一控件上、且都不需要"提交/创建/删除/改后端状态"、不消耗记录】，就【合并成一条用例的多个步骤】——步1 走完那段共用导航，之后每个目标各一步，并【各自挂一个 covered_item】保持可追溯。
  · 例：同一个"预约到场时间"选择器上分别验"分钟只有00/30""可选任意历史时间""可选任意未来时间" → 合并成一条(共用"进报修→处理报修→现场处理→打开选择器"的导航，再分 3 步各验一点)，不要拆 3 条；同一详情页上分别看几个只读字段的特殊展示，也合并成多步。
  · 判据=【要不要提交/改状态】：若其中任一目标需要【提交/创建/删除/改后端状态】、或需要【各自独立的前置数据】→ 该目标仍【独立成条】(否则多条会互相脏状态)。
  · 理由：把"同页只读/不提交的多目标"拆成多条，会让执行端为每条重复整段长导航(还各自冷启动)，纯浪费。
- 例：
  * "新建任务必填校验"是一个目标 → 一条用例，步骤里逐项检查(缺项目→拦截、缺资源→拦截、都填→可提交)，不要每个必填项拆一条；
  * 但"列表展示"与"新建并提交"是两个目标、两个页面 → 拆成两条用例；
  * 正常流程 / 边界 / 异常 通常是不同目标 → 各自成条(但若都在同一控件上只读核对、不提交，按上面的合并例外并成多步)。
- 严禁把多个不相关目标塞进一条(例如"进列表+新建+必填+提交+日历可见"要按目标拆成多条)。
- 步骤(steps)只服务本条这一个目标：必要的导航/前置 + 针对该目标的核对，精简清晰。
- **【功能所在路径必须写进步骤，别让执行去猜】**：需求原文里通常已写明每个功能的【所在端/平台 + 页面/菜单路径】——
  常见于表格(如列： 端/平台 | 页面/模块 | 说明 )、或明写"路径：A-B-C"。生成用例时【必须把该功能的完整入口路径提取出来、写进第一步(导航步)】，
  让执行按路径导航，而不是给个笼统的"进入XX页"让执行自己找。例如：
  * 需求写「移动 App | 资源列表 | 新增菜单」→ 第一步写"打开 Android App → 工作台 → 资源列表"，不要只写"进入资源列表"；
  * 需求写「管理平台 | 资源中心」→ 写"进入 管理平台 → 资源中心 → 资源详情"，不要只写"进入详情页"；
  * 需求写「路径：Android App-资源-回收列表页-扫码入口」→ 就按这条路径逐级导航。
  路径信息在需求哪一端/哪个页面下，就用哪个端(platforms)和路径；缺路径执行会进错子系统/找不到入口而失败。多级路径按下面的原子化拆多步。
- **步骤必须原子化**：一步只做「一次导航」或「一次校验」，禁止把"进入X并打开Y并查看Z"塞进一步。多层导航要拆多步(如：步1 进入列表页 → 步2 从列表点开某条的详情页 → 步3 在详情页查看某字段)，否则执行时会一步做不完卡住。
- **展示校验必须锁定页面、区分易混值**：校验字段展示时，action 与 check_points 要写清「在哪个页面」——列表页的某"列" 和 详情页的某"字段"是两码事，不能在列表页当详情页校验(锚点要含"当前在设备明细页/详情页"这类页面判定)。当字段值可能与公司名/租户名/相邻字段撞脸时，check_points 必须写明"校验的是该字段本身(如 仓库=仓库名称，例'XX仓'，不是公司名/租户名)"，避免执行时 AI 就近抓错值去校验。
- **【单端铁律：一条用例只能在一个端上执行，绝不跨端】**：一条用例的【所有步骤必须落在同一个端/平台】——要么全 PC/web-admin 网页、要么全 App(Android App)、要么全小程序、要么全接口。执行器【按端只选一个运行器(PC 优先于 App)】：把 web-admin 步骤和 App 步骤混在同一条里，App 步骤会被丢到 PC 浏览器里跑而【必然失败】。需要验证"跨端一致/对比"(如"APP 与 web-admin 的完成率一致""PC 建的单在 App 能看到")时，【必须拆成各端各一条】：每端一条、各自【在本端】核对同一规则(如 PC 一条核对完成率=已盘点+无法盘点、App 一条同样在 App 上核对该完成率)，用两条 P 相同、标题标明端的用例覆盖，【不要】写一条"先进 web-admin 再进 App"的跨端用例。platforms 字段也只填该条实际执行的那一个端。

【覆盖要求】
- 以需求文档原文为基础，覆盖其描述的所有功能点、业务规则、正常/边界/异常场景(用多条原子用例覆盖)；
- 充分落实"需求分析确认结果"及各问题点已确认的澄清结论(confirmation_points)，这些规则都要有对应原子用例；
- 不要遗漏原文中虽未被列入问题点、但属于需求范围的内容。宁可用例多而细，也不要一条混多点。
- **问题点清单只是提示、不是范围上限**：必须回到需求文档原文逐段扫描，原文里有、问题点没提到的内容(尤其字段清单/展示规格)照样要生成用例，不能只围着问题点扩写。
- **【只测需求写了的，绝不臆造】**：用例的操作与预期【只能来自需求原文(及已确认的 confirmation_points)明确写出的内容】。绝不发明原文【没有】的具体规则/字段/数值/约束/流程作为预期或校验点——例：原文只说"新建业务单据生成对应数量的资源编码"、没说编号规则，就【不要】写"资源编码按连续流水/统一流水产生"这种预期。某条断言无法从需求原文或确认结论直接得出时，宁可不写该断言；若整条用例的核心目标只能靠"原文没有的规则"才成立，就【根本不要生成这条用例】。确认点(confirmation_points)只有【已确认为真】的才当规则用；把"待确认的疑问"当成"既定规则"去写预期也是臆造。
  【但边界/等价类/异常用例照常生成，这不算臆造】：需求写了输入/表单/字段/操作时，围绕它的【边界值、等价类、空值/超长/多值/必填/非法输入、异常与失败流程】等标准测试维度的用例【要正常生成】——这是对"原文已有功能"的正常覆盖，不是无中生有。只是当某个边界的【确切预期(如具体上限数字、精确错误文案)】原文没给时，预期写成【合理的通用结果】(如"应拦截并给出错误提示""应有长度限制不允许提交")，不要编一个具体数值/规则；确切阈值该由确认点澄清。真正禁止的只有一种：【无中生有一条原文根本没有的业务规则/功能】当成既定事实去断言(如连续流水编号)。
- **字段"正常展示"清单不用你写，但展示逻辑不能漏**：需求里「列表/卡片/详情/使用记录/表格列」这类字段展示清单的**正常展示校验**（每个字段展示对不对、样式/位置/格式/排序），由平台的确定性模板统一补齐，你**不要**再逐字段写这类用例，避免重复。你要专注：**流程/行为/状态流转、输入表单的校验与边界(空值/超长/多值/必填)、以及产品特有的展示逻辑**。凡展示值由【条件、拼接、派生计算、状态/枚举映射、角色/状态显隐、空值兜底】决定，都属于业务规则，必须覆盖；例如"质保过期是否标红""某字段对某角色隐藏""数据为空时的缺省展示""原因由结构化原因+补充说明拼装"。纯"字段展示正确"不要写。
- **【列表的搜索/筛选/排序/分页 必须由你生成用例——它们是"行为"不是"字段展示"】**：确定性模板只补"字段展示正确"，【不覆盖】列表上的交互行为。原文写了列表支持【搜索框/筛选项/排序/分页】的，你【必须】各自生成用例，别因为"字段展示不用写"就把搜索筛选也跳过：
  · 搜索：原文"支持按单号/服务工程师搜索"→ 生成"输入单号关键词→列表只剩匹配项""按服务工程师搜索命中对应任务""清空搜索→恢复全部"等用例；
  · 筛选：原文"任务状态/盘点类型/服务网点多选筛选""盘点单号模糊搜索"→ 各筛选项生成用例(单选生效、多选组合、清空复位)；
  · 排序/分页：原文写了"按X倒序""分页"→ 各生成一条。
  这些是需求范围内、可执行的交互，漏掉就是漏测。

每个用例包含：
- title: 点明本条验证的【那一个点】
- modules(从给定模块枚举选), platforms(从给定端枚举选)：判端以本条用例【实际验证的对象】为准——
  只有当用例直接验证接口(后端API调用、接口入参/出参/字段、请求/响应报文、协议)时才标「接口」端；
  纯页面/交互/前端校验只标对应 PC/App/小程序端，不要给纯UI用例标接口端。
- priority: P0/P1/P2
- preconditions: 前置条件列表
- steps: [{seq, action, expected, check_points}] —— 只服务本条验证点；expected 必须填写，不能为空
  · check_points: 该步【可核对的判定锚点】，每条是一个具体、可见、客观的事实(界面上该出现/不该出现的文案、元素、状态、字段、数值)，
    用于执行时逐条核对，避免凭感觉判过。例如["页面标题显示『任务记录报表』","存在『导出』按钮","不出现旧文案『任务明细』"]。
    锚点要具体可观察，不要写"展示正常"这类无法核对的话。
- expected_result: 本条单一的总预期；case_type: 只填 ui 或 api 两种【小写，与"用例类型"枚举一致】——仅当本条就是验证后端接口(platforms 含接口端、校验 API 入参/出参/报文)时填 api；其余所有界面/功能/交互/流程/展示用例【一律填 ui】。不要写「功能」「UI」「界面」等其它写法，也不要生成性能/安全/兼容性/其他
- source_issue_point: 该用例对应哪个问题点就填其 issue_id——【必须按内容匹配，不要按顺序/就近/随手填】：
  拿本条用例真正验证的【功能/业务对象/动作】去逐一比对"问题点清单"里每个 issue_point 的 description 与 feature，
  选【主题真正吻合】的那个。例：一条"web-admin导入已占用资源失败"的用例，要挑 description/feature 属于"批量导入"的问题点，
  【绝不能】挑"新建盘点任务"的。若没有任何问题点的内容与本条吻合(如本条来自原文其他段落)，就【留空】，
  【宁可留空也不要】硬塞一个不相干的 issue_id——填错会让"增量重生成去重"按错误功能点比对而误判重复。
- tags: 可选标签列表

【覆盖项 covered_items —— 质量判断单元，必填至少 1 条】
- 每条用例必须产出 1 个及以上 covered_items，表达"这条用例具体验证了哪些质量点"。
- 每个覆盖项结构：{name, object, action, expected, scenario_type, risk_tags}
  · object: 被验证的对象(如"优惠券""订单金额""登录表单")；
    object 只是质量点归属对象/分组线索，不是用例拆分粒度；用例仍必须按上面的「目标级原子化」拆分。
    object 要按页面/入口/业务对象归一，不要细到字段名、搜索条件或筛选项。同一列表页的搜索、筛选、字段展示、排序、分页等，object 都填该列表页名称。
    例如"资源列表搜索/筛选/字段展示/卡片字段"的 object 都填"资源列表"；"设备列表位置搜索条件/位置字段展示"的 object 都填"设备列表"。
  · action: 对该对象的动作(如"叠加使用""提交""必填校验")；
  · expected: 可判定的预期结果(如"订单金额按叠加规则正确")；
  · name: 该覆盖项的简洁命名(通常= object+action+expected 的凝练，如"多张优惠券叠加后订单金额正确")；
  · scenario_type: 从「正常路径 / 边界 / 异常」三选一；
  · risk_tags: 风险标签(如"金额""优惠券""幂等""权限")，由你判断打标，可空。
- 覆盖项与步骤不同：步骤是操作序列，覆盖项是质量断言。一条用例的多个步骤可共同支撑一个覆盖项，也可分别验证多个覆盖项。
- sources: 本用例覆盖项来源，需求侧生成固定填 ["requirement"]。
- reason: 一句话说明本条为何生成、验证了需求/代码的哪个变化点。
调用工具输出结果，字段为cases数组。优先保证"目标级原子化(一条一个目标，同屏同目标可多步、跨目标才拆条；但同页同控件、只读/不提交的多目标合并成一条多步、免重复导航)"与对需求的完整覆盖。"""


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
            "case_type": {"type": "string", "enum": ["ui", "api"]},
            "tags": {"type": ["array", "null"], "items": {"type": "string"}},
            "covered_items": {
                "type": "array",
                "description": "质量判断单元，至少 1 条。每项是本用例验证的一个质量点",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "覆盖项简洁命名"},
                        "object": {"type": "string", "description": "被验证对象"},
                        "action": {"type": "string", "description": "对该对象的动作"},
                        "expected": {"type": "string", "description": "可判定的预期结果"},
                        "scenario_type": {"type": "string", "enum": ["正常路径", "边界", "异常"]},
                        "risk_tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "object", "action", "expected", "scenario_type"],
                },
            },
            "sources": {"type": "array", "items": {"type": "string"}, "description": "覆盖项来源，需求侧固定 [\"requirement\"]"},
            "reason": {"type": "string", "description": "本条为何生成/验证了哪个变化点"},
        },
        "required": [
            "title", "modules", "platforms", "priority", "preconditions",
            "steps", "expected_result", "case_type", "covered_items",
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
            "case_type": "ui",
            "tags": None,
            "covered_items": [{
                "name": f"{ip.get('description', 'Mock')[:20]} 验证通过",
                "object": ip.get("module") or "功能点",
                "action": "执行操作",
                "expected": "结果符合预期",
                "scenario_type": "正常路径",
                "risk_tags": [],
            }],
            "sources": ["requirement"],
            "reason": "Mock 生成：来自需求问题点",
        })
    return {"cases": cases}


def normalize_covered_items(
    items: list | None,
    default_sources: list[str] | None = None,
    source_issue_id: str | None = None,
) -> list[dict]:
    """平台侧归一化覆盖项：赋 item_id、补默认 coverage_status/sources，容忍 LLM 缺字段。

    item_id 由平台生成（CI_{短uuid}），不让 LLM 编号，避免跨批冲突。
    """
    normalized: list[dict] = []
    for raw in (items or []):
        if not isinstance(raw, dict):
            continue
        name = (raw.get("name") or raw.get("object") or "").strip()
        if not name and not raw.get("action"):
            continue
        srcs = list(raw.get("sources") or default_sources or ["requirement"])
        obj = normalize_secondary_feature(
            raw.get("object"),
            " ".join(str(raw.get(k) or "") for k in ("name", "expected", "action")),
        )
        item = {
            "item_id": raw.get("item_id") or f"CI_{uuid.uuid4().hex[:8]}",
            "name": name or f"{raw.get('object', '')}{raw.get('action', '')}".strip(),
            "object": obj or raw.get("object"),
            "action": raw.get("action"),
            "expected": raw.get("expected"),
            "scenario_type": raw.get("scenario_type") or "正常路径",
            "risk_tags": list(raw.get("risk_tags") or []),
            "sources": srcs,
            "matched_rules": list(raw.get("matched_rules") or []),
            "priority": raw.get("priority"),
            "reason": raw.get("reason"),
            "coverage_status": raw.get("coverage_status") or "not_covered",
        }
        if source_issue_id:
            item["source_issue_id"] = source_issue_id
        normalized.append(item)
    return normalized


def _normalize_case_type(v) -> str:
    """归一到「用例类型」枚举键 ui/api，容忍模型/导入写成 功能/UI/接口/大小写等。"""
    raw = str(v or "").strip()
    if raw.lower() == "api" or "接口" in raw:
        return "api"
    return "ui"  # 功能/UI/界面/空一律归 ui（枚举只有 ui、api 两键）


def _apply_covered_item_normalization(cases: list[dict]) -> list[dict]:
    """对生成结果批量归一化 covered_items 并回填用例级 sources/risk_tags 汇总。"""
    for c in cases:
        c["case_type"] = _normalize_case_type(c.get("case_type"))
        c["covered_items"] = normalize_covered_items(
            c.get("covered_items"),
            default_sources=c.get("sources") or ["requirement"],
            source_issue_id=c.get("source_issue_point"),
        )
        # 用例级 sources = 各覆盖项来源并集（保序去重）
        merged_sources: list[str] = list(c.get("sources") or [])
        merged_tags: list[str] = list(c.get("risk_tags") or [])
        for ci in c["covered_items"]:
            for s in ci.get("sources") or []:
                if s not in merged_sources:
                    merged_sources.append(s)
            for t in ci.get("risk_tags") or []:
                if t not in merged_tags:
                    merged_tags.append(t)
        c["sources"] = merged_sources or ["requirement"]
        c["risk_tags"] = merged_tags
    return cases


# ── 按步骤重新生成判定锚点（用例编辑时"锚点跟着步骤走"）──────────────────────
_CHECKPOINTS_SYSTEM = (
    "你是测试专家。给定用例标题和它每个步骤的【操作】与【预期】，为【每一个步骤】生成 2~4 条"
    "「判定锚点(check_points)」——每条是一个具体、可见、客观的事实(界面上该出现/不该出现的文案、元素、"
    "状态、字段、数值)，供执行时逐条核对，避免凭感觉判过。要求：\n"
    "1) 锚点必须紧扣该步的操作与预期，不跨步、不臆造该步之外的内容；\n"
    "2) 锚点要具体可观察，禁止'展示正常/符合需求/正确'这类无法核对的话；\n"
    "3) 若操作/预期指明了页面(如'在设备明细页'/'在列表页')或具体值，锚点要含页面判定与该具体值；\n"
    "4) 返回的 steps 数组顺序、条数与我给的步骤严格一一对应。"
)
_CHECKPOINTS_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"check_points": {"type": "array", "items": {"type": "string"}}},
                "required": ["check_points"],
            },
        }
    },
    "required": ["steps"],
}


async def gen_check_points(title: str, steps: list[dict]) -> list[list[str]]:
    """按当前步骤(操作/预期)重新生成每步的判定锚点。返回与 steps 等长的锚点列表。失败则返回原锚点/空。"""
    from app.agents.llm import get_provider
    if not steps:
        return []
    provider = get_provider()
    lines = "\n".join(
        f"步骤{i + 1}：操作={s.get('action', '') or ''}；预期={s.get('expected', '') or ''}"
        for i, s in enumerate(steps)
    )
    user = f"用例标题：{title}\n\n步骤清单：\n{lines}\n\n为每个步骤生成判定锚点，steps 顺序与上面步骤一一对应。"
    try:
        out = await provider.tool(_CHECKPOINTS_SYSTEM, user, "submit_check_points", _CHECKPOINTS_SCHEMA, 2048)
    except Exception:
        return [list(s.get("check_points") or []) for s in steps]
    res = (out or {}).get("steps") or []
    result: list[list[str]] = []
    for i, s in enumerate(steps):
        cps = res[i].get("check_points") if i < len(res) and isinstance(res[i], dict) else None
        result.append([str(x) for x in (cps or []) if str(x).strip()] or list(s.get("check_points") or []))
    return result


def _build_generate_prompt(
    requirement_title: str, requirement_content: str, analysis_confirmation: str | None,
    issue_points: list[dict], modules_text: str, platforms_text: str,
    experience_hints: str | None = None,
) -> str:
    return (
        f"需求: {requirement_title}\n\n"
        f"需求文档内容:\n{requirement_content}\n\n"
        + (f"需求分析确认结果:\n{analysis_confirmation}\n\n" if analysis_confirmation else "")
        + (f"{experience_hints}\n\n" if (experience_hints or "").strip() else "")
        + f"问题点清单:\n{json.dumps(issue_points, ensure_ascii=False, indent=2)}\n\n"
        f"可选模块枚举:\n{modules_text}\n\n"
        f"可选端枚举:\n{platforms_text}"
    )


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
        experience_hints: str | None = None,
    ) -> list[dict]:
        module_keys = [m["key"] for m in modules]
        platform_keys = [p["key"] for p in platforms]

        if self.use_mock:
            return _apply_covered_item_normalization(_mock_cases(issue_points, module_keys, platform_keys)["cases"])

        tool_schema = _build_tool_schema(module_keys, platform_keys)
        modules_text = "\n".join(f"- {m['key']}: {m['label']}" for m in modules)
        platforms_text = "\n".join(f"- {p['key']}: {p['label']}" for p in platforms)
        prompt = _build_generate_prompt(
            requirement_title, requirement_content, analysis_confirmation,
            issue_points, modules_text, platforms_text, experience_hints,
        )
        result = await self.call_claude_tool_multimodal(
            SYSTEM, prompt, images or [], "submit_test_cases", tool_schema,
            max_tokens=8192, mock_result=_mock_cases(issue_points, module_keys, platform_keys),
        )
        return _apply_covered_item_normalization(result.get("cases", []))

    async def backfill_covered_items(
        self, title: str, steps: list | None, expected_result: str | None,
    ) -> list[dict]:
        """存量用例覆盖项回填（方案 10.4-②）：从既有 title/steps/expected 反推覆盖项。

        sources=["backfill"] 低置信标注，测试可在 Review 页修正。回填是阶段四复用检索生效前提。
        """
        backfill_schema = {
            "type": "object",
            "properties": {
                "covered_items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string"},
                            "object": {"type": "string"},
                            "action": {"type": "string"},
                            "expected": {"type": "string"},
                            "scenario_type": {"type": "string", "enum": ["正常路径", "边界", "异常"]},
                            "risk_tags": {"type": "array", "items": {"type": "string"}},
                        },
                        "required": ["name", "object", "action", "expected", "scenario_type"],
                    },
                }
            },
            "required": ["covered_items"],
        }
        steps_text = json.dumps(steps or [], ensure_ascii=False)
        prompt = (
            f"以下是一条既有测试用例，请提取它实际验证了哪些质量点（covered_items）：\n\n"
            f"标题: {title}\n步骤: {steps_text}\n总预期: {expected_result or ''}\n\n"
            f"每个覆盖项含 object(被验证对象)/action(动作)/expected(可判定预期)/name(命名)/scenario_type/risk_tags。"
        )
        sys_prompt = "你是测试专家，从既有用例的标题、步骤、预期中提取它验证的质量点。只输出结构化覆盖项。"
        mock = {"covered_items": [{
            "name": (title or "用例")[:30], "object": "功能点", "action": "验证",
            "expected": expected_result or "结果符合预期", "scenario_type": "正常路径", "risk_tags": [],
        }]}
        if self.use_mock:
            return normalize_covered_items(mock["covered_items"], default_sources=["backfill"])
        result = await self.call_claude_tool_multimodal(
            sys_prompt, prompt, [], "submit_covered_items", backfill_schema,
            max_tokens=2048, mock_result=mock,
        )
        return normalize_covered_items(result.get("covered_items", []), default_sources=["backfill"])
