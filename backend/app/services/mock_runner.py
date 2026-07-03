"""Mock execution engine — simulates test run without real runner."""
from __future__ import annotations
import asyncio
import random
from datetime import datetime
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Execution, TestResult, TestCase, PageStructureCache
from app.agents import ScriptGeneratorAgent
from app.agents.script_generator import determine_script_type
from app.services.page_cache_service import normalize_url, compute_region_hashes
from app.services.automation_switch import is_generation_enabled
from app.services.defect_review import create_defect_for_failure, resolve_open_defects_for_case

script_gen = ScriptGeneratorAgent()


def _mock_screenshot(status: str) -> str:
    """UI 端执行最终结果截图（mock 用占位图，按结果着色）。"""
    color = {"passed": "16A34A", "failed": "EF4444", "skipped": "E8930C"}.get(status, "64748B")
    label = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(status, status).upper()
    return f"https://placehold.co/900x520/{color}/FFFFFF/png?text={label}"


def _mock_api_trace(tc, status: str) -> dict:
    """接口端执行请求/返回 + x-hubble-trace-id（mock）。"""
    import uuid as _uuid
    trace_id = _uuid.uuid4().hex
    ok = status == "passed"
    return {
        "trace_id": trace_id,
        "request": {
            "method": "POST",
            "url": f"/api/{(tc.modules or ['demo'])[0]}/action",
            "headers": {"Content-Type": "application/json", "x-hubble-trace-id": trace_id},
            "body": {"caseId": tc.case_id, "params": {"sample": "value"}},
        },
        "response": {
            "status": 200 if ok else 500,
            "headers": {"Content-Type": "application/json", "x-hubble-trace-id": trace_id},
            "body": {"code": 0, "data": {"ok": True}} if ok else {"code": 500, "message": "内部错误：断言失败"},
        },
    }


