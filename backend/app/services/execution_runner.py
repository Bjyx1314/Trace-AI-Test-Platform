"""RealExecutionRunner —— 整批真实执行器（详设 P1）。

与 MockExecutionRunner 同构（同样的状态流转、落库、门禁、缺陷分析、飞书通知收尾），
区别只在「单条用例如何判定」：本执行器用 build_runner(case) 选出真实 Runner（接口走
ApiRunner 真跑；未就绪端回退 MockRunner），把 RunOutcome 写入 TestResult。

队列 worker 调用 run_execution(execution_id, case_ids, run_mode) 即可。
"""
from __future__ import annotations

import re
import asyncio
import logging
from datetime import datetime

from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Execution, TestResult, TestCase, Requirement, Project
from app.services.runners.base import RunContext, RunOutcome
from app.services.runners.factory import build_runner
from app.services.defect_review import create_defect_for_failure, resolve_open_defects_for_case

logger = logging.getLogger(__name__)

# 批内执行顺序：操作类用例先跑(造数据)，查询类后跑(才有数据可验)。关键词启发式判定。
_OP_KEYWORDS = [
    "创建", "新增", "添加", "新建", "录入", "登记", "发起", "提交", "保存", "上传", "导入",
    "修改", "编辑", "更新", "变更", "删除", "移除", "作废", "取消", "撤销",
    "分派", "指派", "派单", "派工", "调度", "确认", "接单", "下单", "支付", "结算",
    "审批", "通过", "驳回", "退场", "进场", "绑定", "解绑", "开通", "注册", "生成",
    "转单", "转订单", "转联营", "转化", "转交", "立项", "建单", "开单", "签约",
    "收款", "付款", "入库", "出库", "盖章", "核销", "下发", "归档",
]
_QUERY_KEYWORDS = [
    "查询", "查看", "列表", "详情", "搜索", "检索", "筛选", "统计", "报表",
    "导出", "校验", "核对", "展示", "显示", "查找", "浏览", "对账",
]


def _case_kind_rank(tc) -> int:
    """0=操作类(先跑) / 1=未知 / 2=查询类(后跑)。先看标题，标题不明确再看步骤文本。"""
    title = tc.title or ""
    op_t = any(k in title for k in _OP_KEYWORDS)
    q_t = any(k in title for k in _QUERY_KEYWORDS)
    if op_t and not q_t:
        return 0
    if q_t and not op_t:
        return 2
    text = " ".join(
        f"{s.get('action', '')}{s.get('expected', '')}" for s in (tc.steps or []) if isinstance(s, dict)
    )
    opc = sum(text.count(k) for k in _OP_KEYWORDS)
    qc = sum(text.count(k) for k in _QUERY_KEYWORDS)
    if opc > qc:
        return 0
    if qc > opc:
        return 2
    return 1


# 功能块归类(从标题提取)：用于需求详情批量执行时「按功能块分组、组内操作先于查询」。
_SYS_QUALIFIERS = [
    "管理后台", "用户门户", "业务后台", "Web端", "PC端", "APP端", "App端", "小程序", "工作台", "首页",
]
_VIEW_DESCRIPTORS = ["记录报表", "记录", "报表", "详情", "列表", "明细", "看板", "页面"]
# 让功能块更细的“对象扩展词”（如 用户 vs 用户模板）；这些词跟在基础名词后构成独立对象。
_OBJECT_EXT = ["计划", "订单", "合同", "模板", "方案", "报告", "台账", "档案", "工单", "单据"]


