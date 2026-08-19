"""统一变量注入引擎（方案 §20，MVP-0 核心）。

为什么必须有它：UI 端（web/app runner）此前【零变量机制】——steps 是自由文本直接喂 AI，
"数据准备结果自动注入页面执行"根本不成立。这里提供确定性的 `${alias.field}` 占位符替换，
把 ExecutionContext 变量注入 web/app 步骤文本与 api 变量，替掉"账号/订单号写死在步骤里"。

约定：
- `${alias.field}` = 平台级跨用例上下文（本引擎负责）；
- `{{var}}` = 接口用例内部链传（api_runner 内已有，不动，避免与本引擎冲突）；
- 未解析占位符 = 硬阻断：调用方据 unresolved 判 setup_error，绝不带着未替换的 `${...}` 去执行。
"""
from __future__ import annotations

import re
from typing import Any

# ${alias.field} 或 ${alias}；名字里允许 中文/字母/数字/下划线/点/短横线
_PLACEHOLDER = re.compile(r"\$\{([\w.\-一-鿿]+)\}")


def build_variables(requirements: list[Any]) -> tuple[dict[str, Any], dict[str, dict]]:
    """从用例的 TestDataRequirement 列表生成 (variables, credentials)。

    MVP-0：只认 manual_values（人工在 Review 页直填的实际值）。
    - variables: {"alias.field": value, "alias": value(当 manual_values 直接是标量/或含同名 key)}
    - credentials: {alias: {field: value}}，仅收敛 sensitive 字段名（password/token/secret/pwd），
      供登录执行器读取，不进步骤文本/AI prompt。
    """
    variables: dict[str, Any] = {}
    credentials: dict[str, dict] = {}
    for r in requirements or []:
        alias = getattr(r, "alias", None)
        mv = getattr(r, "manual_values", None)
        if not alias or not isinstance(mv, dict):
            continue
        for field, value in mv.items():
            if _is_sensitive(field):
                credentials.setdefault(alias, {})[field] = value
            else:
                variables[f"{alias}.{field}"] = value
    return variables, credentials


_SENSITIVE_HINTS = ("password", "passwd", "pwd", "secret", "token", "credential")


def _is_sensitive(field: str) -> bool:
    f = (field or "").lower()
    return any(h in f for h in _SENSITIVE_HINTS)


def inject_text(text: str, variables: dict[str, Any]) -> tuple[str, list[str]]:
    """把 text 里的 `${alias.field}` 替换为变量值。返回 (替换后文本, 未解析占位符名列表)。"""
    if not text or "${" not in text:
        return text, []
    unresolved: list[str] = []

    def _sub(m: "re.Match[str]") -> str:
        key = m.group(1)
        if key in variables and variables[key] is not None:
            return str(variables[key])
        unresolved.append(key)
        return m.group(0)   # 原样保留，供调用方发现

    return _PLACEHOLDER.sub(_sub, text), unresolved


def inject_steps(steps: list[dict], variables: dict[str, Any]) -> tuple[list[dict], list[str]]:
    """对 steps 的 action/expected 逐条注入。返回 (注入后 steps 的浅拷贝, 全部未解析占位符去重列表)。

    不改动传入对象（浅拷贝每步），避免污染 ORM 对象。
    """
    out: list[dict] = []
    unresolved: set[str] = set()
    for s in steps or []:
        if not isinstance(s, dict):
            out.append(s)
            continue
        ns = dict(s)
        for k in ("action", "expected"):
            if isinstance(ns.get(k), str):
                ns[k], miss = inject_text(ns[k], variables)
                unresolved.update(miss)
        # check_points 里也可能引用
        cps = ns.get("check_points")
        if isinstance(cps, list):
            new_cps = []
            for cp in cps:
                if isinstance(cp, str):
                    v, miss = inject_text(cp, variables)
                    unresolved.update(miss)
                    new_cps.append(v)
                else:
                    new_cps.append(cp)
            ns["check_points"] = new_cps
        out.append(ns)
    return out, sorted(unresolved)


def scan_text_placeholders(text: str | None) -> list[str]:
    """列出一段文本（如接口用例脚本）里出现的全部占位符名。"""
    if not text or "${" not in str(text):
        return []
    return sorted(set(_PLACEHOLDER.findall(str(text))))


def scan_placeholders(steps: list[dict]) -> list[str]:
    """列出 steps 里出现的全部占位符名（用于校验/展示"这条用例引用了哪些数据变量"）。"""
    found: set[str] = set()
    for s in steps or []:
        if not isinstance(s, dict):
            continue
        for k in ("action", "expected"):
            if isinstance(s.get(k), str):
                found.update(_PLACEHOLDER.findall(s[k]))
        for cp in (s.get("check_points") or []):
            if isinstance(cp, str):
                found.update(_PLACEHOLDER.findall(cp))
    return sorted(found)
