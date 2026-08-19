"""框架积木扫描器 —— 纯 AST 静态扫描，不导入目标仓库（避免依赖/环境耦合）。

两类框架：
- 接口（interface）：数据驱动 AW 关键字架构。积木 = AW 类 + 其 `aw_yml_list` 中的关键字清单。
  生成用例时引用「AW类.关键字」组织 YAML 步骤。
- UI（web/app）：POM 分层。积木 = pages/flows/components 的类与公共方法 + fixtures 清单。
  生成用例时引用真实的 page/flow 方法编排 test。

所有函数输入文件系统路径、输出可 JSON 序列化的 dict，便于缓存进 FrameworkRepo.index_json，
也便于脱离 DB 单测（直接对本地框架仓库跑）。
"""
from __future__ import annotations

import ast
from pathlib import Path


# ── 通用 AST 辅助 ──────────────────────────────────────────────────────────

def _safe_parse(path: Path) -> ast.Module | None:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError, OSError):
        return None


def _func_args(node: ast.FunctionDef | ast.AsyncFunctionDef) -> list[str]:
    """提取参数名（去掉 self/cls）。"""
    args = [a.arg for a in node.args.args]
    if args and args[0] in ("self", "cls"):
        args = args[1:]
    return args


def _public_methods(cls: ast.ClassDef) -> list[dict]:
    """类内非下划线开头的方法（公共积木方法）。"""
    out: list[dict] = []
    for item in cls.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_"):
            out.append({
                "name": item.name,
                "args": _func_args(item),
                "doc": ast.get_docstring(item),
            })
    return out


def _iter_py(root: Path):
    """递归遍历 .py，跳过缓存/隐藏目录与 __init__。"""
    if not root.exists():
        return
    for p in sorted(root.rglob("*.py")):
        parts = set(p.parts)
        if "__pycache__" in parts or any(x.startswith(".") for x in p.relative_to(root).parts[:-1]):
            continue
        if p.name == "__init__.py":
            continue
        yield p


# ── 接口框架扫描 ────────────────────────────────────────────────────────────

def _dict_str_keys(value: ast.AST) -> list[str]:
    """从 ast 字典字面量里取出字符串 key（关键字名）。"""
    keys: list[str] = []
    if isinstance(value, ast.Dict):
        for k in value.keys:
            if isinstance(k, ast.Constant) and isinstance(k.value, str):
                keys.append(k.value)
    return keys


def scan_interface(repo_root: Path, keyword_root: str = "data/aw/aw_class") -> dict:
    """扫描接口框架的 AW 关键字库。

    约定：keyword_root 下每个 .py 含若干 AW 类，类体内 `aw_yml_list = {关键字: yaml路径}`。
    返回 {"aw_classes": [{module, class, doc, keywords:[...]}], "class_count", "keyword_count"}。
    """
    base = repo_root / keyword_root
    aw_classes: list[dict] = []
    kw_total = 0

    for py in _iter_py(base):
        tree = _safe_parse(py)
        if tree is None:
            continue
        rel = py.relative_to(repo_root).as_posix()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            keywords: list[str] = []
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    targets = [t.id for t in stmt.targets if isinstance(t, ast.Name)]
                    if "aw_yml_list" in targets:
                        keywords = _dict_str_keys(stmt.value)
            # 也收录类内显式定义的公共方法（部分 AW 走 python 方法而非 yaml 关键字）
            methods = _public_methods(node)
            if not keywords and not methods:
                continue
            kw_total += len(keywords)
            aw_classes.append({
                "module": rel,
                "class": node.name,
                "doc": ast.get_docstring(node),
                "keywords": keywords,
                "methods": [m["name"] for m in methods],
            })

    return {
        "kind": "interface",
        "keyword_root": keyword_root,
        "aw_classes": aw_classes,
        "class_count": len(aw_classes),
        "keyword_count": kw_total,
    }


# ── UI 框架扫描（web/app 同构）──────────────────────────────────────────────

def _scan_class_dir(repo_root: Path, root: Path) -> list[dict]:
    """扫描一个目录下所有类及其公共方法。"""
    out: list[dict] = []
    for py in _iter_py(root):
        tree = _safe_parse(py)
        if tree is None:
            continue
        rel = py.relative_to(repo_root).as_posix()
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                out.append({
                    "module": rel,
                    "class": node.name,
                    "bases": [b.id for b in node.bases if isinstance(b, ast.Name)],
                    "doc": ast.get_docstring(node),
                    "methods": _public_methods(node),
                })
    return out


def _is_fixture_decorator(dec: ast.AST) -> bool:
    """识别 @pytest.fixture / @fixture / @pytest.fixture(...)。"""
    target = dec.func if isinstance(dec, ast.Call) else dec
    if isinstance(target, ast.Attribute):
        return target.attr == "fixture"
    if isinstance(target, ast.Name):
        return target.id == "fixture"
    return False


def _fixture_scope(dec: ast.AST) -> str | None:
    if isinstance(dec, ast.Call):
        for kw in dec.keywords:
            if kw.arg == "scope" and isinstance(kw.value, ast.Constant):
                return str(kw.value.value)
    return None


def _scan_fixtures(repo_root: Path, root: Path) -> list[dict]:
    out: list[dict] = []
    for py in _iter_py(root):
        tree = _safe_parse(py)
        if tree is None:
            continue
        rel = py.relative_to(repo_root).as_posix()
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                fx = next((d for d in node.decorator_list if _is_fixture_decorator(d)), None)
                if fx is not None:
                    out.append({
                        "module": rel,
                        "name": node.name,
                        "scope": _fixture_scope(fx),
                        "doc": ast.get_docstring(node),
                    })
    return out


def scan_ui(repo_root: Path, ui_base: str) -> dict:
    """扫描 UI 框架（POM）。

    ui_base 为该端源码根（如 ui_web / ui_app）。按约定子目录扫描：
      <ui_base>/pages、<ui_base>/flows、<ui_base>/pages/components、<ui_base>/fixtures
    缺失的子目录自动跳过（如 ui_app 可能无 components）。
    """
    base = repo_root / ui_base
    pages = _scan_class_dir(repo_root, base / "pages")
    components = _scan_class_dir(repo_root, base / "pages" / "components")
    # components 在 pages/ 之下，从 pages 列表里剔除，避免重复
    comp_modules = {c["module"] for c in components}
    pages = [p for p in pages if p["module"] not in comp_modules]

    return {
        "kind": "ui",
        "ui_base": ui_base,
        "pages": pages,
        "flows": _scan_class_dir(repo_root, base / "flows"),
        "components": components,
        "fixtures": _scan_fixtures(repo_root, base / "fixtures"),
        "page_count": len(pages),
        "flow_count": len(_scan_class_dir(repo_root, base / "flows")),
    }


def build_index(repo_root: Path, *, repo_type: str, tests_root: str | None = None,
                keyword_root: str | None = None) -> dict:
    """按 repo_type 选择扫描策略，产出 index_json。

    - interface：扫 keyword_root（默认 data/aw/aw_class）。
    - web/app：扫 ui_base（由 tests_root 推导，如 ui_web/tests → ui_web）。
    """
    if repo_type == "interface":
        return scan_interface(repo_root, keyword_root or "data/aw/aw_class")

    # UI：从 tests_root 推导源码根
    ui_base = "ui_web"
    if tests_root:
        tr = tests_root.strip("/").replace("\\", "/")
        ui_base = tr.rsplit("/tests", 1)[0] if "/tests" in tr else tr.split("/")[0]
    return scan_ui(repo_root, ui_base)
