"""把用例里写的"先新建一个X"自动变成数据要求，并匹配到能造出它的场景（方案 §11 Recommender）。

要解决的真实问题：用例常把前置数据写成散文塞在 expected 里（线上 TC-ZN-0494 的步骤1 预期是
"先新建一个异常工单，填好所有的必填后提交，再在列表页面操作对目标工单发起完成"）。执行端
只能让 AI 在单步 20 次动作预算内临场把整套创建流程做完——做不完就判"无法验证"，而这条用例
真正要验的其实是【立即完成】，不是【新建】。

这里做的是：认出这类前置需求 → 匹配已发布场景（按对象 + 目标状态）→ 建一条 AUTO 数据要求。
执行前置就会确定性地把数据造好，用例只验它自己的目标。
"""
from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import select

from app.models import DataScenario, TestDataRequirement

logger = logging.getLogger(__name__)

# 【强意图】= 明确要求"事先就得有这条数据"。
# 不能把光秃秃的"新建/创建"算进来：那多半是用例【要验的动作本身】(如"新建异常工单必填校验")，
# 替它把数据造好等于把用例架空。所以只认"存在/已有/先新建"这类前置口吻。
_INTENT_RE = re.compile(r"(存在|已存在|已有|已创建|先新建|先创建|需要一[个条张笔]|准备好|前置数据)")
_STATE_RE = re.compile(r"(待处理|已完成|待审批|审批中|已作废|待支付|已支付|待确认|生效中|已关闭)")


def _precondition_text(case: Any) -> str:
    """取【前置性质】的文字：preconditions + 第一步的预期。

    不取后续步骤——那里的"新建"往往就是用例要验的动作本身，替它造好等于把用例架空。
    """
    texts = [str(p) for p in (getattr(case, "preconditions", None) or [])]
    steps = getattr(case, "steps", None) or []
    if steps and isinstance(steps[0], dict):
        texts.append(str(steps[0].get("expected") or ""))
    return " ".join(t for t in texts if t)


def _aliases(data_type: str) -> list[str]:
    """对象名的可匹配写法：全称 + 去掉修饰词后的核心名词。

    需要它是因为用例里很少写全称：场景对象叫"异常工单"，前置里写的却是"待处理工单"、
    "目标工单"。只认全称会漏掉一大半；但核心名词很宽泛，所以调用方必须校验【唯一命中】，
    多个场景都能匹配上就算歧义、不绑。
    """
    t = (data_type or "").strip()
    out = [t] if t else []
    for n in (4, 3, 2):
        if len(t) > n:
            out.append(t[-n:])
    return out


def detect_data_need(case: Any, known_types: list[str] | None = None) -> dict | None:
    """认出用例需要"一条什么状态的什么数据"。认不出返回 None。

    对象【不做自由抽取】，而是拿【已有场景的对象名】去文本里对——自由抽正则贪心起来会抠出
    "待处理工单及当前"这种碎片，据此匹配只会乱绑。已发布场景数量有限，反向匹配既稳又可解释。
    """
    blob = _precondition_text(case)
    if not blob:
        return None
    types = [t for t in (known_types or []) if t]
    # 【意图词与对象必须在同一子句里】否则会张冠李戴：
    # "当前合同项目存在已启用的一级异常原因。用户具有新建异常工单权限。"——这里的"存在"
    # 说的是【异常原因】，若拿标题或全文里的"异常工单"去配，就会给一条"新建校验"用例
    # 平白造一条工单当前置。所以按句切开，只在含意图词的那一句里找对象。
    hits: dict[str, str] = {}
    for clause in re.split(r"[。；;|\n]+", blob):
        if not _INTENT_RE.search(clause):
            continue
        for t in types:
            for a in _aliases(t):
                if a and a in clause:
                    hits.setdefault(t, a)
                    break
    if len(hits) != 1:                 # 0 个=认不出；多个=歧义，都不绑
        return None
    obj = next(iter(hits))
    s = _STATE_RE.search(blob)
    return {"object": obj, "matched_as": hits[obj],
            "state": s.group(1) if s else None, "source_text": blob[:160]}


async def active_scenarios(db) -> list[DataScenario]:
    return list((await db.execute(select(DataScenario).where(DataScenario.status == "ACTIVE"))).scalars().all())


async def recommend_scenario(db, need: dict, env: str = "sit",
                             rows: list[DataScenario] | None = None) -> DataScenario | None:
    """按"要什么对象 + 什么状态"匹配已发布场景。匹配不上返回 None（宁可不绑，也不乱绑）。"""
    if not need:
        return None
    rows = rows if rows is not None else await active_scenarios(db)
    obj = need["object"]
    cands = [s for s in rows if obj == (s.data_type or "") or obj in (s.data_type or "")]
    if not cands:
        return None
    want = need.get("state")
    if want:
        # 场景的 guarantees 里声明能保证该状态的优先
        exact = [s for s in cands
                 if any(str(v.get("status") or "") == want for v in (s.guarantees or {}).values())]
        if exact:
            return exact[0]
        # 有 guarantees 但状态对不上 → 不硬用，交给人
        if any(s.guarantees for s in cands):
            return None
    return cands[0]


async def auto_bind_requirement(db, case: Any, env: str = "sit") -> dict | None:
    """给用例自动挂一条 AUTO 数据要求。已有要求则不动。返回绑定结果或 None。

    标 source=auto + review_status=pending_review：自动绑定是【建议】，让人在评审页能看到
    并纠正，而不是悄悄改了用例的执行前提。
    """
    case_id = getattr(case, "case_id", None)
    if not case_id:
        return None
    exist = (await db.execute(select(TestDataRequirement).where(
        TestDataRequirement.case_id == case_id))).scalars().all()
    if exist:
        return None

    rows = await active_scenarios(db)
    need = detect_data_need(case, [s.data_type for s in rows])
    if not need:
        return None
    scn = await recommend_scenario(db, need, env, rows)
    if scn is None:
        logger.info("用例 %s 需要「%s%s」但没有可用场景", case_id, need.get("state") or "", need["object"])
        return None

    alias = re.sub(r"[^\w]", "", need["object"]) or "data"
    out_key = next(iter(scn.outputs or {}), None) or scn.data_type
    row = TestDataRequirement(
        case_id=case_id, alias=alias, data_type=scn.data_type,
        target_state={"status": need["state"]} if need.get("state") else None,
        strategy="AUTO", scenario_id=scn.scenario_id, scenario_version=scn.version,
        output_key=out_key, required=True, source="auto", confidence=0.6,
        review_status="pending_review",
    )
    db.add(row)
    await db.commit()
    logger.info("用例 %s 自动绑定造数场景 %s（需求：%s%s）",
                case_id, scn.scenario_id, need.get("state") or "", need["object"])
    return {"case_id": case_id, "alias": alias, "scenario_id": scn.scenario_id,
            "need": need, "output_key": out_key}
