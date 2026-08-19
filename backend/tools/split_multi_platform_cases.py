"""存量多端用例拆分：把 platforms 含多个端的用例按端拆成单端用例。

配套 pipeline._fanout_by_platform（生成侧已保证新用例单端），本脚本清理【历史存量】：
- 原用例保留（端裁成数组里第一个端），其 id/执行结果/操作记录都留着，只归属首端；
- 其余每个端各新建一条用例：内容(标题/步骤/预期/覆盖项/优先级…)整份拷贝，新 case_id、单端；
  新端需要独立跑，故重置 last_status/script/script_status（不继承原用例的脚本与执行态）。

默认 dry-run 只打印计划；加 --commit 才落库。可按项目/需求/case_id 前缀过滤，缩小范围到「一物一码」。

用法（在 backend/ 下）：
    python3 -m tools.split_multi_platform_cases                          # 全库 dry-run
    python3 -m tools.split_multi_platform_cases --project <PROJECT_ID>   # 只看某项目
    python3 -m tools.split_multi_platform_cases --prefix TC-ZN- --commit # 一物一码, 落库
    python3 -m tools.split_multi_platform_cases --requirement <REQ_ID> --commit
"""
from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import func, select

from app.database import AsyncSessionLocal
from app.models import Project, TestCase, TestCaseLog


def _snapshot(tc: TestCase) -> dict:
    return {
        "case_id": tc.case_id, "title": tc.title, "priority": tc.priority,
        "case_type": tc.case_type, "modules": list(tc.modules or []),
        "platforms": list(tc.platforms or []), "expected_result": tc.expected_result,
        "last_status": tc.last_status,
    }


# 拷进新端副本的内容列（排除 id/case_id/执行态/脚本/时间戳/软删/相似标记等实例专属列）
_COPY_COLS = (
    "project_id", "requirement_id", "slice_id", "product_line", "source_req_id",
    "modules", "title", "priority", "preconditions", "steps", "expected_result",
    "source_issue_point", "secondary_feature", "case_type", "in_library",
    "tags", "covered_items", "sources", "risk_tags", "matched_rules", "reason",
    "affected_page_nodes", "affected_api_nodes",
)


async def _max_seq_map(db, prefix_by_project: dict[str, str]) -> dict[str, int]:
    """每个项目当前 TC-{prefix}- 的最大序号，供新 case_id 自增。"""
    out: dict[str, int] = {}
    for pid, prefix in prefix_by_project.items():
        rows = (await db.execute(
            select(TestCase.case_id).where(
                TestCase.project_id == pid, TestCase.case_id.like(f"{prefix}%"),
            )
        )).scalars().all()
        mx = 0
        for cid in rows:
            suf = cid[len(prefix):]
            if suf.isdigit():
                mx = max(mx, int(suf))
        out[pid] = mx
    return out


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", help="只处理该 project_id")
    ap.add_argument("--requirement", help="只处理该 requirement_id")
    ap.add_argument("--prefix", help="只处理 case_id 以此开头的用例，如 TC-ZN-")
    ap.add_argument("--commit", action="store_true", help="真正落库（默认 dry-run）")
    args = ap.parse_args()

    async with AsyncSessionLocal() as db:
        q = select(TestCase).where(
            TestCase.deleted_at.is_(None),
            func.array_length(TestCase.platforms, 1) > 1,
        ).order_by(TestCase.case_id)
        if args.project:
            q = q.where(TestCase.project_id == args.project)
        if args.requirement:
            q = q.where(TestCase.requirement_id == args.requirement)
        if args.prefix:
            q = q.where(TestCase.case_id.like(f"{args.prefix}%"))
        multi = (await db.execute(q)).scalars().all()

        if not multi:
            print("没有多端用例，无需拆分。")
            return

        # 项目前缀 & 起始序号
        pids = {tc.project_id for tc in multi}
        projects = {p.id: p for p in (await db.execute(
            select(Project).where(Project.id.in_(pids))
        )).scalars().all()}
        prefix_by_project = {pid: f"TC-{projects[pid].case_id_prefix}-" for pid in pids}
        seq = await _max_seq_map(db, prefix_by_project)

        new_count = 0
        print(f"待拆分多端用例：{len(multi)} 条\n")
        for tc in multi:
            plats = list(tc.platforms or [])
            keep, rest = plats[0], plats[1:]
            print(f"  {tc.case_id}  {plats}  ->  保留[{keep}] + 新建{rest}  «{tc.title[:30]}»")
            tc.platforms = [keep]
            if args.commit:
                db.add(TestCaseLog(test_case_id=tc.id, operation="update",
                                   operator="系统·多端拆分", snapshot=_snapshot(tc)))
            for p in rest:
                seq[tc.project_id] += 1
                new_case_id = f"{prefix_by_project[tc.project_id]}{seq[tc.project_id]:04d}"
                data = {col: getattr(tc, col) for col in _COPY_COLS}
                # ARRAY/JSONB 深拷贝，避免多副本共享同一列表对象
                for col in ("modules", "preconditions", "steps", "covered_items",
                            "sources", "risk_tags", "matched_rules"):
                    v = data.get(col)
                    if isinstance(v, list):
                        data[col] = list(v)
                new_tc = TestCase(
                    **data, platforms=[p], case_id=new_case_id,
                    last_status="not_run", script_status="pending", is_automated=False,
                )
                new_count += 1
                if args.commit:
                    db.add(new_tc)
                    await db.flush()
                    db.add(TestCaseLog(test_case_id=new_tc.id, operation="create",
                                       operator="系统·多端拆分", snapshot=_snapshot(new_tc)))

        print(f"\n汇总：{len(multi)} 条多端用例 -> 原地裁成单端 {len(multi)} 条 + 新建 {new_count} 条单端。")
        if args.commit:
            await db.commit()
            print("✅ 已落库。")
        else:
            print("（dry-run，未落库；确认无误后加 --commit 执行）")


if __name__ == "__main__":
    asyncio.run(main())
