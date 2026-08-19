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
from app.services import execution_control

logger = logging.getLogger(__name__)


def _ai_diagnosis_from_outcome(outcome: RunOutcome) -> dict | None:
    data: dict[str, bool] = {}
    if outcome.apk_install_ok is not None:
        data["apk_install_ok"] = outcome.apk_install_ok
    if outcome.app_launch_by_package_ok is not None:
        data["app_launch_by_package_ok"] = outcome.app_launch_by_package_ok
    return data or None

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
    "金融与物流", "金融工作台", "物流工作台", "物流工作台", "业务模块", "工作台", "金融", "物流",
    "PC端", "APP端", "App端", "小程序", "工作台", "首页",
]
_VIEW_DESCRIPTORS = ["记录报表", "记录", "报表", "详情", "列表", "明细", "看板", "页面"]
# 让功能块更细的"对象扩展词"(如 任务 vs 任务计划)；这些词跟在基础名词后构成独立对象
_OBJECT_EXT = ["计划", "订单", "合同", "模板", "方案", "报告", "台账", "档案", "工单", "单据"]


def _api_spec_of(tc) -> dict:
    """取用例的结构化 API 定义（tags.api_spec）。

    tags 有两种形状：AI 生成的用例是【字符串标签数组】(如 ["接口","幂等"])，接口用例才是
    带 api_spec 的字典。历史上这里直接 .get 取值，遇到数组形状会 AttributeError 并把整批
    执行打断，所以只在确为 dict 时取。
    """
    tags = getattr(tc, "tags", None)
    if not isinstance(tags, dict):
        return {}
    spec = tags.get("api_spec")
    return spec if isinstance(spec, dict) else {}


def _is_api_case(tc, platform_group_map: dict[str, str] | None = None) -> bool:
    """接口用例判定：兼容 case_type 不是 api、但 platforms 标成「接口」的历史数据。"""
    return _resolve_case_runner_type(tc, platform_group_map) == "api"


def _resolve_case_runner_type(tc, platform_group_map: dict[str, str] | None = None) -> str:
    from app.services.runners.dispatcher import resolve_runner_type
    return resolve_runner_type(tc, platform_group_map)


def _misrouted_app_platforms(tc, platform_group_map: dict[str, str] | None, runner_type: str) -> list[str]:
    if runner_type != "web":
        return []
    plats = getattr(tc, "platforms", None) or []
    gm = platform_group_map or {}
    # 含 PC 端(如 web-admin)时 web 路由是刻意的(PC 优先于 App)，不算误路由，放行
    if any(gm.get(p) == "pc" for p in plats):
        return []
    return [p for p in plats if gm.get(p) == "app"]


def _should_force_sonic_runner(tc, target_device: str | None, platform_group_map: dict[str, str] | None = None) -> bool:
    """仅移动端用例允许被 Sonic 远程真机接管，避免接口/PC 被 target_device 误带偏。"""
    return bool(str(target_device or "").startswith("sonic:") and _resolve_case_runner_type(tc, platform_group_map) == "android")


