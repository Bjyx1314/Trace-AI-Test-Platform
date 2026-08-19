"""Core data-flow router: requirement → analyze → generate cases (执行测试时惰性生成脚本，见mock_runner.py)."""
import asyncio
import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from app.config import settings
from app.database import get_db
from app.models import Requirement, TestCase, Project, EnumDefinition
from app.schemas import PipelineRequest, PipelineStatus, PlatformsConfirmRequest
from app.routers.testcases import _generate_case_id
from app.agents import RequirementAnalystAgent, TestCaseGeneratorAgent
from app.dependencies import get_current_user
from app.services.ai_key import resolve_user_ai_key, NoAiKeyError
from app.services import experience_recall

router = APIRouter(prefix="/api/pipeline", tags=["pipeline"])
logger = logging.getLogger(__name__)

analyst = RequirementAnalystAgent()
case_gen = TestCaseGeneratorAgent()

_TC_FIELDS = {
    "modules", "platforms", "title", "priority", "preconditions",
    "steps", "expected_result", "source_issue_point", "case_type", "tags",
    # AI 质量闭环：覆盖项与来源（matched_rules/affected_*/regression_flag 由平台侧写，不从 LLM 直接映射）
    "covered_items", "sources", "risk_tags", "reason",
}


def _fanout_by_platform(cases: list[dict]) -> list[dict]:
    """一条用例只归属一个端：把 platforms 含多个端的用例按端拆成 N 条单端用例。

    多端来源有二：① AI 判端时把跨端功能标了多个端；② 展示字段模板判不出端时回填了「全部确认端」。
    执行侧本就按单端跑，多端用例会歧义。这里统一在落库前 fan-out：内容不变，端各一，保持覆盖不丢。
    0/1 个端的用例原样保留。保序：同一条的各端副本相邻。
    """
    out: list[dict] = []
    for c in cases:
        plats = c.get("platforms") or []
        if len(plats) <= 1:
            out.append(c)
            continue
        for p in plats:
            out.append({**c, "platforms": [p]})
    return out


def _title_similarity(t1: str, t2: str) -> float:
    """两个用例标题的相似度(字符 bigram Jaccard)。

    中文标题没有空格，旧版按 .split() 分词对中文恒为 0(整条标题算一个词)，导致中文用例
    永远去重不到。改用字符 2-gram 的 Jaccard，对中文有效。
    """
    def _bigrams(s: str) -> set[str]:
        s = "".join((s or "").lower().split())
        if len(s) < 2:
            return {s} if s else set()
        return {s[i:i + 2] for i in range(len(s) - 1)}

    b1, b2 = _bigrams(t1), _bigrams(t2)
    if not b1 or not b2:
        return 0.0
    return len(b1 & b2) / len(b1 | b2)


async def _fetch_enum_options(db: AsyncSession, category: str, parent_key: str | None = None) -> list[dict]:
    q = select(EnumDefinition).where(
        EnumDefinition.category == category, EnumDefinition.is_active.is_(True),
    ).order_by(EnumDefinition.sort_order)
    if parent_key is not None:
        q = q.where(EnumDefinition.parent_key == parent_key)
    rows = (await db.execute(q)).scalars().all()
    if parent_key is not None and not rows:
        rows = (await db.execute(
            select(EnumDefinition).where(EnumDefinition.category == category, EnumDefinition.is_active.is_(True))
            .order_by(EnumDefinition.sort_order)
        )).scalars().all()
    return [{"key": e.key, "label": e.label} for e in rows]


def _union_platforms(analysis_result: dict | None) -> list[str]:
    """各问题点端的并集，作为「涉及端」初值。"""
    ips = (analysis_result or {}).get("issue_points") or []
    seen: list[str] = []
    for ip in ips:
        for p in (ip.get("platforms") or []):
            if p not in seen:
                seen.append(p)
    return seen


def _validate_ready_for_case_generation(req: Requirement) -> str | None:
    """Returns error message if not ready, None if ready to generate cases."""
    if not req.analysis_result:
        return "请先完成需求分析再生成用例"
    if not req.analysis_result.get("platforms_confirmed"):
        return "请先确认「涉及端」"
    for ip in req.analysis_result.get("issue_points", []):
        for cp in ip.get("confirmation_points", []):
            if cp.get("status") != "confirmed":
                return "请确认所有待确认点后再生成用例"
    return None


async def _remainder_content(db: AsyncSession, req: Requirement) -> str:
    """父需求(全文)的「剩余文本」= 整条需求内容减去已被各子范围圈过的文字。
    无子范围时即整条内容。用于父行分析/生成只补未被圈过的部分，不与子范围重复。"""
    from app.models import RequirementSlice
    subs = (await db.execute(
        select(RequirementSlice).where(
            RequirementSlice.requirement_id == req.id, RequirementSlice.is_default.is_(False)
        )
    )).scalars().all()
    rem = req.content or ""
    for sl in subs:
        for seg in (sl.scope_text or "").split("———"):
            seg = seg.strip()
            if seg:
                rem = rem.replace(seg, "")
    return rem.strip()


def _append_supplement(content: str | None, supplement: str | None) -> str | None:
    """把人工补充需求点(选填)拼进交给 LLM 的正文，带醒目标注，供分析识别与用例生成一并覆盖。"""
    if supplement and supplement.strip():
        base = (content or "").rstrip()
        return f"{base}\n\n【人工补充需求点（请务必识别并覆盖为测试用例）】\n{supplement.strip()}"
    return content


