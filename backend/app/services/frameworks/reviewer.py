"""提交前自动 review —— 静态校验生成产物，挡住"幻觉/写坏"的用例再合并回仓库。

校验项（无需安装目标仓库依赖，纯静态）：
1. 语法：每个 .py 必须 ast.parse 通过；每个 .yaml 必须 yaml.safe_load 通过。
2. 引用真实积木：
   - interface：用例 YAML 每个 step 的 AWFunc 必须在 index 关键字全集内；壳 case_yml_list
     指向的 yaml 必须在本批产物或仓库中存在。
   - UI：test 中引用的 PageObject/Flow 类名必须在 index 内；调用的方法必须是该类公共方法。
3. 命名约定：用例文件名 test_ 开头；接口壳类名 Test 开头。
collect-only dry-run 留待执行机（依赖齐备处）做，见 runner；此处只做依赖无关的强校验。
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover
    yaml = None


@dataclass
class ReviewResult:
    ok: bool = True
    issues: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def fail(self, msg: str):
        self.ok = False
        self.issues.append(msg)

    def warn(self, msg: str):
        self.warnings.append(msg)


def _interface_keyword_set(index: dict) -> set[str]:
    kws: set[str] = set()
    for c in index.get("aw_classes", []):
        kws.update(c.get("keywords", []))
        kws.update(c.get("methods", []))
    return kws


def _ui_class_methods(index: dict) -> dict[str, set[str]]:
    """{类名: {公共方法名}}，合并 pages/flows/components。"""
    out: dict[str, set[str]] = {}
    for bucket in ("pages", "flows", "components"):
        for e in index.get(bucket, []):
            out.setdefault(e["class"], set()).update(m["name"] for m in e.get("methods", []))
    return out


def _check_yaml_steps(content: str, kw_set: set[str], rr: ReviewResult, fname: str):
    if yaml is None:
        rr.warn("PyYAML 不可用，跳过 YAML 步骤校验")
        return
    try:
        docs = yaml.safe_load(content)
    except Exception as exc:  # noqa: BLE001
        rr.fail(f"{fname}: YAML 解析失败 - {exc}")
        return
    cases = docs if isinstance(docs, list) else [docs]
    for case in cases:
        if not isinstance(case, dict):
            continue
        for step in case.get("steps", []) or []:
            if not isinstance(step, dict):
                continue
            aw = step.get("AWFunc")
            if aw and aw not in kw_set:
                rr.fail(f"{fname}: 步骤引用了不存在的 AWFunc 关键字「{aw}」")


def _referenced_ui_calls(tree: ast.AST):
    """收集 (类名实例化, 方法调用)。返回 (instantiated_classes, var_class_map, method_calls)。

    简化处理：var = ClassName(...) 记 var→ClassName；var.method() 记 (ClassName, method)。
    """
    var_class: dict[str, str] = {}
    method_calls: list[tuple[str, str]] = []
    classes_used: set[str] = set()

    for node in ast.walk(tree):
        # var = ClassName(...)
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            f = node.value.func
            if isinstance(f, ast.Name) and f.id[:1].isupper():
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        var_class[t.id] = f.id
                classes_used.add(f.id)
    for node in ast.walk(tree):
        # var.method(...)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            recv = node.func.value
            if isinstance(recv, ast.Name) and recv.id in var_class:
                method_calls.append((var_class[recv.id], node.func.attr))
    return classes_used, method_calls


def review(index: dict, artifacts: list, repo_root: Path | None = None) -> ReviewResult:
    """artifacts: list[Artifact(path, content, action)]。返回 ReviewResult。"""
    rr = ReviewResult()
    repo_type = index.get("kind")
    kw_set = _interface_keyword_set(index) if repo_type == "interface" else set()
    cls_methods = _ui_class_methods(index) if repo_type == "ui" else {}
    produced_paths = {a.path.lstrip("/") for a in artifacts}

    for a in artifacts:
        name = a.path
        # 1) 语法
        if name.endswith(".py"):
            try:
                tree = ast.parse(a.content, filename=name)
            except SyntaxError as exc:
                rr.fail(f"{name}: Python 语法错误 - {exc}")
                continue
            base = name.rsplit("/", 1)[-1]
            if base.startswith("test_"):
                # 接口壳类名约定
                for node in tree.body:
                    if isinstance(node, ast.ClassDef) and not node.name.startswith("Test"):
                        rr.warn(f"{name}: 测试类「{node.name}」未以 Test 开头")
            # UI 引用校验
            if repo_type == "ui" and cls_methods:
                used, calls = _referenced_ui_calls(tree)
                for c in used:
                    if c not in cls_methods and c.endswith(("Page", "Flow", "Component")):
                        rr.fail(f"{name}: 引用了 index 中不存在的页面/流程类「{c}」")
                for c, mth in calls:
                    if c in cls_methods and mth not in cls_methods[c]:
                        rr.fail(f"{name}: 「{c}」无公共方法「{mth}」（不在 index 内）")
        elif name.endswith((".yaml", ".yml")):
            _check_yaml_steps(a.content, kw_set, rr, name)

        # 3) 命名：用例/壳文件
        leaf = name.rsplit("/", 1)[-1]
        if ("/tests/" in name or "/cases/" in name or name.startswith("cases/")) and leaf.endswith(".py"):
            if not leaf.startswith("test_"):
                rr.fail(f"{name}: 用例文件名应以 test_ 开头")

    # 2b) 接口壳 case_yml_list 指向的 yaml 是否存在（本批 or 仓库）
    if repo_type == "interface":
        for a in artifacts:
            if a.path.endswith(".py"):
                for ref in _shell_yaml_refs(a.content):
                    ref_norm = ref.lstrip("/")
                    in_batch = ref_norm in produced_paths
                    in_repo = bool(repo_root and (Path(repo_root) / ref_norm).exists())
                    if not (in_batch or in_repo):
                        rr.fail(f"{a.path}: case_yml_list 引用的 YAML 不存在「{ref}」")
    return rr


def _shell_yaml_refs(py_content: str) -> list[str]:
    """从壳里抓 case_yml_list 中的字符串字面量。"""
    try:
        tree = ast.parse(py_content)
    except SyntaxError:
        return []
    refs: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "case_yml_list" and isinstance(node.value, (ast.List, ast.Tuple)):
                    for el in node.value.elts:
                        if isinstance(el, ast.Constant) and isinstance(el.value, str):
                            refs.append(el.value)
    return refs
