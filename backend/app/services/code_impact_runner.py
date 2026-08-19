"""代码影响分析执行器（platform mode，方案 8.2/8.4；req 2/3/7 重构）。

**重构要点**：过去放 claude 用 `Bash(git *)/Grep/Glob` 满仓库自己找——违反「静态扫描为主、
LLM 只吃 changed diff」。现在改为：
- 变更清单 / 增删 / 分类 / 风险排序 / 大变更截断由 `code_change_static` 确定性算出；
- 本 runner 只把「结构化摘要 + 受限 bounded_diff + skill 方法论」拼进 prompt，
  `claude -p --output-format json` **不给任何仓库工具**（纯文本进、JSON 出）；
- 输入规模受控 → 相同 commit 可缓存（req 6，由 router 落地）、大变更自动限流（req 7）。

失败/超时不阻塞流程：异常时返回 status=failed、error_message，调用方照常继续。
"""
from __future__ import annotations
import json
import os
import shutil
from pathlib import Path

from app.config import settings
from app.agents.llm import get_provider
from app.services.code_change_static import StaticSummary

# code-impact LLM 输出预算（含推理 token；结构化影响分析可能较大，给足余量）
_CODE_IMPACT_MAX_TOKENS = 16000

_IMPACT_SYSTEM = (
    "你是资深测试架构师，对一次代码变更做「测试影响分析」。"
    "只依据我提供的结构化摘要 + 受限 diff + 存量依赖面作答，绝不臆造未提供的代码；证据不足处如实标注。"
    "按给定的函数参数结构输出。"
)

# 方案 8.4 输出契约的精简 JSON Schema（platform mode）
IMPACT_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string"},
        "analysis_mode": {"type": "string"},
        "change_summary": {
            "type": "object",
            "properties": {
                "mr_id": {"type": ["string", "null"]},
                "changed_files_count": {"type": "integer"},
                "feature_clusters": {"type": "array", "items": {"type": "string"}},
                "overall_risk": {"type": "string"},
            },
        },
        "changed_units": {"type": "array", "items": {"type": "object"}},
        "impact_scope": {
            "type": "object",
            "properties": {
                "affected_pages": {"type": "array", "items": {"type": "string"}},
                "affected_components": {"type": "array", "items": {"type": "string"}},
                "affected_apis": {"type": "array", "items": {"type": "string"}},
                "affected_services": {"type": "array", "items": {"type": "string"}},
                "affected_flows": {"type": "array", "items": {"type": "string"}},
                "evidence": {"type": "array", "items": {"type": "object"}},
            },
        },
        "logic_changes": {"type": "array", "items": {"type": "object"}},
        "risk_assessment": {"type": "array", "items": {"type": "object"}},
        "suggested_validation_items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "item": {"type": "string"},
                    "priority": {"type": "string"},
                    "source": {"type": "string"},
                    "reason": {"type": "string"},
                    "affected_pages": {"type": "array", "items": {"type": "string"}},
                    "affected_apis": {"type": "array", "items": {"type": "string"}},
                    "risk_tags": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["item", "priority"],
            },
        },
        "pending_questions": {"type": "array", "items": {"type": "string"}},
        "entry_coverage_matrix": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["schema_version", "impact_scope", "suggested_validation_items"],
}

_SCHEMA_VERSION = "1.0"


def _resolve_claude_exe() -> str:
    """解析真实 claude 可执行文件，供 code-graph / code-impact 共享。"""
    cli = settings.claude_cli_path
    path = shutil.which(cli) or cli
    if os.name == "nt":
        folder = os.path.dirname(path)
        search_bases = ([folder] if folder else []) + [os.path.join(os.environ.get("APPDATA", ""), "npm")]
        for base in search_bases:
            cand = os.path.join(base, "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe")
            if os.path.exists(cand):
                return cand
    return path


def _skill_dir() -> Path:
    base = Path(settings.skills_dir)
    if not base.is_absolute():
        base = (Path.cwd() / base).resolve()
    return base / "code-change-test-impact"