class MockExecutionRunner:
    async def run(self, execution_id: str, case_ids: list[str], run_mode: str = "fresh"):
        async with AsyncSessionLocal() as db:
            ex = await db.get(Execution, execution_id)
            if not ex:
                return

            ex.status = "running"
            await db.commit()

            # 收集关联需求ID并置为"测试中"
            from app.models import Requirement
            req_ids: set[str] = set()
            for case_id in case_ids:
                tc = await db.get(TestCase, case_id)
                if tc and tc.requirement_id:
                    req_ids.add(tc.requirement_id)
            for req_id in req_ids:
                req = await db.get(Requirement, req_id)
                if req and req.status in ("pending_test", "testing"):
                    req.status = "testing"
            if req_ids:
                await db.commit()

            passed = failed = skipped = 0
            total_ms = 0

            for case_id in case_ids:
                tc = await db.get(TestCase, case_id)
                if not tc:
                    continue

                await asyncio.sleep(random.uniform(0.05, 0.2))
                roll = random.random()
                if roll < 0.75:
                    status = "passed"
                    passed += 1
                elif roll < 0.90:
                    status = "failed"
                    failed += 1
                else:
                    status = "skipped"
                    skipped += 1

                dur = random.randint(100, 2000)
                total_ms += dur

                # 自动化用例生成开关（按端）：执行通过后是否生成自动化用例，由系统管理处
                # 的分端开关控制（仅管理员可改）。对应端关闭则跳过生成（开关缺省视为开启，
                # 保持历史行为）。
                script_type = determine_script_type(tc.case_type, tc.platforms)
                if (
                    status == "passed"
                    and run_mode != "automated"
                    and await is_generation_enabled(db, script_type)
                ):
                    tc.script_status = "generating"
                    await db.commit()
                    tc.script = await script_gen.generate_script({
                        "title": tc.title,
                        "preconditions": tc.preconditions,
                        "steps": [{"step": s.get("action", ""), "expected": s.get("expected", "")} for s in tc.steps],
                        "expected_result": tc.expected_result,
                    }, script_type)
                    tc.script_status = "ready"
                    tc.is_automated = True
                    await db.commit()

                # 执行情况展示物料：UI端(pc/移动/小程序)截图；接口端请求/返回/trace
                is_api = script_type in ("api", "backend_api") or tc.case_type == "api"
                screenshot_url = None if is_api else _mock_screenshot(status)
                api_trace = _mock_api_trace(tc, status) if is_api else None

                tr = TestResult(
                    execution_id=execution_id,
                    test_case_id=case_id,
                    status=status,
                    duration_ms=dur,
                    error_message="AssertionError: expected value mismatch" if status == "failed" else None,
                    screenshot_url=screenshot_url,
                    api_trace=api_trace,
                    defect_status="pending_review" if status == "failed" else "none",
                )
                db.add(tr)
                tc.last_status = status
                if status == "passed":
                    tc.in_library = True  # 执行通过：纳入用例库(单向，永久保留)

                # 失败 → 生成待复核缺陷；通过 → 自动复核既有缺陷为已解决
                await db.flush()
                if status == "failed":
                    await create_defect_for_failure(db, tr, tc)
                elif status == "passed":
                    await resolve_open_defects_for_case(db, case_id, note="再次执行通过，缺陷已解决")

                # 执行中发现未缓存的 web/UI 页面时自动写入共享缓存（7.3.1 / 7.3.6 新页面自动写入）
                if tc.case_type == "ui" and "web" in (tc.platforms or []):
                    module = (tc.modules or [""])[0] if tc.modules else ""
                    derived_url = f"/{module}/{{id}}" if module else "/unknown"
                    pattern = normalize_url(derived_url)
                    cached = (await db.execute(
                        select(PageStructureCache.id).where(
                            PageStructureCache.project_id == ex.project_id,
                            PageStructureCache.url_pattern == pattern,
                        )
                    )).scalar_one_or_none()
                    if cached is None:
                        auto_regions = [{
                            "name": f"{module or '未知'}功能区",
                            "selector": f".{module}-container" if module else ".main-content",
                            "elements": [
                                {"name": "内容主体", "selector": ".content-area", "type": "container"},
                                {"name": "操作按钮组", "selector": ".action-group", "type": "button-group"},
                            ],
                        }]
                        db.add(PageStructureCache(
                            project_id=ex.project_id,
                            url_pattern=pattern,
                            page_name=f"{tc.title[:30]}（执行中发现）",
                            dom_hash=compute_region_hashes(auto_regions),
                            regions=auto_regions,
                            status="active",
                        ))

            total = passed + failed + skipped
            pass_rate = (passed / total * 100) if total > 0 else 0.0

            ex.passed = passed
            ex.failed = failed
            ex.skipped = skipped
            ex.total = total
            ex.pass_rate = round(pass_rate, 2)
            ex.duration_ms = total_ms
            ex.status = "done"
            ex.finished_at = datetime.now()

            # CI 质量门禁判定
            from app.models import Project
            proj = await db.get(Project, ex.project_id)

            # flush，使本次执行新写入的TestResult对规则引擎的JOIN查询可见（规则3）
            await db.flush()

            if proj is not None:
                from app.services.quality_gate_engine import evaluate_gate
                ex.ci_gate_result = await evaluate_gate(db, ex)
            else:
                ex.ci_gate_result = {"releasable": True, "blocking_reasons": []}

            from app.services.result_analyzer import analyze_failed_results
            await analyze_failed_results(db, execution_id)

            from app.services.requirement_status import apply_requirement_completion
            for req_id in req_ids:
                await apply_requirement_completion(db, req_id)

            await db.commit()

            # Feishu notification
            if proj and proj.feishu_webhook:
                from app.services.feishu import send_feishu_notification
                gate_text = "PASS" if ex.ci_gate_result["releasable"] else "FAIL"
                await send_feishu_notification(
                    webhook_url=proj.feishu_webhook,
                    title=f"测试执行完成: {ex.name}",
                    content=(
                        f"**项目**: {proj.name}\n"
                        f"**通过率**: {ex.pass_rate:.1f}%\n"
                        f"**通过**: {ex.passed} / **失败**: {ex.failed} / **跳过**: {ex.skipped}\n"
                        f"**门禁结果**: {gate_text}"
                    ),
                    pass_rate=ex.pass_rate,
                )
