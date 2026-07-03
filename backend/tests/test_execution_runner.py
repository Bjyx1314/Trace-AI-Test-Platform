"""RealExecutionRunner 集成测试 —— 连真实 PostgreSQL，验证逐用例真实执行并落库。

聚焦本步骤新增逻辑：run_execution 用 build_runner 选 Runner、逐用例真跑、落 TestResult、
统计 passed/failed/skipped。收尾（门禁/缺陷/飞书）用 monkeypatch stub，避免引入重依赖。

零污染：用唯一前缀的临时 id，测试结束删除自己造的数据。
PG 不可达时 skip（保持纯逻辑测试可在无 DB 环境运行的约定）。
"""
import asyncio
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PG_URL = "postgresql+asyncpg://testplatform:testplatform@localhost:5432/test_platform"


def _pg_reachable() -> bool:
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    async def _ping():
        e = create_async_engine(PG_URL)
        try:
            async with e.connect() as c:
                await c.execute(text("select 1"))
            return True
        except Exception:
            return False
        finally:
            await e.dispose()
    try:
        return asyncio.run(_ping())
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _pg_reachable(), reason="PostgreSQL 不可达，跳过真实执行集成测试")


@pytest.fixture
def pg_session(monkeypatch):
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    import app.services.execution_runner as er

    engine = create_async_engine(PG_URL)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(er, "AsyncSessionLocal", Session)

    async def _noop_finalize(db, ex, **kw):
        ex.passed = kw["passed"]; ex.failed = kw["failed"]; ex.skipped = kw["skipped"]
        ex.total = kw["passed"] + kw["failed"] + kw["skipped"]
        ex.status = "done"
        await db.commit()
    monkeypatch.setattr(er, "_finalize_execution", _noop_finalize)

    from app.config import settings
    monkeypatch.setattr(settings, "execution_mode", "real")
    monkeypatch.setattr(settings, "runner_api_enabled", True)
    return Session, engine


def test_run_execution_real_api(pg_session):
    from app.services.execution_runner import run_execution
    from app.models import Project, Execution, TestCase, TestResult, Defect
    from sqlalchemy import select, delete

    Session, _ = pg_session
    tag = uuid.uuid4().hex[:8]
    pid, eid = f"itp-{tag}", f"ite-{tag}"
    PASS = "def test_ok():\n    assert True\n"
    FAIL = "def test_bad():\n    assert False\n"

    async def _scenario():
        # 单一事件循环内完成 seed → 执行 → 校验 → 清理，避免 asyncpg 连接池跨循环失效
        async with Session() as db:
            db.add(Project(id=pid, name=f"it-{tag}"))
            db.add(Execution(id=eid, project_id=pid, name="run", status="pending", total=2))
            cids = []
            for i, sc in enumerate([PASS, FAIL]):
                cid = f"itc-{tag}-{i}"
                db.add(TestCase(
                    id=cid, case_id=f"TC-IT-{tag}-{i}", project_id=pid,
                    title=f"case{i}", case_type="api", platforms=["backend_api"], script=sc,
                ))
                cids.append(cid)
            await db.commit()

        try:
            await run_execution(eid, cids, "fresh")
            async with Session() as db:
                rows = (await db.execute(
                    select(TestResult).where(TestResult.execution_id == eid)
                )).scalars().all()
                statuses = {r.test_case_id: (r.status, r.failure_type) for r in rows}
            assert statuses[cids[0]][0] == "passed"
            assert statuses[cids[1]][0] == "failed"
            assert statuses[cids[1]][1] == "real_defect"
        finally:
            async with Session() as db:
                result_ids = select(TestResult.id).where(TestResult.execution_id == eid)
                await db.execute(delete(Defect).where(Defect.test_result_id.in_(result_ids)))
                await db.execute(delete(TestResult).where(TestResult.execution_id == eid))
                await db.execute(delete(TestCase).where(TestCase.project_id == pid))
                await db.execute(delete(Execution).where(Execution.id == eid))
                await db.execute(delete(Project).where(Project.id == pid))
                await db.commit()

    asyncio.run(_scenario())
