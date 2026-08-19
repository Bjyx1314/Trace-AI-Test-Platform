"""数据编排执行引擎（方案 §11/§12/§13/§18，MVP-1 AUTO 路径）。

把一条 strategy=AUTO 的数据要求，按它绑定的场景确定性地造出数据：
  Resolver（场景→计划，${} 确定性解析）→ Orchestration（顺序跑【已认证】能力、抽取输出）
  → Validator（postconditions 校验目标状态真达成）→ 按 output_key 映射回要求的 alias 变量。

Provider（能力执行器）可插拔：本增量落地 MOCK（确定性，自测/异常态注入）与 HTTP（复用 api_runner
的 _subst/_join_url/_resolve_path 与环境 base_url 解析）。API_CASE（复用整条 api 用例）为后续。

运行时【零 AI、零猜测】：只引用 ACTIVE+APPROVED 能力；解析不出的输入/找不到的能力 → 判失败不硬造。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select

from app.models import DataCapability, DataScenario
from . import injection

_WHOLE = re.compile(r"^\$\{([\w.\-一-鿿]+)\}$")

# 造数能力的登录 token 缓存：{(service, appid, phone, env): (token_headers, 过期时间戳)}
# 一个场景常有多步都打同一个服务，逐步重登既慢又可能触发风控/验证码次数限制。
_TOKEN_TTL_SEC = 600
_token_cache: dict[tuple, tuple[dict, float]] = {}


async def _auth_headers(auth: dict, environment: str) -> dict:
    """按能力声明的 auth 取登录态请求头（复用接口自动化的 SMS 登录 + X-Auth-Token 口径）。

    auth 结构与接口用例 api_spec.auth 一致：{"service": ..., "appid": ..., "account": {...}}。
    十分钟内同一 (service, appid, 账号, 环境) 复用同一 token。
    """
    from app.services.runners.api_runner import _login_token_header

    acc = auth.get("account") or {}
    key = (auth.get("service"), str(auth.get("appid") or ""),
           str(acc.get("phone") or ""), str(auth.get("account_profile") or ""), environment)
    hit = _token_cache.get(key)
    now = time.monotonic()
    if hit and hit[1] > now:
        return dict(hit[0])
    headers = await _login_token_header(auth, environment)
    _token_cache[key] = (dict(headers), now + _TOKEN_TTL_SEC)
    return dict(headers)


@dataclass
class ScenarioResult:
    ok: bool = True
    variables: dict[str, Any] = field(default_factory=dict)   # {alias.field: value}，注入用
    trace: list[dict] = field(default_factory=list)
    error: str | None = None


def _resolve_val(v: Any, ctx: dict, unresolved: list[str]) -> Any:
    """把 ${name.field} 按 ctx 解析。整值占位符保留原类型；部分占位符做字符串替换。"""
    if isinstance(v, str):
        m = _WHOLE.match(v.strip())
        if m:
            key = m.group(1)
            if key in ctx and ctx[key] is not None:
                return ctx[key]
            unresolved.append(key)
            return v
        out, miss = injection.inject_text(v, ctx)
        unresolved.extend(miss)
        return out
    if isinstance(v, dict):
        return {k: _resolve_val(x, ctx, unresolved) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve_val(x, ctx, unresolved) for x in v]
    return v


async def _load_capability(db, cap_id: str, version: str | None):
    """加载【ACTIVE+APPROVED】能力。未指定版本取最新 ACTIVE 的。找不到/未认证 → None。"""
    q = select(DataCapability).where(
        DataCapability.capability_id == cap_id,
        DataCapability.status == "ACTIVE",
        DataCapability.approval_status == "APPROVED",
    )
    if version:
        q = q.where(DataCapability.version == version)
    q = q.order_by(DataCapability.updated_at.desc())
    return (await db.execute(q)).scalars().first()


async def _exec_capability(cap: DataCapability, inputs: dict, environment: str) -> dict:
    """执行一个能力，返回抽取后的输出 {field: value}。按 provider_type 分派。"""
    pm = cap.parameter_mapping or {}
    ptype = (cap.provider_type or "").upper()

    if ptype == "MOCK":
        # 确定性：mock_output 里的 {{input}} 用本步已解析输入填充
        from app.services.runners.api_runner import _subst
        return _subst(pm.get("mock_output") or {}, None, inputs) or {}

    if ptype in ("HTTP", "TEST_API", "API_CASE", "QUERY"):
        # 复用 api_runner 的请求/抽取机制，保持与平台接口处理一致
        import httpx
        from app.services.runners.api_runner import _subst, _join_url, _resolve_path
        from app.services.frameworks.interface_env import resolve_service_base_url
        req = pm.get("request") or {}
        # 自动沉淀出来的报文里，幂等键被模板化成 {{__ts__}}/{{__rand__}}——每次重放都要
        # 重新生成，否则服务端按重复请求拒掉。这些自动值优先级低于步骤自己传的 input。
        from .sediment import auto_inputs
        inputs = {**auto_inputs(), **(inputs or {})}
        base_url = resolve_service_base_url(pm.get("service"), environment or "sit")
        # 造数接口同样要登录态：声明 auth 就先换 token，再并进本步 headers（显式 headers 优先）。
        auth = pm.get("auth") or {}
        auth_headers = await _auth_headers(auth, environment or "sit") if auth else {}
        method = str(_subst(req.get("method") or "GET", base_url, inputs)).upper()
        url = _join_url(_subst(req.get("url") or req.get("path") or "", base_url, inputs), base_url)
        headers = {**auth_headers, **(_subst(req.get("headers") or {}, base_url, inputs) or {})} or None
        params = _subst(req.get("params") or {}, base_url, inputs) or None
        body = _subst(req.get("body"), base_url, inputs) if req.get("body") is not None else None
        async with httpx.AsyncClient(timeout=cap.timeout_seconds or 30, verify=False, follow_redirects=True) as client:
            resp = await client.request(method, url, headers=headers, params=params,
                                        json=body if isinstance(body, (dict, list)) else None,
                                        content=body if isinstance(body, str) else None)
            try:
                data = resp.json()
            except Exception:
                data = None
        # 业务码不对/HTTP 出错要【显式抛出】：只把抽不到的字段填 None 会让调用方以为造数成功，
        # 实际什么都没造出来(路径拼错时 HTTP 不抛错，仅返回 404 页面，表现就是静默失败)。
        if resp.status_code >= 300:
            raise ValueError(f"HTTP {resp.status_code} {method} {url}：{(resp.text or '')[:160]}")
        if isinstance(data, dict) and "errCode" in data and str(data.get("errCode")) not in ("0", "200"):
            raise ValueError(f"业务失败 errCode={data.get('errCode')} {data.get('message')}")
        out = {}
        for field_name, path in (cap.output_extract or {}).items():
            val = _resolve_path(data, path) if data is not None else None
            out[field_name] = val
        if out and all(v is None for v in out.values()):
            raise ValueError(f"未能从响应抽出 {list(cap.output_extract or {})}：{str(data)[:160]}")
        return out

    raise ValueError(f"暂不支持的 provider_type：{cap.provider_type}")


async def run_scenario(db, scenario: DataScenario, requirement, environment: str = "sit") -> ScenarioResult:
    """按场景为一条数据要求造数。返回 ScenarioResult（含 alias 变量）。"""
    res = ScenarioResult()
    ctx: dict[str, Any] = {}
    # 种子：要求自己的 target_state / constraints 作为 ${req.*} 可供步骤引用
    for k, v in (requirement.target_state or {}).items():
        ctx[f"req.{k}"] = v
    for k, v in (requirement.constraints or {}).items():
        ctx[f"req.{k}"] = v

    for step in (scenario.workflow or []):
        cap_id = step.get("use")
        version = step.get("version")
        out_name = step.get("output") or cap_id
        cap = await _load_capability(db, cap_id, version)
        if cap is None:
            res.ok = False
            res.error = f"能力未就绪：{cap_id}{('@'+version) if version else ''} 不存在或未认证(ACTIVE+APPROVED)"
            return res
        unresolved: list[str] = []
        inputs = _resolve_val(step.get("input") or {}, ctx, unresolved)
        if unresolved:
            res.ok = False
            res.error = f"步骤 {cap_id} 的输入无法解析：{'、'.join('${%s}' % u for u in unresolved)}"
            return res
        try:
            outputs = await _exec_capability(cap, inputs, environment)
        except Exception as e:  # noqa: BLE001
            res.ok = False
            res.error = f"能力 {cap_id} 执行失败：{e}"
            res.trace.append({"step": cap_id, "error": str(e)})
            return res
        for k, v in (outputs or {}).items():
            ctx[f"{out_name}.{k}"] = v
        res.trace.append({"step": cap_id, "output_name": out_name, "outputs": outputs})

    # postconditions：独立校验目标状态真达成（方案 §13）
    for pc in (scenario.postconditions or []):
        vcap = await _load_capability(db, pc.get("validator"), pc.get("version"))
        if vcap is None:
            res.ok = False
            res.error = f"校验能力未就绪：{pc.get('validator')}"
            return res
        un: list[str] = []
        vin = _resolve_val(pc.get("input") or {}, ctx, un)
        if un:
            res.ok = False
            res.error = f"校验输入无法解析：{'、'.join(un)}"
            return res
        try:
            state = await _exec_capability(vcap, vin, environment)
        except Exception as e:  # noqa: BLE001
            res.ok = False
            res.error = f"状态校验执行失败：{e}"
            return res
        for ek, ev in (pc.get("expected") or {}).items():
            actual = state.get(ek)
            if str(actual) != str(ev):
                res.ok = False
                res.error = f"目标状态未达成：{ek} 期望={ev} 实际={actual}"
                res.trace.append({"validate": pc.get("validator"), "expected": pc.get("expected"), "actual": state})
                return res
        res.trace.append({"validate": pc.get("validator"), "ok": True, "state": state})

    # 按 output_key 把场景产出映射回该要求的 alias 变量
    alias = getattr(requirement, "alias", None) or "data"
    out_key = getattr(requirement, "output_key", None) or getattr(requirement, "data_type", None) or ""
    out_map = (scenario.outputs or {}).get(out_key)
    if not out_map and len(scenario.outputs or {}) == 1:
        out_map = list((scenario.outputs or {}).values())[0]
    un2: list[str] = []
    for var_field, ref in (out_map or {}).items():
        res.variables[f"{alias}.{var_field}"] = _resolve_val(ref, ctx, un2)
    if un2:
        res.ok = False
        res.error = f"场景输出映射无法解析：{'、'.join(un2)}"
    return res