def _feature_key(title: str) -> str:
    """从标题提取「功能块」键：去掉系统/平台限定词、操作/查询动词、视图描述词后，
    取核心业务对象。默认取前 2 个汉字；若紧跟"对象扩展词"(计划/订单…)则取前 4 个，
    以便区分订单与订单计划，同时把记录/详情等视图描述折叠回核心对象。
    """
    t = title or ""
    for s in _SYS_QUALIFIERS:
        t = t.replace(s, "")
    for v in _OP_KEYWORDS + _QUERY_KEYWORDS:
        t = t.replace(v, "")
    for w in _VIEW_DESCRIPTORS:
        t = t.replace(w, "")
    core = re.sub(r"[^一-龥A-Za-z0-9]", "", t)
    if not core:
        return (title or "")[:4]
    if core[2:4] in _OBJECT_EXT:
        return core[:4]
    return core[:2]


async def run_execution(execution_id: str, case_ids: list[str], run_mode: str = "fresh",
                        account_overrides: dict | None = None, reorder: bool = False,
                        ai_key: str | None = None, target_device: str | None = None,
                        env: str | None = None, package_overrides: dict | None = None):
    """整批真实执行入口。供 RQ worker 或 BackgroundTasks 调用。
    ai_key=发起人 key(走其自己额度)；target_device=App 指定真机 serial(空则兜底默认设备)；
    env=PC 执行环境(sit/dev，默认 sit)，决定 base_url 取哪个环境的地址；
    package_overrides={app端: 包版本id}，App 执行前换测试包(卸旧装新)。"""
    from app.agents.llm import set_current_ai_key
    set_current_ai_key(ai_key)
    async with AsyncSessionLocal() as db:
        ex = await db.get(Execution, execution_id)
        if not ex:
            return

        ex.status = "running"
        ex.execution_mode = "real"
        await db.commit()

        # 关联需求置为"测试中"
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

        passed = failed = skipped = errored = 0
        total_ms = 0
        ctx = RunContext(execution_id=execution_id, project_id=ex.project_id)
        ctx.extra["ai_key"] = ai_key  # App 派发时随任务下发给 worker，用发起人 key 执行
        ctx.extra["target_device"] = target_device  # App 指定真机(空=兜底默认设备)

        # App 换测试包：按用例所属 app 端解析「包版本 → 下载来源 + 旧包名」。执行前推到真机卸旧装新。
        # 旧包名优先用「枚举管理 → 端 → 应用包名」(app_package 分类)配的，取不到再看接口返回，最后由 apk 解析。
        _pkg_ov = package_overrides or {}

        def _resolve_apk_for(tc):
            if not _pkg_ov:
                return None
            from app.services.app_packages import resolve_package
            for p in (tc.platforms or []):
                pid = _pkg_ov.get(p)
                if not pid:
                    continue
                info = resolve_package(p, pid)
                if info and info.get("source"):
                    return {"source": info["source"], "package": info.get("package") or app_pkg_map.get(p)}
            return None
        # PC web 执行需要被测系统地址。按「端 → base_url」一一对应解析：
        #   主源：枚举管理，按所选环境 env 取对应分类(sit→base_url，dev→base_url_dev，见 environments.py)
        #   回退：所选环境某端未配地址 → 回退该端 SIT 地址并写日志(不静默不阻断)
        #   兜底：都没有 → 该项目页面缓存里任意 base_url
        from app.models import PageStructureCache, EnumDefinition
        from app.services.environments import env_category, normalize_env, env_label
        _env = normalize_env(env)

        async def _url_map(category: str) -> dict[str, str]:
            return {
                e.key: (e.label or "").strip()
                for e in (await db.execute(
                    select(EnumDefinition).where(EnumDefinition.category == category)
                )).scalars().all()
                if (e.label or "").strip()
            }

        base_url_map = await _url_map(env_category(_env))       # 所选环境
        sit_url_map = base_url_map if _env == "sit" else await _url_map("base_url")  # 回退用 SIT
        app_pkg_map = await _url_map("app_package")             # app端→应用包名(枚举管理配置)，换包卸旧包用
        fallback_base_url = (await db.execute(
            select(PageStructureCache.base_url)
            .where(PageStructureCache.project_id == ex.project_id, PageStructureCache.base_url.is_not(None))
            .limit(1)
        )).scalar()

        def _resolve_base_url(tc) -> str | None:
            plats = tc.platforms or []
            for p in plats:
                if p in base_url_map:
                    return base_url_map[p]
                # 所选环境缺该端地址 → 回退该端 SIT 地址并记日志
                if p in sit_url_map:
                    logger.info("端「%s」未配置「%s」环境地址，回退使用 SIT 地址执行(用例 %s)",
                                p, env_label(_env), getattr(tc, "id", "?"))
                    return sit_url_map[p]
            # 接口端(platforms 含 backend_api)：在枚举管理 base_url 组建一条 key=api 或 backend_api
            # 填接口网关地址，AI 直连执行时作为 base_url 拼接
            if "backend_api" in plats:
                return base_url_map.get("api") or sit_url_map.get("api") \
                    or base_url_map.get("backend_api") or sit_url_map.get("backend_api") or fallback_base_url
            return fallback_base_url

        # 载入该项目已缓存的「导航目录」(菜单树) + 已知页面名，执行时按端注入 AI 提示，
        # 让 AI 照菜单直接定位、减少探索耗时(用户诉求:缓存要能省下次执行的探索时间)。
        nav_by_url: dict[str, str] = {}
        pages_by_url: dict[str, list[str]] = {}
        _caches = (await db.execute(
            select(PageStructureCache).where(
                PageStructureCache.project_id == ex.project_id,
                PageStructureCache.status == "active",
            )
        )).scalars().all()
        for c in _caches:
            key = (c.base_url or "").rstrip("/")
            if not key:
                continue
            pages_by_url.setdefault(key, []).append(c.page_name)
            for reg in (c.regions or []):
                if reg.get("kind") == "menu" and reg.get("elements") and key not in nav_by_url:
                    lines = []
                    for m in reg["elements"]:
                        ind = "  " * int(m.get("level", 0) or 0)
                        mark = "▸ " if m.get("type") == "submenu" else "· "
                        lines.append(f"{ind}{mark}{m.get('name', '')}")
                    nav_by_url[key] = "\n".join(lines[:120])

        def _nav_hint(base_url: str | None):
            key = (base_url or "").rstrip("/")
            return nav_by_url.get(key), (pages_by_url.get(key) or None)

        # 登录态：直接复用 PC 自动化框架的登录机制(失效/新端自动重登)，按用例 platforms 解析。
        # 同一批执行里每个(端,账号)只 ensure 一次(缓存)，避免重复探测/重登。
        # account_overrides[端] = {role} 选已配账号 | {username,password,tenant_name?} 临时账号(用完即弃)。
        from pathlib import Path as _Path
        from app.services.web_login import ensure_login_state, login_temp, launch_args_for
        _overrides = account_overrides or {}
        _login_cache: dict[str, str | None] = {}
        _temp_files: list[str] = []
        _tmp_dir = _Path(__file__).resolve().parents[2] / "login_tmp"

        async def _resolve_storage_state(tc) -> str | None:
            for p in (tc.platforms or []):
                ov = _overrides.get(p) or {}
                if ov.get("username"):
                    key = f"{p}::temp"
                    if key not in _login_cache:
                        out = str((_tmp_dir / f"{execution_id}__{p}.json").resolve())
                        try:
                            ok = await login_temp(p, ov.get("username", ""), ov.get("password", ""),
                                                  out, ov.get("tenant_name"))
                        except Exception:
                            ok = False
                        _login_cache[key] = out if ok else None
                        if ok:
                            _temp_files.append(out)
                    if _login_cache[key]:
                        return _login_cache[key]
                else:
                    role = ov.get("role") or "default"
                    key = f"{p}::{role}"
                    if key not in _login_cache:
                        try:
                            _login_cache[key] = await ensure_login_state(p, role)
                        except Exception:
                            _login_cache[key] = None
                    if _login_cache[key]:
                        return _login_cache[key]
            return None

        # 批内排序(仅需求详情批量执行 reorder=True；用例库不排序)：
        # 按功能块分组(标题提取，首次出现序) → 组内操作类先于查询类 → 原序，稳定。
        # 效果如：用户模板(操作→查询) → 用户(操作→查询)，让操作先造数据、查询才有数据可验。
        if reorder:
            _feat_first: dict[str, int] = {}
            _sortinfo: dict[str, tuple] = {}
            for _idx, cid in enumerate(case_ids):
                _tc = await db.get(TestCase, cid)
                _fk = _feature_key(_tc.title) if _tc else ""
                if _fk not in _feat_first:
                    _feat_first[_fk] = len(_feat_first)
                _sortinfo[cid] = (_feat_first[_fk], _case_kind_rank(_tc) if _tc else 1, _idx)
            case_ids = sorted(case_ids, key=lambda c: _sortinfo[c])

        try:
            for case_id in case_ids:
                tc = await db.get(TestCase, case_id)
                if not tc:
                    continue

                # App 换包信息（按用例 app 端解析）；随任务下发给 worker / Sonic，执行前卸旧装新
                ctx.extra["apk"] = _resolve_apk_for(tc)
                # App 目标应用包名（枚举「端→应用包名」配置）；执行前按此直接启动 App，AI 不用在桌面找、避免找错 App
                ctx.extra["app_package"] = next(
                    (app_pkg_map[p] for p in (tc.platforms or []) if app_pkg_map.get(p)), None
                )
                # 按用例所属端解析被测 PC 地址 + 登录态 + 浏览器启动参数(web 执行用)
                ctx.base_url = _resolve_base_url(tc)
                ctx.extra["storage_state"] = await _resolve_storage_state(tc)
                ctx.extra["browser_args"] = next(
                    (launch_args_for(p) for p in (tc.platforms or []) if launch_args_for(p)), []
                )
                _nav, _pages = _nav_hint(ctx.base_url)
                ctx.extra["nav_menu"] = _nav
                ctx.extra["known_pages"] = _pages

                # 优先「仓库内执行」：用例已绑定到已 checkout 的框架仓库时，跑框架自身命令；
                # 否则回退既有 build_runner（temp 脚本模型 / 未就绪端回退 Mock）。
                from app.services.frameworks.repos import runner_for_case
                runner = await runner_for_case(db, tc) or build_runner(tc)
                # 远程真机(Sonic)：目标设备为 "sonic:<udId>" 时，改走 SonicRunner 在进程内
                # 占用→adb connect→复用 AndroidAgentRunner→释放（无需本地真机/worker）
                if str((ctx.extra or {}).get("target_device") or "").startswith("sonic:"):
                    from app.services.runners.sonic_runner import SonicRunner
                    runner = SonicRunner()
                # 单条用例超时兜底：避免某条挂起(AI/浏览器/真机无响应)拖死整批、状态永久卡在 running。
                try:
                    outcome = await asyncio.wait_for(
                        runner.run(tc, ctx), timeout=settings.case_exec_timeout_sec
                    )
                except asyncio.TimeoutError:
                    logger.warning("execution %s 用例 %s 执行超时(%ss)", execution_id, case_id,
                                   settings.case_exec_timeout_sec)
                    outcome = RunOutcome(
                        status="error",
                        duration_ms=settings.case_exec_timeout_sec * 1000,
                        error_message=f"执行超时（超过 {settings.case_exec_timeout_sec} 秒未完成，已中断），请重试",
                        failure_type="env_error",
                    )

                # 统计：error 计入 failed 桶（门禁/通过率口径与 mock 一致），但 status 保留 error
                if outcome.status == "passed":
                    passed += 1
                elif outcome.status == "skipped":
                    skipped += 1
                elif outcome.status == "error":
                    errored += 1
                else:
                    failed += 1
                total_ms += outcome.duration_ms

                tr = TestResult(
                    execution_id=execution_id,
                    test_case_id=case_id,
                    status=outcome.status,
                    duration_ms=outcome.duration_ms,
                    error_message=outcome.error_message,
                    failure_type=outcome.failure_type,
                    screenshot_url=outcome.screenshot_url,
                    api_trace=outcome.api_trace,
                    ui_trace=outcome.ui_trace,
                    defect_status="pending_review" if outcome.status in ("failed", "error") else "none",
                )
                db.add(tr)
                tc.last_status = outcome.status
                if outcome.status == "passed":
                    tc.in_library = True  # 执行通过：纳入用例库(单向，永久保留)

                # 执行时抓到的页面结构 → 自动补充/刷新页面结构缓存(新页面自动写入)
                if outcome.page_captures:
                    from app.services.page_cache_service import upsert_from_execution
                    for cap in outcome.page_captures:
                        try:
                            await upsert_from_execution(
                                db, project_id=ex.project_id, url=cap.get("url", ""),
                                page_name=cap.get("page_name", ""), regions=cap.get("regions", []),
                                base_url=ctx.base_url,
                            )
                        except Exception:
                            pass

                # 失败 → 生成待复核缺陷；通过 → 自动复核既有缺陷为已解决
                # 口径：只要用例「失败(failed)」就建复核——含「无法验证(blocked/env_error)」，
                # 让所有未通过的执行都能在缺陷复核里看到。仅「无法运行(status=error，如打不开
                # 浏览器/无地址/超时等纯环境问题)」不建单。
                await db.flush()
                _build_defect = (
                    outcome.status == "failed"
                    or (outcome.status == "error" and outcome.failure_type != "env_error")
                )
                if _build_defect:
                    await create_defect_for_failure(db, tr, tc)
                elif outcome.status == "passed":
                    await resolve_open_defects_for_case(db, case_id, note="再次执行通过，缺陷已解决")

            await _finalize_execution(
                db, ex, passed=passed, failed=failed + errored, skipped=skipped,
                total_ms=total_ms, req_ids=req_ids, execution_id=execution_id,
            )
        except Exception as e:
            # 批次级异常(runner 抛错/收尾/门禁/DB)：必须收口到终态 failed，否则状态永久卡在 running
            # 而无法重试。原 session 此时可能已损坏，用新 session 落库失败原因。
            logger.exception("execution %s 批次执行异常，置为 failed", execution_id)
            try:
                await db.rollback()
            except Exception:
                pass
            try:
                async with AsyncSessionLocal() as db2:
                    ex2 = await db2.get(Execution, execution_id)
                    if ex2 and ex2.status not in ("done", "failed"):
                        ex2.status = "failed"
                        ex2.finished_at = datetime.now()
                        ex2.error_message = f"执行异常：{e}"[:2000]
                        await db2.commit()
            except Exception:
                logger.exception("execution %s 失败状态落库也失败", execution_id)
        finally:
            # 临时账号登录态用完即删(含 cookie，绝不残留)
            for _f in _temp_files:
                try:
                    _Path(_f).unlink(missing_ok=True)
                except Exception:
                    pass


async def _finalize_execution(db, ex, *, passed, failed, skipped, total_ms, req_ids, execution_id):
    """执行收尾：统计、质量门禁、缺陷分析、需求完成判定、飞书通知。

    与 MockExecutionRunner 收尾逻辑一致；抽成公共函数便于 mock/real 复用。
    """
    total = passed + failed + skipped
    ex.passed = passed
    ex.failed = failed
    ex.skipped = skipped
    ex.total = total
    ex.pass_rate = round((passed / total * 100) if total > 0 else 0.0, 2)
    ex.duration_ms = total_ms
    ex.status = "done"
    ex.finished_at = datetime.now()

    proj = await db.get(Project, ex.project_id)
    await db.flush()  # 使新写入的 TestResult 对门禁引擎 JOIN 可见

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