def _feature_key(title: str) -> str:
    """从标题提取「功能块」键：去掉系统/平台限定词、操作/查询动词、视图描述词后，
    取核心业务对象。默认取前 2 个汉字；若紧跟"对象扩展词"(计划/订单…)则取前 4 个，
    以便区分 任务 与 任务计划，同时把 任务记录/任务详情/任务方式 等折叠回 任务。
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
                        env: str | None = None, package_overrides: dict | None = None,
                        app_login: dict | None = None):
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
        ctx.extra["account_overrides"] = account_overrides or {}  # 接口执行：account_overrides["api"] 临时账号(像 PC 一样)
        ctx.extra["app_login"] = app_login or {}  # App 自动登录：{端key: {env, account, tenant, label}}，android_runner 装包后登录

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
        ctx.extra["env"] = _env  # 接口执行：登录/base_url(service 未配置字面量时)按此环境解析

        async def _url_map(category: str) -> dict[str, str]:
            return {
                e.key: (e.label or "").strip()
                for e in (await db.execute(
                    select(EnumDefinition).where(EnumDefinition.category == category)
                )).scalars().all()
                if (e.label or "").strip()
            }

        async def _platform_group_map() -> dict[str, str]:
            rows = (await db.execute(
                select(EnumDefinition).where(EnumDefinition.category == "platform")
            )).scalars().all()
            return {
                e.key: e.parent_key
                for e in rows
                if (e.key or "").strip() and (e.parent_key or "") in {"pc", "app", "miniprogram", "api"}
            }

        base_url_map = await _url_map(env_category(_env))       # 所选环境
        sit_url_map = base_url_map if _env == "sit" else await _url_map("base_url")  # 回退用 SIT
        app_pkg_map = await _url_map("app_package")             # app端→应用包名(枚举管理配置)，换包卸旧包用
        platform_group_map = await _platform_group_map()        # 端→执行口径（枚举管理 parent_key）
        fallback_base_url = (await db.execute(
            select(PageStructureCache.base_url)
            .where(PageStructureCache.project_id == ex.project_id, PageStructureCache.base_url.is_not(None))
            .limit(1)
        )).scalar()

        def _resolve_base_url(tc) -> str | None:
            # 接口用例：优先按结构化 API 定义(tags.api_spec.service)解析域名——免手填「代码仓库地址」，
            # service 命中框架 env_config 则查表，是 http(s) 字面量则直接透传(框架未收录的新域名)。
            if _is_api_case(tc, platform_group_map):
                spec = _api_spec_of(tc)
                if spec.get("service"):
                    from app.services.frameworks.interface_env import resolve_service_base_url
                    resolved = resolve_service_base_url(spec["service"], _env)
                    if resolved:
                        return resolved
                if spec.get("base_url"):
                    return str(spec["base_url"]).rstrip("/")
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
            if _is_api_case(tc, platform_group_map):
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
                            # 框架未覆盖端(如 web-admin)降级登录用被测地址：优先所选环境地址，回退 SIT
                            _p_base = base_url_map.get(p) or sit_url_map.get(p)
                            ok = await login_temp(p, ov.get("username", ""), ov.get("password", ""),
                                                  out, ov.get("tenant_name"), base_url=_p_base)
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
        # 效果如：任务计划(操作→查询) → 任务(操作→查询)，让操作先造数据、查询才有数据可验。
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

        from app.services.runners.sonic_runner import release_cached_sonic_sessions

        canceled = False
        ctx.extra["execution_id"] = execution_id  # 供 Runner 逐动作查取消标志/推实时日志
        execution_control.log(execution_id, f"开始执行，共 {len(case_ids)} 条用例")
        try:
            try:
                for _ci, case_id in enumerate(case_ids, start=1):
                    # 取消：在每条用例开始前检查，未跑的用例不再执行（已跑的结果保留）
                    if execution_control.is_canceled(execution_id):
                        canceled = True
                        execution_control.log(execution_id, f"已取消，剩余 {len(case_ids) - _ci + 1} 条未执行", "warn")
                        break
                    # 单条用例取消：只跳过这一条，批次其余继续（点单个用例的"取消测试"）
                    if execution_control.is_case_canceled(case_id):
                        execution_control.clear_case(case_id)
                        skipped += 1
                        execution_control.log(execution_id, f"[{_ci}/{len(case_ids)}] 该用例已取消，跳过", "warn", case_id=case_id)
                        continue
                    # 单条用例的意外异常【不能打断整批】：历史上这里任一处抛错(如用例字段形状异常)都会
                    # 冒泡到批次级 except，把剩余未跑的用例全部丢掉。这里兜住并记为该条的 env_error，
                    # 批次继续跑下一条。
                    try:
                        tc = await db.get(TestCase, case_id)
                        if not tc:
                            continue
                        execution_control.log(execution_id, f"[{_ci}/{len(case_ids)}] 开始用例：{tc.title or case_id}", case_id=case_id)

                        # App 换包信息（按用例 app 端解析）；随任务下发给 worker / Sonic，执行前卸旧装新
                        ctx.extra["apk"] = _resolve_apk_for(tc)
                        # 换包决策落日志：装了什么 / 配了但解析失败 / 没配，排查「选了换包却没装」用
                        if any((platform_group_map.get(p) == "app") for p in (tc.platforms or [])):
                            _apk = ctx.extra["apk"]
                            if _apk and _apk.get("source"):
                                logger.info("execution %s 用例 %s 更换测试包：%s", execution_id, case_id, _apk["source"])
                            elif _pkg_ov:
                                logger.warning("execution %s 用例 %s 配了换包但未解析到 apk（package_overrides=%s, platforms=%s）",
                                               execution_id, case_id, _pkg_ov, tc.platforms)
                            else:
                                logger.info("execution %s 用例 %s 未配置更换测试包，使用设备现有包", execution_id, case_id)
                        # App 目标应用包名（枚举「端→应用包名」配置）；执行前按此直接启动 App，AI 不用在桌面找、避免找错 App
                        ctx.extra["app_package"] = next(
                            (app_pkg_map[p] for p in (tc.platforms or []) if app_pkg_map.get(p)), None
                        )
                        runner_type = _resolve_case_runner_type(tc, platform_group_map)
                        misrouted_apps = _misrouted_app_platforms(tc, platform_group_map, runner_type)
                        if misrouted_apps:
                            logger.error("execution %s 用例 %s 误路由为 web，app端=%s",
                                         execution_id, case_id, ",".join(misrouted_apps))
                            outcome = RunOutcome(
                                status="error",
                                duration_ms=0,
                                error_message=f"执行路由错误：用例端「{'、'.join(misrouted_apps)}」属于 App，但被识别为 PC/Web；已阻止兜底到其它站点执行",
                                failure_type="env_error",
                            )
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
                                ai_diagnosis=_ai_diagnosis_from_outcome(outcome),
                                checked_points=outcome.checked_points,
                                actual_visited_pages=outcome.actual_visited_pages,
                                actual_api_calls=outcome.actual_api_calls,
                                defect_status="pending_review",
                            )
                            db.add(tr)
                            tc.last_status = outcome.status
                            await db.commit()
                            continue
                        # 按用例所属端解析被测 PC 地址 + 登录态 + 浏览器启动参数(web 执行用)
                        ctx.base_url = _resolve_base_url(tc)
                        ctx.extra["storage_state"] = await _resolve_storage_state(tc)
                        ctx.extra["browser_args"] = next(
                            (launch_args_for(p) for p in (tc.platforms or []) if launch_args_for(p)), []
                        )
                        _nav, _pages = _nav_hint(ctx.base_url)
                        ctx.extra["nav_menu"] = _nav
                        ctx.extra["known_pages"] = _pages

                        # ── 数据前置（测试数据准备与状态编排 MVP-0）：把用例『数据要求』的实际值注入步骤/凭证。
                        # 缺必填数据（步骤里有 ${别名.字段} 却没配值）→ 直接判 setup_error，不进执行（不冤枉成产品缺陷）。
                        outcome = None
                        try:
                            from app.services.data_prep.context import prepare_case
                            _prep = await prepare_case(db, tc, environment=(ctx.extra or {}).get("env") or "sit")
                            if _prep.has_requirements:
                                if not _prep.ok:
                                    execution_control.log(execution_id, f"[{_ci}/{len(case_ids)}] 数据未准备好：{_prep.error_message}",
                                                          "warn", case_id=case_id)
                                    outcome = RunOutcome(status="error", duration_ms=0,
                                                         failure_type="setup_error", error_message=_prep.error_message)
                                else:
                                    ctx.extra["steps_override"] = _prep.steps_override
                                    ctx.extra["script_override"] = _prep.script_override
                                    ctx.extra["data_vars"] = _prep.variables
                                    ctx.extra["data_credentials"] = _prep.credentials
                                    if _prep.variables:
                                        execution_control.log(execution_id,
                                                              f"[{_ci}/{len(case_ids)}] 已注入数据变量 {len(_prep.variables)} 个",
                                                              case_id=case_id)
                        except Exception as _pe:  # noqa: BLE001 前置异常不吞成假通过，记 setup_error
                            logger.warning("execution %s 用例 %s 数据前置异常：%s", execution_id, case_id, _pe)
                            outcome = RunOutcome(status="error", duration_ms=0,
                                                 failure_type="setup_error", error_message=f"数据前置异常：{_pe}")

                        # 优先「仓库内执行」：用例已绑定到已 checkout 的框架仓库时，跑框架自身命令；
                        # 否则回退既有 build_runner（temp 脚本模型 / 未就绪端回退 Mock）。
                        from app.services.frameworks.repos import runner_for_case
                        runner = await runner_for_case(db, tc, platform_group_map) or build_runner(tc, platform_group_map=platform_group_map)
                        # 远程真机(Sonic)：目标设备为 "sonic:<udId>" 时，改走 SonicRunner 在进程内
                        # 占用→adb connect→复用 AndroidAgentRunner；整批结束后统一释放。
                        if _should_force_sonic_runner(tc, (ctx.extra or {}).get("target_device"), platform_group_map):
                            from app.services.runners.sonic_runner import SonicRunner
                            runner = SonicRunner()
                        # 单条用例执行：以 Task 方式跑，边等边【每 2s 轮询取消标志/超时】。
                        # 关键：Runner 可能卡在挂死的远程调用(截图/AI/adb 无响应)里，协作式的"动作前检查取消"
                        # 到不了 → 取消/超时就永远生效不了。这里直接 task.cancel() 打断挂起的 await，
                        # 让取消【即时生效】(约 2s 内)，不必干等挂死调用返回或拖到 30 分钟超时。
                        # 数据前置已判 setup_error → 跳过真正执行，直接用该 outcome。
                        run_task = None if outcome is not None else asyncio.ensure_future(runner.run(tc, ctx))
                        _deadline = asyncio.get_event_loop().time() + settings.case_exec_timeout_sec
                        while run_task is not None:
                            done, _ = await asyncio.wait({run_task}, timeout=2)
                            if run_task in done:
                                try:
                                    outcome = run_task.result()
                                except asyncio.CancelledError:
                                    outcome = RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                                                         error_message="已取消执行")
                                break
                            _batch_cancel = execution_control.is_canceled(execution_id)
                            _case_cancel = execution_control.is_case_canceled(case_id)
                            _cancel = _batch_cancel or _case_cancel
                            _timeout = asyncio.get_event_loop().time() > _deadline
                            if _cancel or _timeout:
                                run_task.cancel()
                                try:
                                    await run_task
                                except (asyncio.CancelledError, Exception):  # noqa: BLE001 打断挂起调用
                                    pass
                                if _case_cancel and not _batch_cancel:
                                    # 单条取消：这条记"跳过"(不算失败、不建缺陷)，批次继续跑后续用例
                                    execution_control.clear_case(case_id)
                                    outcome = RunOutcome(status="skipped", duration_ms=0,
                                                         error_message="已取消该用例执行")
                                elif _cancel:
                                    outcome = RunOutcome(status="error", duration_ms=0, failure_type="env_error",
                                                         error_message="已取消执行")
                                else:
                                    logger.warning("execution %s 用例 %s 执行超时(%ss)", execution_id, case_id,
                                                   settings.case_exec_timeout_sec)
                                    outcome = RunOutcome(
                                        status="error",
                                        duration_ms=settings.case_exec_timeout_sec * 1000,
                                        error_message=f"执行超时（超过 {settings.case_exec_timeout_sec} 秒未完成，已中断），请重试",
                                        failure_type="env_error",
                                    )
                                break

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
                        _rstat = {"passed": "通过", "skipped": "跳过", "error": "无法执行"}.get(outcome.status, "未通过")
                        execution_control.log(execution_id, f"[{_ci}/{len(case_ids)}] 用例结果：{_rstat}",
                                              "info" if outcome.status == "passed" else "warn", case_id=case_id)

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
                            ai_diagnosis=_ai_diagnosis_from_outcome(outcome),
                            # AI 质量闭环：覆盖项级执行证据（方案 12.1）
                            checked_points=outcome.checked_points,
                            actual_visited_pages=outcome.actual_visited_pages,
                            actual_api_calls=outcome.actual_api_calls,
                            defect_status="pending_review" if outcome.status in ("failed", "error") else "none",
                        )
                        db.add(tr)
                        tc.last_status = outcome.status
                        if outcome.status == "passed":
                            tc.in_library = True  # 执行通过：纳入用例库(单向，永久保留)

                        # 执行证据回填 covered_items.coverage_status（方案 MVP：checked_points → 覆盖矩阵）
                        if outcome.checked_points and tc.covered_items:
                            from app.services.runners.coverage_evidence import coverage_status_from_checked_points
                            from sqlalchemy.orm.attributes import flag_modified
                            status_map = coverage_status_from_checked_points(outcome.checked_points)
                            changed = False
                            for ci in tc.covered_items:
                                iid = ci.get("item_id")
                                if iid and iid in status_map:
                                    ci["coverage_status"] = status_map[iid]
                                    changed = True
                                elif ci.get("coverage_status") in (None, "not_covered"):
                                    # 无 item_id 精确映射时按用例整体结论兜底
                                    ci["coverage_status"] = "covered" if outcome.status == "passed" else "failed"
                                    changed = True
                            if changed:
                                flag_modified(tc, "covered_items")

                        # 阶段五：执行采集 → 图谱运行时边回补（Case→Page、Page/Case→API），失败不阻塞
                        if outcome.actual_visited_pages or outcome.actual_api_calls:
                            try:
                                await db.flush()
                                from app.services.graph.runtime_collector import collect_from_result
                                await collect_from_result(db, tr.id)
                            except Exception:  # noqa: BLE001
                                pass

                        # 执行时抓到的页面结构 → 自动补充/刷新页面结构缓存(新页面自动写入)
                        if outcome.page_captures:
                            from app.services.page_cache_service import upsert_from_execution
                            for cap in outcome.page_captures:
                                try:
                                    await upsert_from_execution(
                                        db, project_id=ex.project_id, url=cap.get("url", ""),
                                        page_name=cap.get("page_name", ""), regions=cap.get("regions", []),
                                        base_url=ctx.base_url, description=cap.get("description", ""),
                                    )
                                except Exception:
                                    pass

                        # 失败 → 生成待复核缺陷；通过 → 自动复核既有缺陷为已解决
                        # 口径：只要用例「失败(failed)」就建复核——含「无法验证(blocked/env_error)」，
                        # 让所有未通过的执行都能在缺陷复核里看到。仅「无法运行(status=error，如打不开
                        # 浏览器/无地址/超时等纯环境问题)」不建单。
                        await db.flush()
                        # setup_error（数据未准备好）与 env_error 一样，是"没跑成"，不建产品缺陷（方案 §16.3）。
                        _build_defect = (
                            outcome.status == "failed"
                            or (outcome.status == "error" and outcome.failure_type not in ("env_error", "setup_error"))
                        )
                        if _build_defect:
                            await create_defect_for_failure(db, tr, tc)
                        elif outcome.status == "passed":
                            await resolve_open_defects_for_case(db, case_id, note="再次执行通过，缺陷已解决")
                        # 清掉该用例可能残留的单条取消标志（自然跑完时），避免影响它下次执行
                        execution_control.clear_case(case_id)
                        # 每条用例结束就提交：让该条结果【立刻】对前端可见。否则整批跑完才在
                        # _finalize_execution 统一提交，期间已完成用例的结果在另一连接查不到 →
                        # 前端一直显示"测试中"、"最近结果"还是旧的(批量执行尤其明显)。
                        try:
                            await db.commit()
                        except Exception as _ce:  # noqa: BLE001 单条提交失败不拖垮整批
                            logger.warning("execution %s 用例 %s 结果提交失败：%s", execution_id, case_id, _ce)
                            await db.rollback()

                        # 【造数能力自动沉淀】本次执行真实打过的写操作报文 → 可重放的造数能力。
                        # 用例通不通过都沉淀：中间那次"新建"往往是成功的，只是后续断言没过，
                        # 那份报文照样有价值。失败绝不影响执行结果。
                        try:
                            from app.services.data_prep.sediment import sediment_and_verify
                            _sed = await sediment_and_verify(db, tr, tc, (ctx.extra or {}).get("env") or "sit")
                            if _sed.get("sedimented"):
                                execution_control.log(
                                    execution_id,
                                    f"[{_ci}/{len(case_ids)}] 已沉淀造数能力 {len(_sed['sedimented'])} 个"
                                    + (f"，自动认证 {sum(1 for v in _sed['auto_verified'] if v['ok'])} 个"
                                       if _sed.get("auto_verified") else ""),
                                    case_id=case_id)
                        except Exception as _se:  # noqa: BLE001
                            logger.warning("execution %s 用例 %s 造数能力沉淀失败：%s", execution_id, case_id, _se)
                            try:
                                await db.rollback()
                            except Exception:  # noqa: BLE001
                                pass
                    except Exception as _case_exc:  # noqa: BLE001
                        logger.exception("execution %s 用例 %s 执行异常，记为 env_error 并继续批次",
                                         execution_id, case_id)
                        errored += 1
                        try:
                            await db.rollback()
                        except Exception:  # noqa: BLE001
                            pass
                        try:
                            db.add(TestResult(
                                execution_id=execution_id, test_case_id=case_id, status="error",
                                duration_ms=0, failure_type="env_error",
                                error_message=f"平台执行异常：{_case_exc}", defect_status="pending_review",
                            ))
                            await db.commit()
                        except Exception as _we:  # noqa: BLE001 记不进去也不能拖垮批次
                            logger.warning("execution %s 用例 %s 异常结果落库失败：%s", execution_id, case_id, _we)
                            await db.rollback()
                        execution_control.log(execution_id, f"[{_ci}/{len(case_ids)}] 用例执行异常，已跳过：{_case_exc}",
                                              "warn", case_id=case_id)
                        execution_control.clear_case(case_id)
                        continue
            finally:
                await release_cached_sonic_sessions(ctx.extra)

            await _finalize_execution(
                db, ex, passed=passed, failed=failed + errored, skipped=skipped,
                total_ms=total_ms, req_ids=req_ids, execution_id=execution_id,
                canceled=canceled,
            )
            execution_control.log(
                execution_id,
                "⛔ 已取消执行" if canceled else f"执行完成：通过 {passed} / 未通过 {failed + errored} / 跳过 {skipped}",
                "warn" if canceled else "info",
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
                    if ex2 and ex2.status not in ("done", "failed", "canceled"):
                        ex2.status = "failed"
                        ex2.finished_at = datetime.now()
                        ex2.error_message = f"执行异常：{e}"[:2000]
                        await db2.commit()
            except Exception:
                logger.exception("execution %s 失败状态落库也失败", execution_id)
        finally:
            execution_control.clear(execution_id)  # 清取消标志（日志随环形缓冲自然淘汰）
            # 临时账号登录态用完即删(含 cookie，绝不残留)
            for _f in _temp_files:
                try:
                    _Path(_f).unlink(missing_ok=True)
                except Exception:
                    pass


async def _finalize_execution(db, ex, *, passed, failed, skipped, total_ms, req_ids, execution_id, canceled=False):
    """执行收尾：统计、质量门禁、缺陷分析、需求完成判定、飞书通知。

    与 MockExecutionRunner 收尾逻辑一致；抽成公共函数便于 mock/real 复用。
    canceled=True 时置终态 canceled（前端据此停轮询、不算失败）。
    """
    total = passed + failed + skipped
    ex.passed = passed
    ex.failed = failed
    ex.skipped = skipped
    ex.total = total
    ex.pass_rate = round((passed / total * 100) if total > 0 else 0.0, 2)
    ex.duration_ms = total_ms
    ex.status = "canceled" if canceled else "done"
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

    # 取消：不推进需求完成态（用例没跑全，判定完成会误导）
    if not canceled:
        from app.services.requirement_status import apply_requirement_completion
        for req_id in req_ids:
            await apply_requirement_completion(db, req_id)

    await db.commit()

    if not canceled and proj and proj.feishu_webhook:
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
