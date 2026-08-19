"""框架索引编排 —— 拉取仓库 + 扫描积木 + 写回 FrameworkRepo.index_json。

供 API（手动「重新索引」按钮）与 P3 生成前置（确保 index 新鲜）调用。
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from app.config import settings
from app.models import FrameworkRepo
from . import repo_manager, scanner


def _resolve_local_path(repo: FrameworkRepo) -> Path:
    """优先用已登记的 local_path（直连本地框架目录的场景），否则用工作区路径。"""
    if repo.local_path:
        return Path(repo.local_path)
    return repo_manager.workspace_path(settings.framework_workspace, repo.id, repo.name)


async def reindex(db, repo_id: str, *, sync_git: bool = True) -> FrameworkRepo:
    """重新索引一个框架仓库。

    sync_git=True：先 clone/pull 到工作区再扫描（标准路径）。
    sync_git=False：直接扫描 local_path（仓库已在本地、离线场景）。
    结果写入 index_json/index_status/index_commit/indexed_at/local_path 并提交。
    """
    repo = await db.get(FrameworkRepo, repo_id)
    if repo is None:
        raise ValueError(f"FrameworkRepo 不存在: {repo_id}")

    repo.index_status = "indexing"
    await db.commit()

    try:
        if sync_git and repo.git_url:
            dest = repo_manager.workspace_path(settings.framework_workspace, repo.id, repo.name)
            local_path, sha = repo_manager.ensure_repo(repo.git_url, repo.branch, dest)
        else:
            local_path = _resolve_local_path(repo)
            sha = repo_manager.current_commit(local_path)

        index = scanner.build_index(
            Path(local_path),
            repo_type=repo.repo_type,
            tests_root=repo.tests_root,
            keyword_root=repo.keyword_root,
        )

        repo.local_path = str(local_path)
        repo.index_json = index
        repo.index_commit = sha
        repo.index_status = "ready"
        repo.indexed_at = datetime.now()
        await db.commit()
    except Exception as exc:  # noqa: BLE001 —— 索引失败要落库可见，不吞
        repo.index_status = "failed"
        repo.index_json = {"error": str(exc)[:1000]}
        await db.commit()
        raise

    await db.refresh(repo)
    return repo
