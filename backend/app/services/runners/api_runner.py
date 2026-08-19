"""ApiRunner —— 接口自动化真实执行。

两种真实执行路径(都不 mock、不伪造)：
1) 用例有脚本(script)：把脚本写入隔离临时目录，用 `pytest --json-report` 子进程跑(httpx 真发请求)。
2) 用例无脚本：AI 直连(像 Postman) —— ApiExecutorAgent 按用例构造 HTTP 请求序列+断言，
   平台用 httpx【真发请求】、按断言判定，产出 api_trace。免脚本、免框架。
端环境/AI 不可用时产出真实 error，绝不假装通过。
"""
from __future__ import annotations

import json as _json
import re
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx

from .base import (
    BaseRunner,
    RunContext,
    RunOutcome,
    parse_pytest_subprocess_outcome,
    run_pytest_subprocess,
)

# pytest 子进程兜底超时（秒），防脚本死循环拖死 worker
_DEFAULT_TIMEOUT = 120


def _safe_name(case: Any) -> str:
    raw = getattr(case, "case_id", None) or getattr(case, "title", None) or "case"
    return re.sub(r"[^0-9A-Za-z_]+", "_", str(raw))[:40] or "case"


# ── AI 直连执行的工具函数 ────────────────────────────────────────────────────

def _resolve_path(obj: Any, path: str | None) -> Any:
    """简易 JSON 路径取值，支持 a.b.c 与 a[0].b，可带前导 $ / .。"""
    if path is None:
        return None
    p = str(path).strip().lstrip("$").lstrip(".")
    cur = obj
    for tok in re.findall(r"[^.\[\]]+|\[\d+\]", p):
        if cur is None:
            return None
        if tok.startswith("[") and tok.endswith("]"):
            idx = int(tok[1:-1])
            if isinstance(cur, list) and -len(cur) <= idx < len(cur):
                cur = cur[idx]
            else:
                return None
        elif isinstance(cur, dict):
            cur = cur.get(tok)
        else:
            return None
    return cur


def _subst(val: Any, base_url: str | None, vars: dict) -> Any:
    """递归替换 {base_url} 与 {{var}} 占位。"""
    if isinstance(val, str):
        out = val.replace("{base_url}", (base_url or "").rstrip("/"))
        for k, v in vars.items():
            out = out.replace("{{%s}}" % k, str(v))
        return out
    if isinstance(val, dict):
        return {k: _subst(v, base_url, vars) for k, v in val.items()}
    if isinstance(val, list):
        return [_subst(v, base_url, vars) for v in val]
    return val


def _join_url(url: str, base_url: str | None) -> str:
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if base_url:
        return base_url.rstrip("/") + "/" + url.lstrip("/")
    return url


def _looks_like_common_success(body: Any) -> bool:
    """兜住常见"成功"响应口径，避免 AI 只断言 success 字面量时误判。

    常见返回会写成 errCode/code=0、success=true、message/msg="请求成功" 等。
    """
    if isinstance(body, str):
        txt = body.lower()
        return "success" in txt or "成功" in body
    if not isinstance(body, dict):
        return False

    for key in ("success", "ok"):
        val = body.get(key)
        if val is True:
            return True
        if isinstance(val, str) and val.strip().lower() in {"true", "success", "ok"}:
            return True

    for key in ("errCode", "code", "status", "resultCode"):
        val = body.get(key)
        if val in (0, 200):
            return True
        if isinstance(val, str) and val.strip().lower() in {"0", "200", "success", "ok"}:
            return True

    for key in ("message", "msg"):
        val = body.get(key)
        if isinstance(val, str):
            txt = val.lower()
            if "success" in txt or "成功" in val:
                return True
    return False


def _plan_from_spec(spec: dict) -> dict:
    """结构化 API 定义(决策3a) → 直接构造请求计划，免 AI 猜测(Postman 式直连)。"""
    req = {
        "method": (spec.get("method") or "GET").upper(),
        "url": spec.get("path") or spec.get("url") or "",
        "headers": dict(spec.get("headers") or {}),
        "params": spec.get("query") or None,
        "json_body": spec.get("body"),
    }
    return {"requests": [req], "asserts": spec.get("asserts") or [], "note": "结构化 API 定义直连(免代码仓库)"}