async def _run_analysis_inline(db: AsyncSession, req: Requirement, scope_text: str | None = None,
                               scope_image_tokens: list[str] | None = None, supplement: str | None = None) -> None:
    """需求分析核心逻辑：写入 req.analysis_result/status，不提交，由调用方统一commit。
    scope_text 非空时只分析选中的部分内容；scope_image_tokens 为选区内的图片，精确随分析发送。"""
    req.status = "analyzing"
    modules = await _fetch_enum_options(db, "module", parent_key=req.product_line)
    platforms = await _fetch_enum_options(db, "platform")
    source_req_id = f"REQ-{req.id}"
    scoped = bool(scope_text and scope_text.strip())
    # 父需求(全文)只分析「未被子范围圈过的剩余文本」，避免与子范围重复
    content = scope_text.strip() if scoped else await _remainder_content(db, req)
    if not scoped and not content and not (supplement and supplement.strip()):
        # 整条需求已全部分配到各负责范围，父需求无需补充分析(除非有人工补充需求点)
        req.status = "pending_case_generation" if req.analysis_result else "pending_analysis"
        return
    content = _append_supplement(content, supplement)
    # 选中模式：优先发选区内的图；若选区没圈到图则回退发整条需求的图(避免漏看)。全文模式发整条需求图
    from app.services.requirement_media import collect_requirement_images, collect_images_by_tokens
    images = collect_images_by_tokens(scope_image_tokens) if scoped else []
    if not images:
        images = collect_requirement_images(req)
    logger.warning("需求分析[%s] scoped=%s 选区图tokens=%d 实际发送图片=%d 张",
                   req.id, scoped, len(scope_image_tokens or []), len(images))
    try:
        result = await asyncio.wait_for(
            analyst.analyze(
                req.title, content, source_req_id, req.product_line, modules, platforms, images=images,
            ),
            timeout=settings.ai_call_timeout_sec,
        )
    except asyncio.TimeoutError:
        raise RuntimeError(f"AI 分析超时（超过 {settings.ai_call_timeout_sec // 60} 分钟无响应，已中断），请重试")
    # 记录本次分析的文档范围(选区或全文)，供覆盖分析按同一范围比对
    if isinstance(result, dict):
        result["scope_text"] = content
        result["scoped"] = scoped
        # 涉及端：取各问题点端的并集，待用户在「涉及端」确认项里确认/修改
        result["platforms"] = _union_platforms(result)
        result["platforms_confirmed"] = False
    req.analysis_result = result
    req.status = "pending_case_generation"


async def _run_analysis(req_id: str, scope_text: str | None = None, scope_image_tokens: list[str] | None = None,
                        ai_key: str | None = None, supplement: str | None = None):
    from app.database import AsyncSessionLocal
    from app.agents.llm import set_current_ai_key
    set_current_ai_key(ai_key)  # 走发起人自己的 key
    async with AsyncSessionLocal() as db:
        req = await db.get(Requirement, req_id)
        if not req:
            return
        try:
            # 先提交"分析中"状态，让前端轮询立刻看到进行中(GPT 调用可能 ~30s)；清掉旧错误
            req.status = "analyzing"
            if req.analysis_result and req.analysis_result.get("error_message"):
                base = dict(req.analysis_result); base.pop("error_message", None); req.analysis_result = base
            await db.commit()
            await _run_analysis_inline(db, req, scope_text, scope_image_tokens, supplement)
            await db.commit()
        except Exception as e:
            # 失败不新增状态：回退 pending_analysis；原因写 analysis_result.error_message 供提示
            await db.rollback()
            req = await db.get(Requirement, req_id)
            if req:
                req.status = "pending_analysis"
                base = dict(req.analysis_result or {})
                base["error_message"] = f"需求分析失败：{e}"
                req.analysis_result = base
                await db.commit()


async def _run_case_generation(req_id: str, regenerate: bool = False, scope_text: str | None = None,
                               scope_image_tokens: list[str] | None = None, ai_key: str | None = None,
                               supplement: str | None = None):
    """后台任务入口：失败不新增状态，回退 pending_case_generation，原因写 generation_error 供提示。"""
    from app.agents.llm import set_current_ai_key
    set_current_ai_key(ai_key)  # 走发起人自己的 key
    try:
        await _run_case_generation_impl(req_id, regenerate, scope_text, scope_image_tokens, supplement)
    except Exception as e:
        from app.database import AsyncSessionLocal
        async with AsyncSessionLocal() as db:
            req = await db.get(Requirement, req_id)
            if req:
                req.status = "pending_case_generation"
                base = dict(req.analysis_result or {})
                base["generation_error"] = f"用例生成失败：{e}"
                req.analysis_result = base
                await db.commit()


async def _recall_hints_for_req(db, req) -> tuple[str, list[dict]]:
    """按需求召回历史经验，返回(注入提示词段, 命中经验列表)。失败/空则('', [])。"""
    try:
        issue_points = (req.analysis_result or {}).get("issue_points", [])
        risk_tags = sorted({t for ip in issue_points for t in (ip.get("risk_tags") or [])})
        query = f"{req.title}\n{(req.content or '')[:2000]}"
        hits = await experience_recall.recall_for_generation(
            db, project_id=req.project_id, query_text=query, risk_tags=risk_tags, limit=8,
        )
        return experience_recall.build_experience_prompt_block(hits), hits
    except Exception:  # noqa: BLE001
        return "", []


async def _writeback_adopted(db, hits: list[dict], cases_data: list[dict]) -> None:
    """生成结果命中经验 → 交给 experience_lifecycle.record_signal 记 adopt 信号(转正口径统一在那里)。失败不阻断。"""
    if not hits:
        return
    try:
        names = [ci.get("name") for c in (cases_data or []) for ci in (c.get("covered_items") or []) if ci.get("name")]
        adopted = experience_recall.match_adopted_experiences(hits, names)
        if not adopted:
            return
        from app.services import experience_lifecycle
        for exp_id in adopted:
            await experience_lifecycle.record_signal(db, exp_id, "adopt")
    except Exception:  # noqa: BLE001
        pass


