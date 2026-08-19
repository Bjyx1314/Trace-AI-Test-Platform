"""从执行采集到的真实报文里【自动沉淀造数能力】（方案 §23 的自动化补齐）。

思路：一条用例在真实系统上把"新建"走通时，浏览器发出的那个请求就是一份【可重放的造数动作】。
执行端已经把这些报文采下来了（见 web_agent_runner 的 _on_response），这里负责把它们变成
DataCapability，并自动试运行认证。跑得越多，能力越新——外键失效、接口改版都会被下一次成功
执行自动覆盖掉，不需要人工维护。

只自动激活【新增类】：delete/discard/cancel 这类破坏性操作照样沉淀，但留 DRAFT 等人确认，
自动重放一个删除接口去"验证它能删"风险不对等。
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select

from app.models import DataCapability

logger = logging.getLogger(__name__)

_CREATE_RE = re.compile(r"/(create|save|submit|add|insert|new)(/|$|\?)", re.I)
_UPDATE_RE = re.compile(r"/(update|edit|modify)(/|$|\?)", re.I)
_DESTRUCTIVE_RE = re.compile(r"/(delete|remove|discard|cancel|void|abolish|complete|close)(/|$|\?)", re.I)
_QUERY_RE = re.compile(r"/(page|list|query|search|detail|get)(/|$|\?)", re.I)

# 幂等键/随机串：原样重放会被服务端当重复请求拒掉，必须每次重新生成。
# 线上真实样本：requestId = "ad-1786699852120-ce2howr5"（前缀-13位毫秒时间戳-随机串）
_DYN_NAME_RE = re.compile(r"(requestid|request_id|nonce|idempot|traceid|trace_id|uuid|serialno)", re.I)
_DYN_VALUE_RE = re.compile(r"^(.*?)(\d{13})([-_])([0-9a-zA-Z]{5,})$")


def auto_inputs() -> dict:
    """重放时自动生成的动态值，供 {{__ts__}} / {{__rand__}} 占位。"""
    return {
        "__ts__": str(int(time.time() * 1000)),
        "__rand__": "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=8)),
        "__now__": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "__today__": datetime.now().strftime("%Y-%m-%d"),
    }


def templatize_dynamic(body: Any) -> tuple[Any, list[str]]:
    """把报文里的幂等键换成占位符，返回 (新报文, 被模板化的字段名)。

    不做这一步，重放 create 就会撞上服务端的幂等校验——这是"抓到的报文能不能直接当造数动作"
    的分水岭。
    """
    touched: list[str] = []

    def walk(node, key=""):
        if isinstance(node, dict):
            return {k: walk(v, k) for k, v in node.items()}
        if isinstance(node, list):
            return [walk(v, key) for v in node]
        if isinstance(node, str) and _DYN_NAME_RE.search(key or ""):
            m = _DYN_VALUE_RE.match(node)
            if m:
                touched.append(key)
                return f"{m.group(1)}{{{{__ts__}}}}{m.group(3)}{{{{__rand__}}}}"
            if len(node) >= 16:                    # 纯随机串(uuid 之类)整体换掉
                touched.append(key)
                return "{{__ts__}}{{__rand__}}"
        return node

    return walk(body), touched


def infer_output_extract(response_body: str | None) -> dict:
    """从响应里认出"刚造出来的东西的标识"。

    线上真实样本：{"errCode":"0","data":"YC202608141123608"} —— data 【直接就是单号字符串】，
    不是对象。所以不能想当然写 $.data.id。
    """
    try:
        j = json.loads(response_body or "")
    except Exception:  # noqa: BLE001
        return {}
    d = j.get("data") if isinstance(j, dict) else None
    if isinstance(d, str) and d.strip():
        return {"id": "$.data"}
    if isinstance(d, dict):
        for k in ("code", "id", "orderNo", "orderId", "no", "billNo", "declareCode"):
            if d.get(k):
                return {"id": f"$.data.{k}"}
        for k, v in d.items():                     # 兜底：第一个标量
            if isinstance(v, (str, int)) and str(v).strip():
                return {"id": f"$.data.{k}"}
    return {}


def classify(url: str, method: str) -> str | None:
    """写操作分类：create / update / destructive / query。非业务写操作返回 None。"""
    if (method or "").upper() not in ("POST", "PUT", "PATCH", "DELETE"):
        return None
    if _DESTRUCTIVE_RE.search(url) or (method or "").upper() == "DELETE":
        return "destructive"
    if _CREATE_RE.search(url):
        return "create"
    if _UPDATE_RE.search(url):
        return "update"
    if _QUERY_RE.search(url):
        return "query"      # 这类接口常用 POST 传条件，本质是查询：用作后置校验器
    return None


def _succeeded(call: dict) -> bool:
    """这次调用是不是真的成功了（HTTP 200 还不够，业务码也要对）。"""
    if int(call.get("status") or 0) >= 300:
        return False
    try:
        j = json.loads(call.get("response_body") or "")
    except Exception:  # noqa: BLE001
        return True                                  # 没有响应体就只看 HTTP 码
    if isinstance(j, dict) and "errCode" in j:
        return str(j.get("errCode")) in ("0", "200")
    return True


def _capability_id(url: str, kind: str) -> str:
    """由 URL 生成稳定的能力 ID，如 abnormalDeclare.create。"""
    path = re.sub(r"^https?://[^/]+", "", url or "").split("?")[0].rstrip("/")
    segs = [s for s in path.split("/") if s and not s.isdigit() and not re.fullmatch(r"[vV]\d+", s)]
    tail = segs[-1] if segs else "unknown"
    obj = segs[-2] if len(segs) >= 2 else "api"
    return f"{obj}.{tail}" if tail.lower() != kind else f"{obj}.{kind}"


def _service_and_path(url: str, env: str = "sit") -> tuple[str | None, str]:
    """把 URL 拆成 (service 名, 相对该 service base 的路径)。

    路径【必须相对 base】：多数 service 的 base 自带前缀(如 demo-web → https://…/demo-web)，
    若把 /demo-web/xxx 整条存进来，执行时会拼成 /demo-web/demo-web/xxx 打不通——而且 HTTP 不抛错，
    只是抽不到值，表现为静默失败。
    """
    full = (url or "").split("?")[0]
    m = re.match(r"^(https?://[^/]+)", full)
    if not m:
        return None, full
    host = m.group(1)
    try:
        from app.services.frameworks.interface_env import host_map
        cands = []
        for name, base in (host_map(env) or {}).items():
            if not base:
                continue
            b = base.rstrip("/")
            # 域名相同即候选（忽略 http/https 差异：同一站点两种写法都在表里）
            if re.sub(r"^https?://", "", b).split("/")[0] == re.sub(r"^https?://", "", host):
                cands.append((name, b))
        # 取 base 最长的那个：它的路径前缀最具体，剥掉后剩下的才是真正的业务路径
        for name, b in sorted(cands, key=lambda x: -len(x[1])):
            tail = re.sub(r"^https?://", "", b)
            prefix = "/" + tail.split("/", 1)[1] if "/" in tail else ""
            path = full[len(host):]
            if prefix and path.startswith(prefix):
                return name, path[len(prefix):] or "/"
            if not prefix:
                return name, path
        return host, full[len(host):]
    except Exception:  # noqa: BLE001
        return host, full[len(host):]


def build_capability(call: dict, case: Any, env: str = "sit") -> dict | None:
    """把一次成功的写操作调用变成一份能力定义（DRAFT）。"""
    url, method = call.get("url") or "", (call.get("method") or "POST").upper()
    kind = classify(url, method)
    if kind is None or not _succeeded(call):
        return None
    try:
        body = json.loads(call.get("request_body") or "null")
    except Exception:  # noqa: BLE001
        body = call.get("request_body")
    body, dynamic = templatize_dynamic(body)
    service, path = _service_and_path(url, env)

    # URL 上的查询参数同样是入参：作废/完成这类接口就是 ?code=YCxxx、请求体为空。
    # 把业务标识换成占位符，重放时由调用方按 {id}/{code} 传入，而不是永远作废同一条。
    params: dict = {}
    if call.get("query"):
        from urllib.parse import parse_qsl
        for k, v in parse_qsl(str(call["query"]), keep_blank_values=True):
            params[k] = "{{%s}}" % k if re.search(r"(code|id|no|num)$", k, re.I) else v
        params, dyn2 = templatize_dynamic(params)
        dynamic += dyn2

    return {
        "capability_id": _capability_id(url, kind),
        "version": "auto",
        "name": f"{getattr(case, 'title', '') or ''}｜{kind}"[:110],
        "provider_type": "HTTP",
        "business_domain": ",".join(getattr(case, "modules", None) or []) or None,
        "executor_ref": f"exec://{getattr(case, 'case_id', '')}",
        "parameter_mapping": {
            "service": service,
            "auth": {"service": service, "account_profile": "test"},
            "request": {"method": method, "url": path, "body": body,
                        **({"params": params} if params else {})},
            "_dynamic_fields": dynamic,          # 记录哪些字段被模板化了，便于人工复核
        },
        "output_extract": infer_output_extract(call.get("response_body")),
        "side_effects": [kind] if kind != "query" else [],
        "idempotency_supported": bool(dynamic),  # 有幂等键 → 可安全重放
        "supported_environments": [env],
        "_kind": kind,
    }


async def _cleanup_created(db, cap: DataCapability, produced: dict, env: str) -> dict:
    """用配对的反向能力把刚造出来的对象清掉。清不掉就【如实记录】，绝不假装清理过。

    环境被造数淹掉是这类自动化最常见的后遗症，所以宁可留一条"待人工清理"的记录，
    也不要静默跳过。
    """
    ident = next((v for v in (produced or {}).values() if v), None)
    if not ident:
        return {"done": False, "reason": "没拿到对象标识，无法定位要清理什么"}
    if not cap.cleanup_capability_id:
        return {"done": False, "reason": "尚无配对的清理能力", "leftover": ident}
    cleaner = (await db.execute(select(DataCapability).where(
        DataCapability.capability_id == cap.cleanup_capability_id))).scalars().first()
    if cleaner is None:
        return {"done": False, "reason": f"清理能力 {cap.cleanup_capability_id} 不存在", "leftover": ident}
    try:
        from .engine import _exec_capability
        # 反向能力的入参统一按 {id/code} 喂：报文里对应字段用 {{id}}/{{code}} 占位
        await _exec_capability(cleaner, {"id": ident, "code": ident}, env)
        return {"done": True, "cleaned": ident, "by": cleaner.capability_id}
    except Exception as e:  # noqa: BLE001
        return {"done": False, "reason": str(e)[:200], "leftover": ident}


async def verify_capability(db, cap: DataCapability, env: str = "sit") -> dict:
    """试运行认证：真重放一次，成功才置 ACTIVE+APPROVED。结果写进 last_verify。

    【破坏性能力永不自动认证】——自动重放一个"删除/作废/完成"接口去证明它能删，风险与收益
    不对等。这类留 DRAFT，由人确认后再启用。
    """
    from .engine import _exec_capability

    kind = (cap.side_effects or [None])[0]
    if kind in ("destructive",):
        cap.last_verify = {"ok": False, "skipped": "破坏性操作不自动认证，请人工确认后启用",
                           "at": datetime.now().isoformat(timespec="seconds")}
        await db.commit()
        return {"ok": False, "skipped": True, "reason": "destructive"}

    rec: dict
    try:
        out = await _exec_capability(cap, {}, env)
        cap.status, cap.approval_status = "ACTIVE", "APPROVED"
        rec = {"ok": True, "at": datetime.now().isoformat(timespec="seconds"),
               "env": env, "sample_output": out}
        # 认证造出来的数据【当场清掉】：认证只为证明能力可用，留下的是纯脏数据。
        # 用例前置造的数据不在此列——那是用例要用的，由 retention_hours 管。
        if cap.side_effects:
            rec["cleanup"] = await _cleanup_created(db, cap, out, env)
    except Exception as e:  # noqa: BLE001 认证失败不是异常路径，是正常结论
        cap.status, cap.approval_status = "DRAFT", "PENDING"
        rec = {"ok": False, "at": datetime.now().isoformat(timespec="seconds"),
               "env": env, "error": str(e)[:400]}
    cap.last_verify = rec
    cap.updated_at = datetime.now()
    await db.commit()
    return rec


def should_auto_verify(cap: DataCapability) -> bool:
    """哪些能力可以在执行流程里【顺带】自动认证。

    只放行查询类：它们只读、零副作用、重放不产生脏数据。create 类每认证一次就在环境里
    真造一条数据，必须由人点一下(或后续接定时/配额)，否则每跑一次用例就多一条垃圾数据。
    """
    if cap.status == "ACTIVE" and cap.approval_status == "APPROVED":
        return False
    return not (cap.side_effects or [])          # 查询类 side_effects 为空


async def pair_cleanup_capabilities(db) -> int:
    """给 create 能力配对反向清理能力（同一业务对象下的 discard/delete/cancel）。

    造数会在环境里留真实数据，没有反向能力就只能靠人清。这里只建立配对关系，
    真正的清理执行由 cleanup_mode/retention_hours 驱动。
    """
    rows = (await db.execute(select(DataCapability))).scalars().all()
    by_obj: dict[str, dict[str, DataCapability]] = {}
    for r in rows:
        obj, _, act = (r.capability_id or "").rpartition(".")
        if obj:
            by_obj.setdefault(obj, {})[act.lower()] = r
    n = 0
    for obj, acts in by_obj.items():
        creator = acts.get("create") or acts.get("save") or acts.get("add")
        cleaner = acts.get("discard") or acts.get("delete") or acts.get("cancel") or acts.get("remove")
        if creator is not None and cleaner is not None and creator.cleanup_capability_id != cleaner.capability_id:
            creator.cleanup_capability_id = cleaner.capability_id
            creator.cleanup_mode = "DELETE"
            n += 1
    if n:
        await db.commit()
    return n


def _input_size(pm: dict | None) -> int:
    """一份能力定义带了多少入参（body 字段数 + query 参数数）。用于判断刷新是升级还是降级。"""
    req = ((pm or {}).get("request") or {})
    body = req.get("body")
    n = len(body) if isinstance(body, dict) else (1 if body else 0)
    return n + len(req.get("params") or {})


def _would_downgrade(row: DataCapability, spec: dict) -> bool:
    """新样本会不会把一份【已认证可用】的定义刷成更差的。"""
    if row.status != "ACTIVE" or row.approval_status != "APPROVED":
        return False
    return _input_size(spec.get("parameter_mapping")) < _input_size(row.parameter_mapping)


async def sediment_from_result(db, result: Any, case: Any, env: str = "sit") -> list[dict]:
    """从一条执行结果里沉淀能力。返回 [{capability_id, kind, action}]。

    只要那次【调用本身成功】就沉淀，不要求整条用例通过——用例常因为后续断言失败，
    但中间那次新建是真成功了的，那份报文照样有价值（本次 TC-ZN-0490 正是如此）。
    """
    made: list[dict] = []
    for call in (result.actual_api_calls or []):
        if not isinstance(call, dict) or not call.get("request_body"):
            continue
        spec = build_capability(call, case, env)
        if not spec:
            continue
        kind = spec.pop("_kind")
        cid, ver = spec["capability_id"], spec["version"]
        row = (await db.execute(select(DataCapability).where(
            DataCapability.capability_id == cid, DataCapability.version == ver))).scalars().first()
        if row is None:
            row = DataCapability(**spec, status="DRAFT", approval_status="PENDING")
            db.add(row)
            action = "新建"
        elif _would_downgrade(row, spec):
            # 【自愈不能反向降级】新样本的入参比现有定义还少时不覆盖：实测一次执行采到的
            # discard 报文是空的(当时还没采集 URL 查询参数)，把原本带 params 的可用定义刷成了
            # 空壳，能力还挂着 ACTIVE，清理随即失效。宁可保留旧定义，等一次更完整的样本。
            action = "跳过(新样本入参更少，保留原定义)"
            logger.info("能力 %s 跳过刷新：新样本无入参，现有定义已认证可用", cid)
        else:
            # 已存在 → 用最新的真实报文刷新（外键/字段变了也能自愈），但不动已认证状态
            for k, v in spec.items():
                setattr(row, k, v)
            row.updated_at = datetime.now()
            action = "刷新"
        made.append({"capability_id": cid, "kind": kind, "action": action})
    if made:
        await db.commit()
        logger.info("从用例 %s 沉淀造数能力：%s", getattr(case, "case_id", "?"), made)
        await pair_cleanup_capabilities(db)
    return made


async def build_scenario(db, obj: str, env: str = "sit") -> Any | None:
    """把同一业务对象的 create + 查询能力自动串成一个可用场景。

    场景 = 造(create) + 验(query 后置校验)。guarantees 取【认证时实际观察到的状态】，
    而不是拍脑袋写——Recommender 就是按它来匹配"用例要什么状态的数据"。
    """
    from app.models import DataScenario

    caps = {c.capability_id.rpartition(".")[2].lower(): c for c in (await db.execute(
        select(DataCapability).where(DataCapability.capability_id.like(f"{obj}.%")))).scalars().all()}
    creator = caps.get("create") or caps.get("save") or caps.get("add")
    query = caps.get("page") or caps.get("list") or caps.get("query")
    if creator is None:
        return None

    # 状态从 create 的认证产物 + 查询能力实际返回里取；取不到就不写 guarantees(宁缺勿编)
    state = None
    body = ((creator.parameter_mapping or {}).get("request") or {}).get("body") or {}
    sample = ((creator.last_verify or {}).get("sample_output") or {}).get("id")
    if query is not None and sample:
        try:
            from .engine import _exec_capability
            q = dict(query.parameter_mapping or {})
            got = await _exec_capability(query, {}, env)
            state = (got or {}).get("statusName")
        except Exception:  # noqa: BLE001 查不到就不写保证
            state = None

    scenario_id = f"{obj}.auto"
    wf = [{"use": creator.capability_id, "version": creator.version, "output": "obj", "input": {}}]
    # 除了对象标识，还要把【去哪儿能找到它】一并导出——只给一个单号，执行端的 AI 不知道
    # 该进哪个项目/资源下去找，前置造了也白造。这些定位字段在响应里没有，只能取自请求报文。
    locators = {k: v for k, v in (body or {}).items()
                if isinstance(v, str) and v and re.search(
                    r"(signedProjectCode|signedProjectName|projectCode|projectName|itemName|merchantName)", k)}
    outputs = {obj: {"id": "${obj.id}", **locators}}
    guarantees = {obj: {"status": state}} if state else {}

    row = (await db.execute(select(DataScenario).where(
        DataScenario.scenario_id == scenario_id, DataScenario.version == "auto"))).scalars().first()
    data = dict(scenario_id=scenario_id, version="auto", name=f"{obj} 自动造数（来自执行沉淀）",
                data_type=obj, workflow=wf, outputs=outputs, guarantees=guarantees,
                supported_environments=[env])
    if row is None:
        row = DataScenario(**data, status="DRAFT")
        db.add(row)
    else:
        for k, v in data.items():
            setattr(row, k, v)
        row.updated_at = datetime.now()
    # 造数能力已认证 → 场景可直接发布；否则留 DRAFT
    if creator.status == "ACTIVE" and creator.approval_status == "APPROVED":
        row.status = "ACTIVE"
    await db.commit()
    await db.refresh(row)
    return row


async def sediment_and_verify(db, result: Any, case: Any, env: str = "sit") -> dict:
    """执行流程里调用的入口：沉淀 + 顺带认证只读能力。写操作能力留待人工/后续认证。"""
    made = await sediment_from_result(db, result, case, env)
    verified = []
    for m in made:
        cap = (await db.execute(select(DataCapability).where(
            DataCapability.capability_id == m["capability_id"],
            DataCapability.version == "auto"))).scalars().first()
        if cap is not None and should_auto_verify(cap):
            rec = await verify_capability(db, cap, env)
            verified.append({"capability_id": cap.capability_id, "ok": rec.get("ok")})
    return {"sedimented": made, "auto_verified": verified}
