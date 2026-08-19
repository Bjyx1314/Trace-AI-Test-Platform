"""执行前置：把用例的数据要求解析成 ExecutionContext，并注入步骤（方案 §7.2/§9.6，MVP-0）。

MVP-0 只走 MANUAL 路径（manual_values 直填）：不建 DataPreparationTask、不走 SETUP_* 状态机、
无 Validator——最小闭环先把"数据变量注进步骤/凭证注进登录"跑通。缺必填值即 setup_error。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.models import TestDataRequirement
from . import injection


@dataclass
class PrepResult:
    has_requirements: bool = False            # 该用例是否挂了数据要求
    ok: bool = True                           # 是否可继续执行（无未解析占位符/无缺必填）
    steps_override: list[dict] | None = None  # 注入后的步骤（供 runner 用；None=不改）
    script_override: str | None = None        # 注入后的脚本（脚本型接口用例用；None=不改）
    variables: dict[str, Any] = field(default_factory=dict)
    credentials: dict[str, dict] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)   # 未能解析的占位符
    error_message: str | None = None
    trace: list[dict] = field(default_factory=list)       # AUTO 编排造数轨迹


async def prepare_case(db, case, environment: str = "sit") -> PrepResult:
    """为单条用例做数据前置：加载数据要求 → 生成变量（MANUAL 直填 / AUTO 编排造数）→ 注入步骤。

    返回 PrepResult。ok=False 时调用方应把该用例记 setup_error 并跳过执行。
    无数据要求且步骤里也没有 `${}` 占位符时，has_requirements=False，走原有流程零影响。
    """
    case_id = getattr(case, "case_id", None) or getattr(case, "id", None)
    steps = getattr(case, "steps", None) or []
    # 脚本型接口用例的数据也走同一套占位协议：脚本正文里的 ${别名.字段} 一并扫描/注入，
    # 否则脚本用例只能把账号/订单号写死，编排造出来的数据根本喂不进去。
    script = getattr(case, "script", None)
    placeholders = sorted(set(injection.scan_placeholders(steps)) | set(injection.scan_text_placeholders(script)))

    reqs = []
    if case_id:
        reqs = (await db.execute(
            select(TestDataRequirement).where(TestDataRequirement.case_id == case_id)
        )).scalars().all()

    if not reqs and not placeholders:
        return PrepResult(has_requirements=False)

    # 1) MANUAL：人工直填值 → 变量/凭证
    manual_reqs = [r for r in reqs if (getattr(r, "strategy", "MANUAL") or "MANUAL").upper() != "AUTO"]
    variables, credentials = injection.build_variables(manual_reqs)

    # 2) AUTO：绑定场景 → 编排引擎确定性造数 → 合并变量（方案 §11/§12/§13）
    auto_errors: list[str] = []
    trace: list[dict] = []
    for r in reqs:
        if (getattr(r, "strategy", "") or "").upper() != "AUTO":
            continue
        sid, sver = getattr(r, "scenario_id", None), getattr(r, "scenario_version", None)
        if not sid:
            auto_errors.append(f"要求 {r.alias} 标了 AUTO 但未绑定场景")
            continue
        from app.models import DataScenario
        sq = select(DataScenario).where(DataScenario.scenario_id == sid, DataScenario.status == "ACTIVE")
        if sver:
            sq = sq.where(DataScenario.version == sver)
        scenario = (await db.execute(sq.order_by(DataScenario.updated_at.desc()))).scalars().first()
        if scenario is None:
            auto_errors.append(f"要求 {r.alias} 绑定的场景 {sid} 未发布(ACTIVE)")
            continue
        from .engine import run_scenario
        sr = await run_scenario(db, scenario, r, environment)
        trace.append({"alias": r.alias, "scenario": sid, "ok": sr.ok, "error": sr.error, "steps": sr.trace})
        if not sr.ok:
            auto_errors.append(f"要求 {r.alias} 自动造数失败：{sr.error}")
            continue
        variables.update(sr.variables)

    # 3) 注入步骤 + 脚本
    injected, unresolved = injection.inject_steps(steps, variables)

    # AUTO 造数成功后，把"数据已备好"明确写进第一步——否则 AI 照着原文里的"先新建一个…"
    # 还是会自己去建一遍，白烧步数，前置也就白造了（线上 TC-ZN-0494 正是被这句话拖住的）。
    prepared = [t for t in trace if t.get("ok")]
    if prepared and injected:
        # 标识与"去哪儿找"分开写：只丢一个单号，AI 不知道该进哪个项目，前置造了也白造。
        _LOC_HINT = ("signedProjectCode", "projectCode", "signedProjectName",
                     "projectName", "itemName", "merchantName")
        parts, locs = [], []
        for t in prepared:
            fields = {k.split(".", 1)[-1]: v for k, v in variables.items()
                      if k.startswith(f"{t['alias']}.")}
            ident = "、".join(f"{k}:{v}" for k, v in fields.items() if k not in _LOC_HINT)
            if ident:
                parts.append(f"{t['alias']}={ident}")
            locs += [f"{k}={v}" for k, v in fields.items() if k in _LOC_HINT]
        made = "；".join(parts)
        if made:
            where = (f"它在：{'、'.join(dict.fromkeys(locs))}。别去别的项目里找。" if locs else "")
            # 措辞要克制：说"按单号定位"会被理解成"点开这条记录的详情"，而多数操作
            # (作废/完成等)本来就在【列表行】上——实测 AI 因此点进详情页，然后在里面
            # 找不到按钮空滚到步数耗尽。所以只说"找到它所在的行"，是否进详情听步骤的。
            # 措辞要克制：说"按单号定位"会被理解成"点开这条记录的详情"，而多数操作
            # (作废/完成等)本来就在【列表行】上。
            # 注：曾进一步加强过措辞（明令禁止点开链接、并指明行内操作位置），但那版同样
            # 【未经真实执行验证】就和另一处改动一起上线，之后用例反而更差，已退回本版
            # （本版在真实执行中验证过：能找到前置数据并在列表行上完成操作）。
            note = (f"【前置数据已备好，不要再手工新建】{made}。{where}"
                    f"在列表里找到它所在的行即可；是否需要点开详情，以下面的步骤描述为准。")
            first = dict(injected[0])
            # 提示放在步骤【之后】：先让 AI 读到这一步真正要做什么，再读数据说明，
            # 避免一大段前置说明喧宾夺主。
            first["action"] = f"{first.get('action', '')}\n（{note}）"
            injected = [first] + injected[1:]
    script_injected = None
    if script and "${" in str(script):
        script_injected, miss = injection.inject_text(str(script), variables)
        unresolved = sorted(set(unresolved) | set(miss))

    res = PrepResult(
        has_requirements=bool(reqs) or bool(placeholders),
        steps_override=injected,
        script_override=script_injected,
        variables=variables,
        credentials=credentials,
        unresolved=unresolved,
    )
    res.trace = trace
    problems = list(auto_errors)
    if unresolved:
        problems.append(
            "步骤数据变量未解析 —— " + "、".join("${%s}" % u for u in unresolved)
            + "（MANUAL 请在『数据要求』填值；AUTO 请检查场景/能力是否已认证发布）"
        )
    if problems:
        res.ok = False
        res.error_message = "数据未准备好：" + "；".join(problems)
    return res