async def _run_case_generation_impl(req_id: str, regenerate: bool = False, scope_text: str | None = None,
                                    scope_image_tokens: list[str] | None = None, supplement: str | None = None):
    from app.database import AsyncSessionLocal
    async with AsyncSessionLocal() as db:
        req = await db.get(Requirement, req_id)
        if not req:
            return
        project = await db.get(Project, req.project_id)

        # 默认路径生成的用例也归到该需求的默认「全文」切片，便于阶段3按切片统计(必要时新建)
        from app.routers.requirements import ensure_default_slice
        _dsl = await ensure_default_slice(db, req_id)
        default_slice_id = _dsl.id if _dsl else None

        req.status = "generating_cases"
        if req.analysis_result and req.analysis_result.get("generation_error"):
            base = dict(req.analysis_result); base.pop("generation_error", None); req.analysis_result = base
        await db.commit()

        if regenerate:
            existing = (await db.execute(
                select(TestCase)
                .options(selectinload(TestCase.results))
                .where(TestCase.requirement_id == req_id)
            )).scalars().all()
            for tc in existing:
                if not tc.results:
                    await db.delete(tc)
            await db.flush()

        modules = await _fetch_enum_options(db, "module", parent_key=req.product_line)
        platforms = await _fetch_enum_options(db, "platform")
        # 端硬约束：生成只能在「涉及端」确认项里已确认的端中选
        confirmed_p = (req.analysis_result or {}).get("platforms")
        if confirmed_p:
            platforms = [p for p in platforms if p["key"] in confirmed_p]
        source_req_id = f"REQ-{req.id}"
        # 选中部分时：用选中文本作为"需求内容"，问题点/确认结果仍取需求分析确认结果(结合二者生成)
        scoped_gen = bool(scope_text and scope_text.strip())
        # 父需求(全文)用「剩余文本」作上下文；问题点取需求分析结果(已是剩余文本的分析)
        content = scope_text.strip() if scoped_gen else (await _remainder_content(db, req) or req.content)
        content = _append_supplement(content, supplement)
        issue_points = (req.analysis_result or {}).get("issue_points", [])
        confirmation = req.analysis_confirmation

        # 选中模式：优先发选区内的图，没圈到则回退整条需求图；全文模式发整条需求图
        from app.services.requirement_media import collect_requirement_images, collect_images_by_tokens
        gen_images = collect_images_by_tokens(scope_image_tokens) if scoped_gen else []
        if not gen_images:
            gen_images = collect_requirement_images(req)
        logger.warning("用例生成[%s] scoped=%s 选区图tokens=%d 实际发送图片=%d 张",
                       req.id, scoped_gen, len(scope_image_tokens or []), len(gen_images))
        exp_hints, exp_hits = await _recall_hints_for_req(db, req)
        try:
            cases_data = await asyncio.wait_for(
                case_gen.generate(
                    req.title, content, issue_points, confirmation, modules, platforms,
                    images=gen_images, experience_hints=exp_hints,
                ),
                timeout=settings.ai_call_timeout_sec,
            )
        except asyncio.TimeoutError:
            raise RuntimeError(f"AI 用例生成超时（超过 {settings.ai_call_timeout_sec // 60} 分钟无响应，已中断），请重试")
        # 有图但 AI 视觉识别失败：在 analysis_result 上留醒目标注(同 generation_error 落点)，前端展示
        if case_gen.last_vision_failed_images:
            n = case_gen.last_vision_failed_images
            base = dict(req.analysis_result or {})
            base["generation_vision_warning"] = {
                "image_count": n,
                "message": f"本次生成有 {n} 张需求图片未能被 AI 识别（视觉服务暂不可用），"
                           f"图片中的内容可能未覆盖进用例，请人工核对或稍后重试。",
            }
            req.analysis_result = base

        # 展示字段/展示逻辑确定性补齐（模板，不调 AI）：字段清单直接补；拼接、条件、映射、兜底、
        # 显隐、聚合等展示逻辑做覆盖审计，AI 已覆盖则不重复，未覆盖则补 gap case。
        try:
            from app.agents import field_case_generator as _fcg
            from app.agents.testcase_generator import _apply_covered_item_normalization as _norm_ci
            _blocks = await _fcg.extract_display_blocks(content)
            if _blocks:
                _pkeys = [p["key"] for p in platforms]
                _tpl = _fcg.emit_field_cases(_blocks, platform_keys=_pkeys, default_platforms=(confirmed_p or _pkeys))
                if _tpl:
                    cases_data = _fcg.append_display_rule_gap_cases(list(cases_data), _norm_ci(_tpl))
                    logger.info("展示字段模板补齐 %d 条(需求 %s)", len(_tpl), req.id)
        except Exception as e:  # noqa: BLE001 补齐失败不阻断主生成
            logger.warning("展示字段模板补齐失败(%s)：%s", req.id, e)

        cases_data = _fanout_by_platform(list(cases_data))
        created_cases: list[TestCase] = []
        for c in cases_data:
            c = {k: v for k, v in c.items() if k in _TC_FIELDS}
            # NOT NULL 列若为 None 则剔除，让 DB 服务端默认生效，防单条缺字段导致整批插入回滚(生成 0 条)
            c = {k: v for k, v in c.items() if not (k in ("sources", "covered_items", "risk_tags") and v is None)}
            tc = TestCase(
                **c,
                project_id=req.project_id,
                requirement_id=req.id,
                slice_id=default_slice_id,
                product_line=req.product_line,
                source_req_id=source_req_id,
                case_id=await _generate_case_id(db, project),
                last_status="not_run",
                script_status="pending",
            )
            db.add(tc)
            created_cases.append(tc)

        await db.commit()  # 先落用例(状态仍 generating_cases)；二级功能算完后再翻 pending_test，避免前端先看到未分组的中间态
        await _writeback_adopted(db, exp_hits, cases_data)

        # 去重：只对「本次新建」用例比对，比对集合 = 同项目其它用例(含同需求已有，排除本次新建自身)。
        # 命中则标 pending_review，交用户复用/更新。两路信号：
        #   1) 功能点精确去重(主)：同需求、同 source_issue_point 的跨次生成用例＝重复(最可靠)；
        #   2) 标题相似度兜底：bigram Jaccard ≥ 0.6(中文域名前缀会抬高分，阈值取高以降误判)。
        # 必须包含同需求——否则同一需求多次生成会写出近似重复却永不去重。
        new_ids = {tc.id for tc in created_cases}
        if new_ids:
            existing_cases = (await db.execute(
                select(TestCase).where(
                    TestCase.project_id == req.project_id,
                    TestCase.id.notin_(new_ids),
                    TestCase.review_status.is_(None),
                )
            )).scalars().all()

            if existing_cases:
                # 同需求按功能点建索引(精确去重主路径)
                issue_to_case: dict[str, TestCase] = {}
                for ec in existing_cases:
                    if ec.requirement_id == req.id and ec.source_issue_point:
                        issue_to_case.setdefault(ec.source_issue_point, ec)

                for new_tc in created_cases:
                    match: TestCase | None = None
                    if new_tc.source_issue_point and new_tc.source_issue_point in issue_to_case:
                        match = issue_to_case[new_tc.source_issue_point]
                    if match is None:
                        best_score = 0.0
                        best_match: TestCase | None = None
                        for ec in existing_cases:
                            score = _title_similarity(new_tc.title, ec.title)
                            if score > best_score:
                                best_score = score
                                best_match = ec
                        if best_score >= 0.6:
                            match = best_match
                    if match is not None:
                        new_tc.review_status = "pending_review"
                        new_tc.similar_case_id = match.id
                await db.commit()

        # 生成完自动重算二级功能：从需求【原文】提取页面级功能并回填 secondary_feature(脑图分组)。
        # 与 source_issue_point/用例内容无关，失败不阻断生成。
        try:
            from app.services.secondary_feature import recompute_for_requirement
            await recompute_for_requirement(db, req)
        except Exception as e:  # noqa: BLE001
            logger.warning("生成后二级功能重算失败(req=%s)：%s", req.id, e)
        # 二级功能就绪后再把用例暴露给前端(pending_test)，分组从第一屏就正确
        req = await db.get(Requirement, req_id)
        if req:
            req.status = "pending_test"
            await db.commit()


