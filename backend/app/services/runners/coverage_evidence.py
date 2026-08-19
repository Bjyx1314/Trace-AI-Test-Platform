"""覆盖项级执行证据 helper（方案 12.1），web/android runner 共享，防两端漂移。

职责：
1. covered_items_hint(): 把用例覆盖项拼成提示词块下发给判定 AI，要求判定时在 checks 里带 item_id；
2. build_checked_points(): 从执行后的 ui_trace 聚合出覆盖项级 checked_points（item_id 匹配优先，
   无匹配时按整体 verdict 兜底），附证据(截图+步骤原因)；
3. build_actual_visited_pages(): 从 page_captures 提取实际访问页面；
4. build_actual_api_calls(): 从采集的接口调用提取。

设计取舍(MVP)：覆盖项与步骤非硬绑定。AI 若在 checks 标了 item_id 则精确归属；否则按用例整体
执行结论回填每个覆盖项(covered/failed)，保证覆盖矩阵有据可依，不追求逐步精确。
"""
from __future__ import annotations

import re


def covered_items_hint(covered_items: list | None) -> str:
    """生成下发给判定 AI 的覆盖项提示块（含 item_id，供其在 checks 里回标）。"""
    items = covered_items or []
    if not items:
        return ""
    lines = ["\n本用例需验证的覆盖项(判定时请在 checks 每条尽量带上对应 item_id)："]
    for ci in items:
        iid = ci.get("item_id") or ""
        name = ci.get("name") or ci.get("object") or ""
        exp = ci.get("expected") or ""
        lines.append(f"- [{iid}] {name}" + (f"（预期：{exp}）" if exp else ""))
    return "\n".join(lines)


def _norm(s: str) -> str:
    """归一化文本用于比对：去空白/标点，便于把 AI 复述过的覆盖项文案认回来。"""
    return re.sub(r"[\s，。；、（）()\[\]【】:：]+", "", str(s or "")).lower()


_CI_PREFIX = re.compile(r"^\s*\[\s*CI[_\-]", re.I)


def coverage_check_matcher(covered_items: list | None):
    """返回 is_coverage_check(check) —— 判断一条 AI 回来的 check 是【覆盖项】还是【本步锚点】。

    为什么需要：覆盖项是用例级的（如"允许提交并生成任务"），下发给判定 AI 后它会当成一条
    锚点回标。若让它参与单步 verdict 翻转，后面步骤才可能满足的覆盖项就会把前面步骤误判成
    blocked（真实案例：步骤1"进入新建表单"自身锚点已 ok，却被用例级覆盖项拖成"无法验证"）。

    三种认法，任一命中即算覆盖项：显式 item_id / 文本带 [CI_xxx] 前缀 / 文案与覆盖项名称或
    预期高度重合（AI 常丢掉 item_id 只复述文案）。
    """
    ids = {str(ci.get("item_id")) for ci in (covered_items or []) if ci.get("item_id")}
    texts = []
    for ci in covered_items or []:
        # 只比 name/expected：object 是页面名(如"资产盘点新建页")，本步锚点里天然会出现，
        # 拿它比对会把"当前页面为资产盘点新建页"这种正常锚点误判成覆盖项。
        for k in ("name", "expected"):
            t = _norm(ci.get(k) or "")
            if len(t) >= 6:          # 太短的文案(如"创建成功")会误伤本步锚点，不参与比对
                texts.append(t)

    def is_coverage_check(check: dict) -> bool:
        if str(check.get("item_id") or "") in ids and check.get("item_id"):
            return True
        point = str(check.get("point") or "")
        if _CI_PREFIX.match(point):
            return True
        p = _norm(point)
        return any(t in p for t in texts) if p else False

    return is_coverage_check


def step_own_failed_checks(checks_result: list | None, covered_items: list | None) -> list[dict]:
    """筛出【本步锚点】里未满足的 check（剔除用例级覆盖项），供 verdict 翻转/字段回捞使用。"""
    is_cov = coverage_check_matcher(covered_items)
    return [c for c in (checks_result or []) if not c.get("ok") and not is_cov(c)]