async def _login_token_header(auth: dict, env: str, temp_account: dict | None = None) -> dict:
    """按 auth.service 登录并返回鉴权头，token 统一放 X-Auth-Token(决策5)。

    登录方式【按端而定】：示例/租户等走 SMS，Admin 及同源后台走【账号密码登录】(密码需 RSA
    加密)。原来这里写死 SMS + kgl 账号，打 Admin 只会得到"抱歉，您没有权限"。
    档案见 login_profiles；能力/用例可在 auth 里显式给 appid/account 覆盖。
    账号优先级：执行时临时账号 > 用例自带 auth.account > 框架默认账号池。
    """
    from app.services.frameworks.interface_env import account as _account
    from app.services.frameworks.interface_env import login_url as _login_url
    from app.services.frameworks.interface_env import resolve_service_base_url as _resolve
    from app.services.frameworks import login_profiles

    service = auth.get("service")
    if login_profiles.profile_for(service):
        return await login_profiles.login_headers(
            service, env, appid=str(auth.get("appid") or "").strip() or None,
            account={**(auth.get("account") or {}), **(temp_account or {})} or None,
            account_profile=auth.get("account_profile"),
        )

    appid = str(auth.get("appid") or "").strip()
    host = _resolve(service, env)
    path = _login_url("sms_loginurl", env)
    if not host or not path or not appid:
        raise ValueError(f"接口鉴权配置不完整(service={service!r}, appid={appid!r})，无法登录")

    temp = temp_account or {}
    acc = auth.get("account") or {}
    default_acc = _account(env)
    payload = {
        "phone": temp.get("phone") or acc.get("phone") or default_acc.get("kglAccount"),
        "verifyCode": temp.get("verify_code") or acc.get("verify_code") or default_acc.get("kglPwd"),
        "codeId": temp.get("code_id") or acc.get("code_id") or default_acc.get("kglCode"),
    }
    async with httpx.AsyncClient(timeout=30, verify=False) as client:
        resp = await client.post(
            host.rstrip("/") + path, json=payload,
            headers={"Content-Type": "application/json", "X-Auth-AppId": appid},
        )
        data = resp.json()
    token = (data.get("data") or {}).get("token") or (data.get("data") or {}).get("ticket")
    if not token:
        raise ValueError(f"登录未取得 token：{data}")
    return {"X-Auth-Token": token, "X-Auth-AppId": appid}


def _eval_assert(a: dict, status: int, body: Any) -> tuple[bool, str]:
    t = a.get("type")
    if t == "status_equals":
        ok = status == int(a.get("value"))
        return ok, "" if ok else f"期望状态码={a.get('value')}，实际={status}"
    if t == "status_lt":
        ok = status < int(a.get("value"))
        return ok, "" if ok else f"期望状态码<{a.get('value')}，实际={status}"
    if t == "body_contains":
        wanted = str(a.get("value") or "")
        if wanted.strip().lower() in {"success", "成功", "请求成功"} and _looks_like_common_success(body):
            return True, ""
        txt = body if isinstance(body, str) else _json.dumps(body, ensure_ascii=False)
        ok = wanted in (txt or "")
        return ok, "" if ok else f"响应未包含: {wanted}"
    if t == "jsonpath_equals":
        val = _resolve_path(body, a.get("path"))
        ok = str(val) == str(a.get("value"))
        return ok, "" if ok else f"{a.get('path')} 期望={a.get('value')}，实际={val}"
    return True, ""


