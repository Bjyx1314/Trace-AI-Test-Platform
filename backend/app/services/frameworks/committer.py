"""把通过 review 的原生产物写入框架仓库工作树并提交。

默认在独立分支提交（auto/case-<id>），便于走 PR / 人工合并；push 可选。
依赖 repo_manager 的 git 封装与已 checkout 的工作区。
"""
from __future__ import annotations

import ast
from pathlib import Path

from . import repo_manager


def _insert_into_case_yml_list(file_path: Path, yaml_ref: str) -> bool:
    """向已存在壳文件的 case_yml_list 追加一个 yaml 路径（幂等）。成功返回 True。"""
    if not file_path.exists():
        return False
    src = file_path.read_text(encoding="utf-8")
    if yaml_ref in src:
        return True
    lines = src.splitlines()
    # 找 case_yml_list = [ 的右括号行，在其前插入
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "case_yml_list" for t in node.targets
        ) and isinstance(node.value, ast.List) and node.value.end_lineno:
            close_idx = node.value.end_lineno - 1  # 0-based，] 所在行
            indent = " " * 8
            lines.insert(close_idx, f"{indent}'{yaml_ref}',")
            file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            return True
    return False


def write_artifacts(repo_root: Path, artifacts: list) -> list[str]:
    """把产物写入工作树，返回写入/改动的相对路径列表。"""
    repo_root = Path(repo_root)
    written: list[str] = []
    for a in artifacts:
        rel = a.path.lstrip("/")
        target = repo_root / rel
        if a.action == "append_case_yml":
            # path=壳文件，content=要追加的 yaml 引用
            if _insert_into_case_yml_list(target, a.content.strip()):
                written.append(rel)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(a.content, encoding="utf-8")
        written.append(rel)
    return written


def commit_artifacts(
    repo_root: Path,
    artifacts: list,
    *,
    branch: str,
    message: str,
    base_branch: str | None = None,
    push: bool = False,
) -> dict:
    """写文件 → 新分支 → add/commit →（可选）push。返回 {branch, commit, files, pushed}。"""
    repo_root = Path(repo_root)
    if base_branch:
        repo_manager._run(["checkout", base_branch], cwd=repo_root)
    # 新建/切换到目标分支
    try:
        repo_manager._run(["checkout", "-b", branch], cwd=repo_root)
    except repo_manager.GitError:
        repo_manager._run(["checkout", branch], cwd=repo_root)

    files = write_artifacts(repo_root, artifacts)
    for rel in files:
        repo_manager._run(["add", rel], cwd=repo_root)
    repo_manager._run(["commit", "-m", message], cwd=repo_root)
    sha = repo_manager._run(["rev-parse", "HEAD"], cwd=repo_root)

    pushed = False
    if push:
        repo_manager._run(["push", "-u", "origin", branch], cwd=repo_root)
        pushed = True
    return {"branch": branch, "commit": sha, "files": files, "pushed": pushed}
