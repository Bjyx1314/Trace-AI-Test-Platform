"""框架仓库本地工作区管理 —— clone / fetch / checkout，提供 commit sha。

平台为每个 FrameworkRepo 在 settings.framework_workspace 下维护一份 checkout，
索引扫描与仓库内执行都基于它。本模块只封装 git 操作，保持同步、可单测。
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path


class GitError(RuntimeError):
    pass


def _run(args: list[str], cwd: Path | None = None, timeout: int = 180) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} 失败: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def _slug(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]+", "_", name).strip("_") or "repo"


def workspace_path(workspace_base: str, repo_id: str, name: str) -> Path:
    """该仓库在工作区的目录：<base>/<slug(name)>-<repo_id前8位>，避免重名冲突。"""
    return Path(workspace_base) / f"{_slug(name)}-{repo_id[:8]}"


def ensure_repo(
    git_url: str,
    branch: str,
    dest: Path,
    *,
    pull: bool = True,
) -> tuple[Path, str]:
    """确保 dest 是 git_url@branch 的最新 checkout，返回 (路径, commit_sha)。

    - 不存在 → clone（单分支浅历史可选，这里取完整以便后续提交）。
    - 已存在 → fetch + checkout branch，pull=True 时硬对齐 origin/branch。
    幂等：重复调用安全。git 凭据依赖执行机已配置（缓存/SSH/URL 内嵌）。
    """
    dest = Path(dest)
    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        _run(["clone", "--branch", branch, git_url, str(dest)])
    else:
        _run(["fetch", "origin", branch], cwd=dest)
        _run(["checkout", branch], cwd=dest)
        if pull:
            # 硬对齐远端，避免本地历史/冲突干扰索引与执行（生成提交走独立分支，不在此基准上）
            _run(["reset", "--hard", f"origin/{branch}"], cwd=dest)

    sha = _run(["rev-parse", "HEAD"], cwd=dest)
    return dest, sha


def current_commit(dest: Path) -> str | None:
    try:
        return _run(["rev-parse", "HEAD"], cwd=Path(dest))
    except GitError:
        return None


def fetch_branch(dest: Path, branch: str) -> None:
    """确保 origin/<branch> 引用在本地存在且最新（供 diff base 用，如 origin/master）。"""
    try:
        _run(["fetch", "origin", branch], cwd=Path(dest))
    except GitError:
        pass


def checkout_commit(dest: Path, commit: str) -> str:
    """checkout 到指定 commit（detached HEAD），返回解析后的完整 sha。

    调用前须已 ensure_repo（保证该 commit 已在本地历史内）。commit 可为短 sha/完整 sha。
    """
    dest = Path(dest)
    # 若浅克隆里没有该 commit，尝试补捞（best-effort，失败则由后续 checkout 报错）
    try:
        _run(["fetch", "--depth", "200", "origin", commit], cwd=dest)
    except GitError:
        pass
    _run(["checkout", "--detach", commit], cwd=dest)
    return _run(["rev-parse", "HEAD"], cwd=dest)