def _load_skill_methodology() -> str:
    """读入 skill 方法论文本并按预算截断（装 prompt，不再让 LLM 去磁盘 Read）。

    只装 SKILL.md + reference.md（六步工作流 + 风险启发 + 入口覆盖矩阵），
    examples/maven-guide 略过以省 token；总量受 code_impact_skill_budget_chars 限制。
    """
    skill = _skill_dir()
    budget = settings.code_impact_skill_budget_chars
    chunks: list[str] = []
    for fname in ("SKILL.md", "reference.md"):
        fp = skill / fname
        if fp.exists():
            try:
                chunks.append(f"# {fname}\n" + fp.read_text(encoding="utf-8"))
            except OSError:
                pass
    if not chunks:
        return (
            "方法论（内置兜底）：先辨变更类型→定影响面（页面/组件/接口/服务/流程）→"
            "列逻辑变化与关注点→评风险等级→给带优先级的建议覆盖项→列待确认问题。"
            "无搜索证据不得声称调用方覆盖完整。"
        )
    text = "\n\n".join(chunks)
    return text[:budget] + ("\n…（方法论已按预算截断）" if len(text) > budget else "")


def _guardian_section(gimpact: dict | None) -> str:
    """把 Guardian 合入态图谱的「存量依赖面」渲染成 prompt 段。None=不可用，如实标注让 LLM 别当有。"""
    if not gimpact or not gimpact.get("items"):
        return (
            "\n\n## 存量依赖面（Guardian 合入态图谱）\n"
            "（图谱不可用/该仓未接入——本次「谁依赖了改动代码」为 LLM 基于 diff 的**推断**，"
            "精度有限，跨仓调用方可能看不全；请对回归范围结论标注证据不足。）"
        )
    lines = [
        "\n\n## 存量依赖面（Guardian 合入态图谱 · 确定性事实，非推断）",
        "以下是改动文件在 master 上的**依赖者文件**（谁 import/引用了你改的代码），来自代码图谱。",
        "回归建议必须覆盖这些依赖者；带『热度』的是高流量路径，优先级更高：",
    ]
    for it in gimpact["items"]:
        deps = it.get("dependents") or []
        hot = it.get("hotness")
        hot_s = f"  [热度 {hot}]" if hot is not None else ""
        if deps:
            tops = "、".join(f"{d.get('file')}(深度{d.get('depth')})" for d in deps[:15])
            more = f" 等{len(deps)}个" if len(deps) > 15 else ""
            lines.append(f"- {it['path']}{hot_s} → 依赖者: {tops}{more}")
        else:
            lines.append(f"- {it['path']}{hot_s} → 无存量依赖者（叶子文件/未索引/仅 .vue 消费未入图）")
        for g in (it.get("gaps") or [])[:3]:
            lines.append(f"    影响未知(gap): {g}")
    return "\n".join(lines)