class ApiRunner(BaseRunner):
    platform = "api"
    requires_device = False

    def __init__(self, timeout_sec: int = _DEFAULT_TIMEOUT):
        self.timeout_sec = timeout_sec

    async def run(self, case: Any, ctx: RunContext) -> RunOutcome:
        """有脚本走 pytest 子进程；无脚本走 AI 直连(真发 HTTP)。"""
        script = getattr(case, "script", None)
        if script and str(script).strip():
            return await super().run(case, ctx)
        return await self._ai_direct(case, ctx)

    # ── 路径一：脚本型(pytest 子进程) ──────────────────────────────────────
    def _prepare(self, case: Any, ctx: RunContext) -> Path:
        # 数据前置注入过的脚本（${别名.字段} 已替真值）优先，让脚本用例也能吃到编排造出来的数据。
        script = (ctx.extra or {}).get("script_override") or getattr(case, "script", None)
        tmp = Path(tempfile.mkdtemp(prefix="apirun_"))
        test_file = tmp / f"test_{_safe_name(case)}.py"
        test_file.write_text(str(script), encoding="utf-8")
        return tmp

    async def _execute(self, workdir: Path, case: Any, ctx: RunContext) -> dict:
        # 除占位符替换外，再把变量以 JSON 透给子进程，脚本可
        # `json.loads(os.environ["TP_DATA_VARS"])["targetOrder.orderId"]` 动态取用。
        env = None
        data_vars = (ctx.extra or {}).get("data_vars") or {}
        if data_vars:
            env = {"TP_DATA_VARS": _json.dumps(data_vars, ensure_ascii=False, default=str)}
        return await run_pytest_subprocess(workdir, timeout_sec=self.timeout_sec, env=env)

    def _parse(self, report: dict) -> RunOutcome:
        return parse_pytest_subprocess_outcome(report)

    # ── 路径二：直连执行 —— 有结构化 API 定义(决策3a)则 Postman 式直发；否则回退 AI 猜测(兼容旧用例) ──
    async def _ai_direct(self, case: Any, ctx: RunContext) -> RunOutcome:
        base_url = ctx.base_url
        # tags 两种形状：AI 生成的用例是字符串标签数组，接口用例才是带 api_spec 的字典。
        _tags = getattr(case, "tags", None)
        spec = (_tags.get("api_spec") or None) if isinstance(_tags, dict) else None
        if not isinstance(spec, dict):
            spec = None

        auth_headers: dict = {}
        if spec and spec.get("auth"):
            env = (ctx.extra or {}).get("env") or "sit"
            temp_account = ((ctx.extra or {}).get("account_overrides") or {}).get("api")
            try:
                auth_headers = await _login_token_header(spec["auth"], env, temp_account)
            except Exception as e:  # noqa: BLE001
                return RunOutcome(status="error", duration_ms=0,
                                  error_message=f"接口登录失败：{e}", failure_type="env_error")

        if spec:
            plan = _plan_from_spec(spec)
        else:
            from app.agents.api_executor import ApiExecutorAgent

            case_dict = {
                "title": getattr(case, "title", "") or "",
                # 数据前置注入好的步骤（${别名.字段} 已替真值）优先，让 AI 按真实数据构造请求。
                "steps": (ctx.extra or {}).get("steps_override") or getattr(case, "steps", None) or [],
                "preconditions": getattr(case, "preconditions", None) or [],
                "expected_result": getattr(case, "expected_result", None) or "",
            }
            # AI 构造请求计划（生产无 provider/调用失败会抛错→真实 env_error，不 mock）
            try:
                plan = await ApiExecutorAgent().build_plan(case_dict, base_url)
            except Exception as e:  # noqa: BLE001
                return RunOutcome(status="error", duration_ms=0,
                                  error_message=f"AI 构造接口请求失败：{e}", failure_type="env_error")

        requests = (plan or {}).get("requests") or []
        if not requests:
            return RunOutcome(
                status="error", duration_ms=0,
                error_message="未能构造出可发送的接口请求（用例信息不足，建议补充结构化 API 定义或接口路径/参数）",
                failure_type="script_error", api_trace={"plan": plan})

        # 2) 真发请求序列（支持前置鉴权 + 变量抽取）
        # 掺入数据前置的平台级变量（${别名.字段}），供 spec 里 {{别名.字段}} 引用（方案 §20.2）。
        vars: dict = dict((ctx.extra or {}).get("data_vars") or {})
        trace_reqs, trace_resps = [], []
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=self.timeout_sec, verify=False, follow_redirects=True) as client:
                for r in requests:
                    method = str(_subst(r.get("method") or "GET", base_url, vars)).upper()
                    url = _join_url(_subst(r.get("url") or "", base_url, vars), base_url)
                    headers = {**auth_headers, **_subst(r.get("headers") or {}, base_url, vars)} or None
                    params = _subst(r.get("params") or {}, base_url, vars) or None
                    body = r.get("json_body")
                    body = _subst(body, base_url, vars) if body is not None else None
                    resp = await client.request(method, url, headers=headers, params=params, json=body)
                    try:
                        resp_json = resp.json()
                    except Exception:  # noqa: BLE001
                        resp_json = None
                    trace_reqs.append({"method": method, "url": str(resp.request.url),
                                       "headers": dict(resp.request.headers), "body": body})
                    trace_resps.append({"status": resp.status_code, "headers": dict(resp.headers),
                                        "body": resp_json if resp_json is not None else (resp.text or "")[:4000]})
                    for var, path in (r.get("extract") or {}).items():
                        v = _resolve_path(resp_json, path) if resp_json is not None else None
                        if v is not None:
                            vars[var] = v
        except Exception as e:  # noqa: BLE001
            dur = int((time.monotonic() - start) * 1000)
            return RunOutcome(status="error", duration_ms=dur,
                              error_message=f"接口请求发送失败：{e}", failure_type="env_error",
                              api_trace={"requests": trace_reqs, "responses": trace_resps, "error": str(e)})

        dur = int((time.monotonic() - start) * 1000)
        last_status = trace_resps[-1]["status"]
        last_body = trace_resps[-1]["body"]
        asserts = (plan or {}).get("asserts") or []
        api_trace = {"requests": trace_reqs, "responses": trace_resps,
                     "extracted": vars, "asserts": asserts, "note": (plan or {}).get("note")}

        # 3) 按断言判定（基于真实响应；无断言则以状态码<400 兜底）
        if not asserts:
            if last_status < 400:
                return RunOutcome(status="passed", duration_ms=dur, api_trace=api_trace,
                                  error_message="(AI 未给出显式断言，按最终状态码<400 判通过)")
            return RunOutcome(status="failed", duration_ms=dur, failure_type="real_defect",
                              error_message=f"最终状态码 {last_status}", api_trace=api_trace)

        failures = [msg for a in asserts for ok, msg in [_eval_assert(a, last_status, last_body)] if not ok]
        if failures:
            return RunOutcome(status="failed", duration_ms=dur, failure_type="real_defect",
                              error_message="；".join(failures), api_trace=api_trace)
        return RunOutcome(status="passed", duration_ms=dur, api_trace=api_trace)