# ── 切片级分析/生成（非默认切片走这里；默认切片仍走上面的需求级逻辑，零回归）──────────
async def _resolve_target_slice(db: AsyncSession, requirement_id: str, slice_id: str | None):
    """返回目标切片。给了 slice_id 用之；否则取(必要时新建)该需求的默认全文切片。"""
    from app.models import RequirementSlice
    if slice_id:
        return await db.get(RequirementSlice, slice_id)
    from app.routers.requirements import ensure_default_slice
    return await ensure_default_slice(db, requirement_id)


def _validate_ready_slice(sl) -> str | None:
    if not sl.analysis_result:
        return "请先完成需求分析再生成用例"
    if not sl.analysis_result.get("platforms_confirmed"):
        return "请先确认「涉及端」"
    for ip in sl.analysis_result.get("issue_points", []):
        for cp in ip.get("confirmation_points", []):
            if cp.get("status") != "confirmed":
                return "请确认所有待确认点后再生成用例"
    return None


def _merge_issue_points(existing: dict | None, new: dict | None) -> dict:
    """增量分析：把新分析的问题点追加进现有分析结果，保证 issue_id/point_id 唯一。"""
    import uuid
    merged = dict(existing or {})
    pts = list(merged.get("issue_points") or [])
    used_iids = {ip.get("issue_id") for ip in pts}
    used_pids = {cp.get("point_id") for ip in pts for cp in (ip.get("confirmation_points") or [])}
    for ip in ((new or {}).get("issue_points") or []):
        ip = dict(ip)
        if ip.get("issue_id") in used_iids:
            ip["issue_id"] = f"{ip.get('issue_id')}-{uuid.uuid4().hex[:6]}"
        used_iids.add(ip.get("issue_id"))
        cps = []
        for cp in (ip.get("confirmation_points") or []):
            cp = dict(cp)
            if cp.get("point_id") in used_pids:
                cp["point_id"] = f"{cp.get('point_id')}-{uuid.uuid4().hex[:6]}"
            used_pids.add(cp.get("point_id"))
            cps.append(cp)
        ip["confirmation_points"] = cps
        pts.append(ip)
    merged["issue_points"] = pts
    return merged


async def _run_analysis_for_slice(slice_id: str, ai_key: str | None = None, mode: str = "full",
                                  supplement: str | None = None):
    """切片级需求分析。mode=full 分析整段累加范围并替换；incremental 仅分析 pending_scope 增量并追加。"""
    from app.database import AsyncSessionLocal
    from app.models import RequirementSlice
    from app.agents.llm import set_current_ai_key
    from app.services.requirement_media import collect_requirement_images, collect_images_by_tokens
    set_current_ai_key(ai_key)
    async with AsyncSessionLocal() as db:
        sl = await db.get(RequirementSlice, slice_id)
        if not sl:
            return
        req = await db.get(Requirement, sl.requirement_id)
        if not req:
            return
        incremental = mode == "incremental" and bool(sl.pending_scope and sl.pending_scope.strip()) and bool(sl.analysis_result)
        try:
            sl.status = "analyzing"
            if sl.analysis_result and sl.analysis_result.get("error_message"):
                base = dict(sl.analysis_result); base.pop("error_message", None); sl.analysis_result = base
            await db.commit()
            modules = await _fetch_enum_options(db, "module", parent_key=req.product_line)
            platforms = await _fetch_enum_options(db, "platform")
            # 增量：只分析新追加的 pending_scope；全量：分析整段累加范围
            base_text = sl.pending_scope if incremental else sl.scope_text
            scoped = bool(base_text and base_text.strip())
            content = base_text.strip() if scoped else req.content
            content = _append_supplement(content, supplement)
            images = collect_images_by_tokens(sl.scope_image_tokens) if scoped else []
            if not images:
                images = collect_requirement_images(req)
            try:
                result = await asyncio.wait_for(
                    analyst.analyze(req.title, content, f"REQ-{req.id}", req.product_line, modules, platforms, images=images),
                    timeout=settings.ai_call_timeout_sec,
                )
            except asyncio.TimeoutError:
                raise RuntimeError(f"AI 分析超时（超过 {settings.ai_call_timeout_sec // 60} 分钟无响应，已中断），请重试")
            if isinstance(result, dict):
                result["scope_text"] = sl.scope_text or content
                result["scoped"] = bool(sl.scope_text)
            merged = _merge_issue_points(sl.analysis_result, result) if incremental else result
            # 涉及端：并集 + 需重新确认(增量也重置，新内容可能引入新端)
            if isinstance(merged, dict):
                merged["platforms"] = _union_platforms(merged)
                merged["platforms_confirmed"] = False
            sl.analysis_result = merged
            sl.pending_scope = None  # 增量已并入；全量也清空
            sl.status = "pending_case_generation"
            await db.commit()
        except Exception as e:
            await db.rollback()
            sl = await db.get(RequirementSlice, slice_id)
            if sl:
                sl.status = "pending_analysis"
                base = dict(sl.analysis_result or {})
                base["error_message"] = f"需求分析失败：{e}"
                sl.analysis_result = base
                await db.commit()