def _build_prompt(summary: StaticSummary, *, mr_id: str | None, requirement_context: str | None,
                  guardian_impact: dict | None = None) -> str:
    """拼 prompt：方法论 + 结构化摘要 + 受限 bounded_diff（req 3：输入是摘要，不是全量代码）。"""
    methodology = _load_skill_methodology()
    sm = summary.summary_dict()
    files_tbl = "\n".join(
        f"- [{f.layer}|risk{f.risk_score}] {f.status} {f.path} (+{f.additions}/-{f.deletions})"
        for f in sorted(summary.changed_files, key=lambda x: -x.risk_score)[:settings.code_impact_max_files]
    ) or "（无变更文件）"
    trunc_note = (
        f"\n\n注意：本次变更较大，仅高风险核心文件的 diff 送入分析；以下文件因限流未附 diff，"
        f"请据文件名/层次判断其影响但标注证据不足：{'、'.join(summary.truncated_files)}"
        if summary.truncated_files else ""
    )
    req_note = f"\n\n关联需求上下文：\n{requirement_context}" if requirement_context else ""
    return (
        "你是资深测试架构师，正在对一次代码变更做「测试影响分析」(platform mode)。\n"
        "下面已由平台**静态扫描**给出变更文件清单、分类与风险分，并只附上高风险核心文件的 diff。\n"
        "你**只能基于我提供的这些结构化摘要与 diff 作答，不要臆造未提供的代码**；证据不足处如实标注。\n\n"
        f"## 方法论（严格遵循）\n{methodology}\n\n"
        f"## 变更结构化摘要（静态扫描产出）\n"
        f"- base…head：{summary.base}…{summary.head_sha or summary.head or 'HEAD'}\n"
        f"- 统计：{json.dumps(sm, ensure_ascii=False)}\n"
        f"## 变更文件清单\n{files_tbl}{trunc_note}{req_note}"
        f"{_guardian_section(guardian_impact)}\n\n"
        f"## 受限 diff（仅高风险核心文件）\n```diff\n{summary.bounded_diff[:settings.code_impact_max_diff_bytes]}\n```\n\n"
        "分析完成后，只输出一个符合下面 JSON Schema 的 JSON 对象（不要 markdown 代码块、不要解释）。"
        f'schema_version 固定为 "{_SCHEMA_VERSION}"。change_summary.mr_id 填 {json.dumps(mr_id)}。'
        "每个影响面/逻辑变化结论尽量带 evidence（引用上面 diff 里的 file:相对位置或 hunk）。\n"
        f"JSON Schema:\n{json.dumps(IMPACT_SCHEMA, ensure_ascii=False)}"
    )


def _validate_and_degrade(impact: dict) -> tuple[str, dict]:
    """JSON Schema 字段级降级（方案 8.4 硬要求2）：缺关键字段则 degraded 但保留其它，不整单拒收。"""
    if not isinstance(impact, dict) or not impact:
        return "failed", {}
    impact.setdefault("schema_version", _SCHEMA_VERSION)
    impact.setdefault("analysis_mode", "platform")
    degraded = False
    if not impact.get("impact_scope"):
        impact["impact_scope"] = {}
        degraded = True
    if impact.get("suggested_validation_items") is None:
        impact["suggested_validation_items"] = []
        degraded = True
    return ("degraded" if degraded else "done"), impact


