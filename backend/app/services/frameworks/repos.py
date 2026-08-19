"""框架仓库选择 + 生成编排 —— 把用例路由到正确的框架仓库并产出/落库原生产物。"""
from __future__ import annotations

from sqlalchemy import select

from pathlib import Path

from app.models import FrameworkRepo, TestCase
from app.services.runners.dispatcher import resolve_runner_type
from app.agents.script_generator import determine_script_type
from app.services.automation_switch import is_generation_enabled, PLATFORM_LABELS
from .generator import FrameworkGeneratorAgent, Artifact
from . import reviewer, committer

# runner 类型 → 框架仓库类型
_RUNNER_TO_REPO_TYPE = {
    "api": "interface",
    "web": "web",
    "android": "app",
    "ios": "app",
}


def repo_type_for_case(case, platform_group_map: dict[str, str] | None = None) -> str | None:
    """用例应生成进哪类框架仓库（interface/web/app）；鸿蒙/小程序暂无框架返回 None。"""
    return _RUNNER_TO_REPO_TYPE.get(resolve_runner_type(case, platform_group_map))


async def resolve_repo(db, case, platform_group_map: dict[str, str] | None = None) -> FrameworkRepo | None:
    """为用例选框架仓库：先按 (project, repo_type) 找项目专属，再回退全局(project_id 为空)。"""
    rt = repo_type_for_case(case, platform_group_map)
    if rt is None:
        return None
    project_id = getattr(case, "project_id", None)

    stmt = select(FrameworkRepo).where(
        FrameworkRepo.repo_type == rt,
        FrameworkRepo.enabled.is_(True),
        FrameworkRepo.project_id == project_id,
    )
    repo = (await db.execute(stmt)).scalars().first()
    if repo is None:
        glob = select(FrameworkRepo).where(
            FrameworkRepo.repo_type == rt,
            FrameworkRepo.enabled.is_(True),
            FrameworkRepo.project_id.is_(None),
        )
        repo = (await db.execute(glob)).scalars().first()
    return repo


def _case_payload(tc: TestCase) -> dict:
    return {
        "title": tc.title,
        "modules": tc.modules,
        "priority": tc.priority,
        "preconditions": tc.preconditions,
        "steps": [{"action": s.get("action", ""), "expected": s.get("expected", "")} for s in (tc.steps or [])],
        "expected_result": tc.expected_result,
    }


async def generate_and_store(db, case_id: str, *, agent: FrameworkGeneratorAgent | None = None) -> TestCase:
    """为用例生成框架原生产物并落库到 TestCase（不提交仓库；提交由 P4 review 后进行）。"""
    tc = await db.get(TestCase, case_id)
    if tc is None:
        raise ValueError(f"用例不存在: {case_id}")

    # 分端自动化生成开关（与 mock_runner 同口径，键取 determine_script_type 输出）。
    # 关闭则拒绝对接框架生成（缺省视为开启，保持历史行为）。
    script_type = determine_script_type(tc.case_type, tc.platforms)
    if not await is_generation_enabled(db, script_type):
        label = PLATFORM_LABELS.get(script_type, script_type)
        raise ValueError(f"「{label}」端的自动化生成开关已关闭，未对接框架生成")

    repo = await resolve_repo(db, tc)
    if repo is None:
        tc.script_status = "failed"
        await db.commit()
        raise ValueError(f"未找到匹配的框架仓库（repo_type={repo_type_for_case(tc)}）")
    if repo.index_status != "ready" or not repo.index_json:
        raise ValueError(f"框架仓库 {repo.name} 尚未完成索引（index_status={repo.index_status}），请先索引")

    tc.script_status = "generating"
    await db.commit()

    agent = agent or FrameworkGeneratorAgent()
    arts = await agent.generate(
        _case_payload(tc),
        repo.repo_type,
        repo.index_json,
        data_root=repo.data_root or "data",
        tests_root=repo.tests_root or "tests",
    )

    primary = next((a for a in arts.artifacts if a.path == arts.primary_target), None)
    tc.framework_repo_id = repo.id
    tc.generated_artifacts = arts.to_json()
    tc.script = primary.content if primary else (arts.artifacts[0].content if arts.artifacts else None)
    tc.script_path = arts.primary_target
    tc.script_status = "ready"
    tc.is_automated = True
    await db.commit()
    await db.refresh(tc)
    return tc


async def runner_for_case(db, case, platform_group_map: dict[str, str] | None = None):
    """若用例已绑定到「已 checkout 的框架仓库」，返回在仓库内执行的 RepoRunner，否则 None。

    None 时由调用方回退到既有 build_runner（temp 脚本模型 / Mock）。
    """
    repo_id = getattr(case, "framework_repo_id", None)
    target = getattr(case, "script_path", None)
    if not repo_id or not target:
        return None
    repo = await db.get(FrameworkRepo, repo_id)
    if repo is None or not repo.enabled or not repo.local_path:
        return None
    if not Path(repo.local_path).exists():
        return None

    from app.services.runners.repo_runner import RepoRunner
    return RepoRunner(
        repo.local_path,
        target,
        run_command=repo.run_command,
        env=repo.env_json or {},
    )


def _artifacts_from_case(tc: TestCase) -> list[Artifact]:
    data = tc.generated_artifacts or {}
    return [Artifact(path=a["path"], content=a.get("content", ""), action=a.get("action", "create"))
            for a in data.get("artifacts", [])]


async def review_generated(db, case_id: str) -> reviewer.ReviewResult:
    """对已生成产物做提交前静态 review（引用真实积木/语法/命名）。"""
    tc = await db.get(TestCase, case_id)
    if tc is None or not tc.generated_artifacts:
        raise ValueError("用例无已生成产物，先生成")
    repo = await db.get(FrameworkRepo, tc.framework_repo_id) if tc.framework_repo_id else None
    if repo is None or not repo.index_json:
        raise ValueError("用例未关联已索引的框架仓库")
    repo_root = Path(repo.local_path) if repo.local_path else None
    return reviewer.review(repo.index_json, _artifacts_from_case(tc), repo_root=repo_root)


async def commit_generated(db, case_id: str, *, push: bool = False) -> dict:
    """review 通过后把产物提交回框架仓库（独立分支）。review 不过直接抛错。"""
    rr = await review_generated(db, case_id)
    if not rr.ok:
        raise ValueError(f"review 未通过，拒绝提交: {rr.issues}")

    tc = await db.get(TestCase, case_id)
    repo = await db.get(FrameworkRepo, tc.framework_repo_id)
    if not repo.local_path:
        raise ValueError("框架仓库尚未在执行机 checkout，无法提交")

    result = committer.commit_artifacts(
        Path(repo.local_path),
        _artifacts_from_case(tc),
        branch=f"auto/case-{tc.case_id or tc.id[:8]}",
        message=f"test: 自动生成用例 {tc.case_id or ''} {tc.title}".strip(),
        base_branch=repo.branch,
        push=push,
    )
    return {"review": {"ok": rr.ok, "warnings": rr.warnings}, **result}