def _case_overall_status(ui_trace: list | None) -> str:
    """由步骤 verdict 汇总用例整体覆盖状态：全 pass→covered，有 fail→failed，其余(blocked)→failed。"""
    trace = ui_trace or []
    if not trace:
        return "not_covered"
    if all(st.get("verdict") == "pass" for st in trace):
        return "covered"
    return "failed"


def _verdict_to_status(v: str | None) -> str:
    return {"pass": "passed", "fail": "failed", "blocked": "blocked"}.get(v or "", "not_checked")


def build_checked_points(covered_items: list | None, ui_trace: list | None) -> list:
    """聚合覆盖项级证据。

    - 若某步 checks 里带了 item_id → 该覆盖项按此 check 的 ok 定状态，证据取该步截图+point；
    - 否则该覆盖项按用例整体状态兜底，证据取最后一个有截图的步骤。
    """
    items = covered_items or []
    trace = ui_trace or []
    if not items:
        return []

    # 建 item_id → (ok, evidence, shot, seq) 索引（来自 checks 的显式标注）
    explicit: dict[str, dict] = {}
    for st in trace:
        for c in st.get("checks") or []:
            iid = c.get("item_id")
            if iid:
                explicit[iid] = {
                    "ok": bool(c.get("ok")),
                    "point": str(c.get("point", "")),
                    "shot": st.get("shot"),
                    "seq": st.get("seq"),
                }

    overall = _case_overall_status(trace)
    last_shot = next((st.get("shot") for st in reversed(trace) if st.get("shot")), None)
    last_seq = next((st.get("seq") for st in reversed(trace) if st.get("shot")), None)
    reasons = "；".join(f"步骤{st.get('seq')}{st.get('reason', '')}" for st in trace if st.get("verdict") != "pass")[:300]

    result: list[dict] = []
    for ci in items:
        iid = ci.get("item_id")
        name = ci.get("name") or ci.get("object") or ""
        if iid and iid in explicit:
            e = explicit[iid]
            result.append({
                "item_id": iid, "covered_item_name": name,
                "status": "passed" if e["ok"] else "failed",
                "evidence": e["point"] or (name + (" 验证通过" if e["ok"] else " 验证未通过")),
                "screenshot_url": e["shot"], "step_seq": e["seq"],
            })
        else:
            status = {"covered": "passed", "failed": "failed", "not_covered": "not_checked"}[overall]
            result.append({
                "item_id": iid, "covered_item_name": name,
                "status": status,
                "evidence": (name + " 验证通过") if status == "passed" else (reasons or f"{name} 未通过"),
                "screenshot_url": last_shot, "step_seq": last_seq,
            })
    return result


def build_actual_visited_pages(page_captures: list | None) -> list | None:
    if not page_captures:
        return None
    pages = []
    for cap in page_captures:
        pages.append({"url": cap.get("url"), "page_name": cap.get("page_name")})
    return pages or None


def build_actual_api_calls(api_calls: list | None) -> list | None:
    """归一化实际接口调用为 [{method,url,status,request_body?,response_body?}]。

    写操作的报文要【原样带上】：造数能力就是靠这些真实报文沉淀出来的，丢了报文就只剩
    "打过哪个接口"、造不出数。报文在采集侧已脱敏截断。
    """
    if not api_calls:
        return None
    out = []
    for a in api_calls:
        if not isinstance(a, dict):
            continue
        rec = {"method": a.get("method"), "url": a.get("url"), "status": a.get("status")}
        for k in ("request_body", "response_body", "query"):
            if a.get(k):
                rec[k] = a[k]
        out.append(rec)
    return out or None


def coverage_status_from_checked_points(checked_points: list | None) -> dict[str, str]:
    """由 checked_points 反推每个 item_id 的 coverage_status，供回填 TestCase.covered_items。"""
    mapping: dict[str, str] = {}
    for cp in checked_points or []:
        iid = cp.get("item_id")
        if not iid:
            continue
        st = cp.get("status")
        mapping[iid] = "covered" if st == "passed" else ("failed" if st == "failed" else mapping.get(iid, "not_covered"))
    return mapping