async def _run_case_generation_for_slice(slice_id: str, regenerate: bool = False, ai_key: str | None = None,
                                         mode: str = "full", supplement: str | None = None):
    """切片级用例生成。mode=full 按全部问题点生成；incremental 仅对【尚无用例】的新问题点生成并追加。"""
    from app.database import AsyncSessionLocal
    from app.models import RequirementSlice
    from app.agents.llm import set_current_ai_key
    from app.services.requirement_media import collect_requirement_images, collect_images_by_tokens
    set_current_ai_key(ai_key)
    async with AsyncSessionLocal() as db:
        sl = await db.get(RequirementSlice, slice_id)
        if not sl:
            return
        req = await db.get(Requirement, sl.requirement_id)
        project = await db.get(Project, req.project_id) if req else None
        if not req or not project:
            return
        incremental = mode == "incremental"
        try:
            sl.status = "generating_cases"
            if sl.analysis_result and sl.analysis_result.get("generation_error"):
                base = dict(sl.analysis_result); base.pop("generation_error", None); sl.analysis_result = base
            await db.commit()
            if regenerate and not incremental:
                existing = (await db.execute(
                    select(TestCase).options(selectinload(TestCase.results))
                    .where(TestCase.slice_id == slice_id)
                )).scalars().all()
                for tc in existing:
                    if not tc.results:
                        await db.delete(tc)
                await db.flush()

            modules = await _fetch_enum_options(db, "module", parent_key=req.product_line)
            platforms = await _fetch_enum_options(db, "platform")
            confirmed_p = (sl.analysis_result or {}).get("platforms")
            if confirmed_p:
                platforms = [p for p in platforms if p["key"] in confirmed_p]
            scoped = bool(sl.scope_text and sl.scope_text.strip())
            content = sl.scope_text.strip() if scoped else req.content
            content = _append_supplement(content, supplement)
            issue_points = (sl.analysis_result or {}).get("issue_points", [])
            if incremental:
                # 仅对尚无用例的问题点生成(按 source_issue_point 判断已覆盖)
                covered = set((await db.execute(
                    select(TestCase.source_issue_point).where(
                        TestCase.slice_id == slice_id, TestCase.source_issue_point.is_not(None)
                    )
                )).scalars().all())
                issue_points = [ip for ip in issue_points if ip.get("issue_id") not in covered]
                if not issue_points:
                    sl.status = "pending_test"
                    await db.commit()
                    return
            confirmation = sl.analysis_confirmation
            gen_images = collect_images_by_tokens(sl.scope_image_tokens) if scoped else []
            if not gen_images:
                gen_images = collect_requirement_images(req)
            exp_hints, exp_hits = await _recall_hints_for_req(db, req)
            try:
                cases_data = await asyncio.wait_for(
                    case_gen.generate(req.title, content, issue_points, confirmation, modules, platforms,
                                      images=gen_images, experience_hints=exp_hints),
                    timeout=settings.ai_call_timeout_sec,
                )
            except asyncio.TimeoutError:
                raise RuntimeError(f"AI 用例生成超时（超过 {settings.ai_call_timeout_sec // 60} 分钟无响应，已中断），请重试")
            if case_gen.last_vision_failed_images:
                n = case_gen.last_vision_failed_images
                base = dict(sl.analysis_result or {})
                base["generation_vision_warning"] = {
                    "image_count": n,
                    "message": f"本次生成有 {n} 张需求图片未能被 AI 识别（视觉服务暂不可用），图片中的内容可能未覆盖进用例，请人工核对或稍后重试。",
                }
                sl.analysis_result = base

            # 展示字段/展示逻辑确定性补齐：与主生成路径一致。增量模式只补新问题点，跳过以免重复补字段。
            if not incremental:
                try:
                    from app.agents import field_case_generator as _fcg
                    from app.agents.testcase_generator import _apply_covered_item_normalization as _norm_ci
                    _blocks = await _fcg.extract_display_blocks(content)
                    if _blocks:
                        _pkeys = [p["key"] for p in platforms]
                        _tpl = _fcg.emit_field_cases(_blocks, platform_keys=_pkeys, default_platforms=(confirmed_p or _pkeys))
                        if _tpl:
                            cases_data = _fcg.append_display_rule_gap_cases(list(cases_data), _norm_ci(_tpl))
                            logger.info("展示字段模板补齐 %d 条(切片 %s)", len(_tpl), slice_id)
                except Exception as e:  # noqa: BLE001 补齐失败不阻断主生成
                    logger.warning("展示字段模板补齐失败(切片 %s)：%s", slice_id, e)

            cases_data = _fanout_by_platform(list(cases_data))
            created_cases: list[TestCase] = []
            for c in cases_data:
                c = {k: v for k, v in c.items() if k in _TC_FIELDS}
                # NOT NULL 列若为 None 则剔除，让 DB 服务端默认生效，防单条缺字段导致整批插入回滚(生成 0 条)
                c = {k: v for k, v in c.items() if not (k in ("sources", "covered_items", "risk_tags") and v is None)}
                tc = TestCase(
                    **c, project_id=req.project_id, requirement_id=req.id, slice_id=sl.id,
                    product_line=req.product_line, source_req_id=f"REQ-{req.id}",
                    case_id=await _generate_case_id(db, project), last_status="not_run", script_status="pending",
                )
                db.add(tc)
                created_cases.append(tc)
            await db.commit()  # 先落用例(状态仍 generating_cases)；二级功能算完后再翻 pending_test
            await _writeback_adopted(db, exp_hits, cases_data)

            # 去重：仅在同一切片内比对(不同切片范围不同，不跨切片去重)
            new_ids = {tc.id for tc in created_cases}
            if new_ids:
                existing_cases = (await db.execute(
                    select(TestCase).where(
                        TestCase.slice_id == slice_id,
                        TestCase.id.notin_(new_ids),
                        TestCase.review_status.is_(None),
                    )
                )).scalars().all()
                if existing_cases:
                    issue_to_case: dict[str, TestCase] = {}
                    for ec in existing_cases:
                        if ec.source_issue_point:
                            issue_to_case.setdefault(ec.source_issue_point, ec)
                    for new_tc in created_cases:
                        match: TestCase | None = None
                        if new_tc.source_issue_point and new_tc.source_issue_point in issue_to_case:
                            match = issue_to_case[new_tc.source_issue_point]
                        if match is None:
                            best_score, best_match = 0.0, None
                            for ec in existing_cases:
                                score = _title_similarity(new_tc.title, ec.title)
                                if score > best_score:
                                    best_score, best_match = score, ec
                            if best_score >= 0.6:
                                match = best_match
                        if match is not None:
                            new_tc.review_status = "pending_review"
                            new_tc.similar_case_id = match.id
                    await db.commit()
            # 生成完自动重算二级功能(从需求原文提取, 回填 secondary_feature; 失败不阻断生成)
            try:
                from app.services.secondary_feature import recompute_for_requirement
                await recompute_for_requirement(db, req)
            except Exception as _fe:  # noqa: BLE001
                logger.warning("切片生成后二级功能重算失败(req=%s)：%s", getattr(req, "id", "?"), _fe)
            # 二级功能就绪后再把用例暴露给前端(pending_test)，分组从第一屏就正确
            sl = await db.get(RequirementSlice, slice_id)
            if sl:
                sl.status = "pending_test"
                await db.commit()
        except Exception as e:
            await db.rollback()
            sl = await db.get(RequirementSlice, slice_id)
            if sl:
                sl.status = "pending_case_generation"
                base = dict(sl.analysis_result or {})
                base["generation_error"] = f"用例生成失败：{e}"
                sl.analysis_result = base
                await db.commit()