async def run_analysis(
    summary: StaticSummary,
    *,
    mr_id: str | None = None,
    requirement_context: str | None = None,
) -> dict:
    """基于静态摘要跑 LLM 影响分析，返回 {status, schema_version, impact_json, impact_md, error_message}。

    输入是 `code_change_static` 的确定性产出（摘要 + 受限 diff），LLM 不接触仓库全量代码。
    无变更文件时直接 done 空结果，不浪费 token。
    """
    if not summary.changed_files:
        empty = {"schema_version": _SCHEMA_VERSION, "analysis_mode": "platform",
                 "change_summary": {"mr_id": mr_id, "changed_files_count": 0},
                 "impact_scope": {}, "suggested_validation_items": [],
                 "pending_questions": ["未检测到变更文件（base 与 target 一致或 diff 为空）"]}
        return {"status": "done", "schema_version": _SCHEMA_VERSION, "impact_json": empty,
                "impact_md": _render_md(empty), "error_message": None}

    # Guardian 合入态图谱：查改动文件的存量下游依赖（确定性）。降级安全：不可用返回 None。
    from app.services import guardian_client
    paths = [f.path for f in sorted(summary.changed_files, key=lambda x: -x.risk_score)]
    guardian_impact = await guardian_client.impact_of(paths)

    def _degraded_with_guardian(err: str) -> dict:
        """LLM 失败也保住确定性部分：变更清单 + Guardian 存量依赖面照常挂出、可展示。"""
        base = {
            "schema_version": _SCHEMA_VERSION, "analysis_mode": "platform",
            "change_summary": {"mr_id": mr_id, "changed_files_count": summary.total_files,
                               "overall_risk": None},
            "impact_scope": {}, "suggested_validation_items": [],
            "pending_questions": [f"LLM 分析未完成：{err}（存量依赖面为确定性结果，不受影响）"],
            "guardian_impact": guardian_impact,
        }
        return {"status": "degraded", "schema_version": _SCHEMA_VERSION, "impact_json": base,
                "impact_md": _render_md(base), "error_message": err}

    prompt = _build_prompt(summary, mr_id=mr_id, requirement_context=requirement_context,
                           guardian_impact=guardian_impact)
    # 用平台已配的 provider(如 openai_responses/gpt-5.4)做结构化输出——发起人 key 由调用方经
    # set_current_ai_key 注入。输入仍是结构化摘要+受限diff(见 [[code-impact-llm-input]])，不给仓库工具。
    try:
        impact = await get_provider().tool(
            _IMPACT_SYSTEM, prompt, "emit_impact", IMPACT_SCHEMA, _CODE_IMPACT_MAX_TOKENS,
            reasoning_effort="low")  # 限推理档：防推理吃光预算导致结构化输出为空(gpt-5.x)
    except Exception as e:  # noqa: BLE001 失败不阻塞(存量依赖面仍保留)
        return _degraded_with_guardian(str(e)[:300])

    status, impact = _validate_and_degrade(impact)
    if status == "failed":
        return _degraded_with_guardian("LLM 未产出合法结构化结果")
    # 回填静态扫描已知的确定性字段（LLM 可能漏填/少填）
    cs = impact.setdefault("change_summary", {})
    cs.setdefault("mr_id", mr_id)
    cs.setdefault("changed_files_count", summary.total_files)
    # 存量依赖面：确定性事实单独挂出（前端「代码影响分析」tab 展示，标注来源=Guardian图谱）
    impact["guardian_impact"] = guardian_impact  # None=图谱不可用/未接入（前端如实提示）
    return {
        "status": status,
        "schema_version": impact.get("schema_version"),
        "impact_json": impact,
        "impact_md": _render_md(impact),
        "error_message": None,
    }


def _render_md(impact: dict) -> str:
    """由结构化 impact_json 生成人读 impact.md（挂详情页/下载）。"""
    cs = impact.get("change_summary") or {}
    scope = impact.get("impact_scope") or {}
    lines = ["# 代码影响分析报告", ""]
    lines.append(f"- 总体风险：{cs.get('overall_risk', '—')}")
    lines.append(f"- 变更文件数：{cs.get('changed_files_count', '—')}")
    if cs.get("feature_clusters"):
        lines.append(f"- 功能簇：{'、'.join(cs['feature_clusters'])}")
    lines += ["", "## 影响面", ""]
    for k, label in [("affected_pages", "页面"), ("affected_components", "组件"),
                     ("affected_apis", "接口"), ("affected_services", "服务"), ("affected_flows", "流程")]:
        vals = scope.get(k) or []
        if vals:
            lines.append(f"- {label}：{'、'.join(vals)}")
    sug = impact.get("suggested_validation_items") or []
    if sug:
        lines += ["", "## 建议覆盖项", ""]
        for s in sug:
            lines.append(f"- [{s.get('priority', '')}] {s.get('item', '')}"
                         + (f"（{s.get('reason')}）" if s.get("reason") else ""))
    gi = impact.get("guardian_impact")
    if gi and gi.get("items"):
        lines += ["", "## 存量依赖面（Guardian 合入态图谱 · 确定性）", ""]
        for it in gi["items"]:
            deps = it.get("dependents") or []
            hot = f"（热度 {it['hotness']}）" if it.get("hotness") is not None else ""
            lines.append(f"- `{it['path']}`{hot}：依赖者 {len(deps)} 个"
                         + ("，" + "、".join(str(d.get("file")) for d in deps[:6]) if deps else "，无存量依赖者/未索引"))
    pq = impact.get("pending_questions") or []
    if pq:
        lines += ["", "## 待确认问题", ""] + [f"- {q}" for q in pq]
    return "\n".join(lines)