@router.post("/analyze", response_model=PipelineStatus)
async def analyze_requirement(
    body: PipelineRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    req = await db.get(Requirement, body.requirement_id)
    if not req:
        raise HTTPException(404, "Requirement not found")
    try:
        ai_key = await resolve_user_ai_key(db, current_user)
    except NoAiKeyError as e:
        raise HTTPException(400, str(e))

    # 非默认切片：走切片级分析(范围取自切片)；默认/未指定：走需求级既有逻辑(零回归)
    target = await _resolve_target_slice(db, body.requirement_id, body.slice_id)
    if target is not None and not target.is_default:
        background_tasks.add_task(_run_analysis_for_slice, target.id, ai_key, body.mode or "full", body.supplement)
        kind = "增量" if (body.mode == "incremental") else "全量"
        return PipelineStatus(requirement_id=body.requirement_id, status="started", message=f"已启动范围「{target.scope_label}」的{kind}需求分析")

    background_tasks.add_task(_run_analysis, body.requirement_id, body.scope_text, body.scope_image_tokens, ai_key, body.supplement)
    msg = "已针对选中内容启动需求分析" if (body.scope_text and body.scope_text.strip()) else "需求分析已启动"
    return PipelineStatus(requirement_id=body.requirement_id, status="started", message=msg)


@router.post("/generate-cases", response_model=PipelineStatus)
async def generate_cases(
    body: PipelineRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: dict = Depends(get_current_user),
):
    req = await db.get(Requirement, body.requirement_id)
    if not req:
        raise HTTPException(404, "Requirement not found")

    scoped = bool(body.scope_text and body.scope_text.strip())
    try:
        ai_key = await resolve_user_ai_key(db, current_user)
    except NoAiKeyError as e:
        raise HTTPException(400, str(e))

    # 非默认切片：按切片的分析结果生成、用例带 slice_id；默认/未指定：走需求级既有逻辑
    target = await _resolve_target_slice(db, body.requirement_id, body.slice_id)
    if target is not None and not target.is_default:
        err = _validate_ready_slice(target)
        if err:
            raise HTTPException(400, err)
        background_tasks.add_task(_run_case_generation_for_slice, target.id, body.regenerate, ai_key, body.mode or "full", body.supplement)
        kind = "增量" if (body.mode == "incremental") else "全量"
        return PipelineStatus(requirement_id=body.requirement_id, status="started", message=f"已启动范围「{target.scope_label}」的{kind}用例生成")

    # 无论全文还是选中，都要求先完成需求分析且所有待确认点已确认
    err = _validate_ready_for_case_generation(req)
    if err:
        raise HTTPException(400, err)
    background_tasks.add_task(_run_case_generation, body.requirement_id, body.regenerate, body.scope_text, body.scope_image_tokens, ai_key, body.supplement)
    msg = "已针对选中内容生成用例" if scoped else ("用例重新生成已启动" if body.regenerate else "测试用例生成已启动")
    return PipelineStatus(requirement_id=body.requirement_id, status="started", message=msg)


def _failed_and_message(holder) -> tuple[bool, str]:
    """失败判定(不靠独立状态)：状态回退到 pending_analysis/pending_case_generation 且 analysis_result
    带 error_message/generation_error → 视为刚失败，返回 (True, 错误文案)；否则 (False, 普通状态文案)。"""
    ar = holder.analysis_result or {}
    if holder.status == "pending_analysis" and ar.get("error_message"):
        return True, ar["error_message"]
    if holder.status == "pending_case_generation" and ar.get("generation_error"):
        return True, ar["generation_error"]
    return False, f"status: {holder.status}"


@router.post("/confirm-platforms")
async def confirm_platforms(body: PlatformsConfirmRequest, db: AsyncSession = Depends(get_db)):
    """确认/修改某分析范围的「涉及端」。确认后该范围生成用例只能在这些端里选。"""
    target = await _resolve_target_slice(db, body.requirement_id, body.slice_id)
    holder = target if (target is not None and not target.is_default) else await db.get(Requirement, body.requirement_id)
    if not holder or not holder.analysis_result:
        raise HTTPException(400, "请先完成需求分析")
    base = dict(holder.analysis_result)
    base["platforms"] = body.platforms
    base["platforms_confirmed"] = True
    holder.analysis_result = base
    await db.commit()
    return {"status": "ok", "platforms": body.platforms}


@router.get("/status/{req_id}", response_model=PipelineStatus)
async def pipeline_status(req_id: str, slice_id: str | None = None, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import func
    req = await db.get(Requirement, req_id)
    if not req:
        raise HTTPException(404, "Requirement not found")

    # 非默认切片：状态/错误/用例数按切片口径；默认或未指定：需求级(既有行为)
    target = await _resolve_target_slice(db, req_id, slice_id)
    if target is not None and not target.is_default:
        case_count = (await db.execute(
            select(func.count()).where(TestCase.slice_id == target.id)
        )).scalar() or 0
        script_count = (await db.execute(
            select(func.count()).where(TestCase.slice_id == target.id, TestCase.script_status == "ready")
        )).scalar() or 0
        failed, message = _failed_and_message(target)
        return PipelineStatus(
            requirement_id=req_id, status=target.status, message=message, failed=failed,
            cases_generated=case_count, scripts_generated=script_count,
        )

    count_result = await db.execute(
        select(func.count()).where(TestCase.requirement_id == req_id)
    )
    case_count = count_result.scalar() or 0

    script_result = await db.execute(
        select(func.count()).where(
            TestCase.requirement_id == req_id,
            TestCase.script_status == "ready",
        )
    )
    script_count = script_result.scalar() or 0

    # 失败不再是独立状态：状态回退 + analysis_result 带错误 → 用 failed 标志 + message 透出
    failed, message = _failed_and_message(req)

    return PipelineStatus(
        requirement_id=req_id,
        status=req.status,
        message=message,
        failed=failed,
        cases_generated=case_count,
        scripts_generated=script_count,
    )


# ════════════════════════════════════════════════════════════════════════
# 阶段 B：代码影响 → 增量新增用例 + 已有用例标记（方案 5 阶段B / 8.5）
# ════════════════════════════════════════════════════════════════════════

def _struct_key(name: str = "", object_: str | None = None, action: str | None = None) -> str:
    """覆盖项结构键：object+action 归一化（方案 7.4 结构键粗筛降级版）。"""
    o = "".join((object_ or "").lower().split())
    a = "".join((action or "").lower().split())
    return f"{o}|{a}" if (o or a) else "".join((name or "").lower().split())


def _covered_by_existing(item: dict, existing_index: dict[str, TestCase], existing_cases: list[TestCase]) -> TestCase | None:
    """判断某建议覆盖项是否已被已有用例覆盖：结构键精确 + name 相似度兜底。"""
    key = _struct_key(item.get("name", ""), item.get("object"), item.get("action"))
    if key in existing_index:
        return existing_index[key]
    for ec in existing_cases:
        for ci in ec.covered_items or []:
            if _title_similarity(item.get("name", ""), ci.get("name", "")) >= 0.6:
                return ec
    return None


def _suggested_to_covered_item(s: dict) -> dict:
    """suggested_validation_item → covered_item（source=code_impact）。"""
    from app.agents.testcase_generator import normalize_covered_items
    raw = {
        "name": s.get("item"),
        "object": s.get("object") or (s.get("item") or "")[:20],
        "action": s.get("action") or "验证",
        "expected": s.get("expected") or s.get("item"),
        "scenario_type": s.get("scenario_type") or "正常路径",
        "risk_tags": s.get("risk_tags") or [],
        "priority": s.get("priority"),
        "reason": s.get("reason"),
        "sources": ["code_impact"],
    }
    return normalize_covered_items([raw], default_sources=["code_impact"])[0]


async def run_impact_incremental(record_id: str) -> dict:
    """消费一次代码影响分析结果，做阶段 B 增量。返回 {gap_created, regression_marked, pending_questions}。

    - suggested_validation_items → covered_items(code_impact)；
    - 与需求下全部已有用例的 covered_items 比对；
    - 缺口项 → 增量新建用例(source=code_impact)，应用硬规则；
    - 命中已有用例 → 标 regression_flag=need_regression；
    - pending_questions 追加进 requirement.analysis_result，状态 pending_second_round。
    多 MR 天然累积不重复（比对的是需求下全部已有覆盖项）。
    """
    from app.database import AsyncSessionLocal
    from app.models import ChangeImpactRecord
    from app.services import quality_rule_engine
    from app.agents.testcase_generator import normalize_covered_items

    async with AsyncSessionLocal() as db:
        rec = await db.get(ChangeImpactRecord, record_id)
        if not rec or not rec.requirement_id or not rec.impact_json:
            return {"gap_created": 0, "regression_marked": 0, "pending_questions": 0}
        req = await db.get(Requirement, rec.requirement_id)
        if not req:
            return {"gap_created": 0, "regression_marked": 0, "pending_questions": 0}
        project = await db.get(Project, req.project_id)

        impact = rec.impact_json
        suggested = impact.get("suggested_validation_items") or []
        scope = impact.get("impact_scope") or {}
        rules = await quality_rule_engine.load_active_rules(db)

        existing_cases = (await db.execute(
            select(TestCase).where(
                TestCase.requirement_id == req.id, TestCase.deleted_at.is_(None),
            )
        )).scalars().all()
        existing_index: dict[str, TestCase] = {}
        for ec in existing_cases:
            for ci in ec.covered_items or []:
                existing_index.setdefault(_struct_key(ci.get("name", ""), ci.get("object"), ci.get("action")), ec)

        gap_items: list[dict] = []
        regression_hit: set[str] = set()
        for s in suggested:
            hit = _covered_by_existing(s, existing_index, existing_cases)
            if hit is not None:
                regression_hit.add(hit.id)
            else:
                gap_items.append(s)

        # 命中已有用例 → 标记需回归（页面结构受影响的可后续细分 need_adjust，MVP 统一 need_regression）
        for ec in existing_cases:
            if ec.id in regression_hit and ec.regression_flag != "need_adjust":
                ec.regression_flag = "need_regression"

        # 缺口项 → 增量新建用例（一项一条，带 covered_item(code_impact)，应用硬规则）
        default_slice_id = None
        try:
            from app.routers.requirements import ensure_default_slice
            _dsl = await ensure_default_slice(db, req.id)
            default_slice_id = _dsl.id if _dsl else None
        except Exception:  # noqa: BLE001
            pass

        gap_created = 0
        _new_gap_cases: list[TestCase] = []
        affected_pages = list(scope.get("affected_pages") or [])
        affected_apis = list(scope.get("affected_apis") or [])
        # 阶段五：图谱扩散补全影响面（与 skill impact_scope 合并去重），失败保持原值
        try:
            from app.services.graph import expander
            seeds = list(scope.get("affected_services") or []) + affected_apis
            if seeds:
                exp = await expander.expand_pages_apis(db, seeds, max_hops=3 if (scope.get("overall_risk") == "P0") else 2)
                affected_pages = list(dict.fromkeys(affected_pages + exp["affected_pages"]))
                affected_apis = list(dict.fromkeys(affected_apis + exp["affected_apis"]))
                base_impact = dict(rec.impact_json or {})
                base_impact["graph_expansion"] = exp["detail"]
                rec.impact_json = base_impact
        except Exception:  # noqa: BLE001
            pass
        for s in gap_items:
            ci = _suggested_to_covered_item(s)
            case_dict = {
                "title": s.get("item"),
                "priority": s.get("priority") or "P1",
                "covered_items": [ci],
                "risk_tags": list(ci.get("risk_tags") or []),
                "matched_rules": [],
                "reason": f"代码影响分析建议：{s.get('reason') or s.get('item')}",
            }
            quality_rule_engine.apply_rules_to_case(case_dict, rules)
            tc = TestCase(
                project_id=req.project_id,
                requirement_id=req.id,
                slice_id=default_slice_id,
                product_line=req.product_line,
                source_req_id=f"REQ-{req.id}",
                title=case_dict["title"],
                priority=case_dict["priority"],
                case_type="ui",
                preconditions=[],
                steps=[],
                expected_result=s.get("expected") or s.get("item"),
                covered_items=case_dict["covered_items"],
                sources=["code_impact"],
                risk_tags=case_dict["risk_tags"],
                matched_rules=case_dict.get("matched_rules") or [],
                reason=case_dict["reason"],
                affected_page_nodes=affected_pages or None,
                affected_api_nodes=affected_apis or None,
                regression_flag=None,
                review_status="pending_review",  # 增量用例待测试 Review
                case_id=await _generate_case_id(db, project),
                last_status="not_run",
                script_status="pending",
            )
            db.add(tc)
            _new_gap_cases.append(tc)
            gap_created += 1

        # 阶段四：复用判断（可复用/需调整/需新增）存入 impact_json，供前端复用清单
        try:
            from app.services import case_reuse
            reuse = await case_reuse.judge(
                db, project_id=req.project_id, requirement_id=req.id,
                suggested_items=[{"name": s.get("item"), "object": s.get("object"), "action": s.get("action")} for s in suggested],
                changed_url_patterns=set(affected_pages or []),
            )
            base_impact = dict(rec.impact_json or {})
            base_impact["reuse"] = reuse
            rec.impact_json = base_impact
        except Exception:  # noqa: BLE001
            pass

        # pending_questions 追加进需求 analysis_result（pending_second_round，方案 8.6）
        pqs = impact.get("pending_questions") or []
        pq_count = 0
        if pqs:
            base = dict(req.analysis_result or {})
            second = list(base.get("pending_second_round") or [])
            existing_q = {q.get("question") if isinstance(q, dict) else q for q in second}
            for q in pqs:
                if q not in existing_q:
                    second.append({"question": q, "status": "pending", "from_impact": rec.id})
                    pq_count += 1
            base["pending_second_round"] = second
            req.analysis_result = base

        await db.commit()
        # 阶段四：新缺口用例同步向量索引
        try:
            from app.services.covered_item_vec import sync_case_vectors
            for tc in _new_gap_cases:
                await sync_case_vectors(db, tc)
        except Exception:  # noqa: BLE001
            pass
        logger.info("阶段B增量[req=%s impact=%s]：缺口新增 %d，命中标回归 %d，二轮问题 %d",
                    req.id, record_id, gap_created, len(regression_hit), pq_count)
        return {"gap_created": gap_created, "regression_marked": len(regression_hit), "pending_questions": pq_count}
