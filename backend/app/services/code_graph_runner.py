"""code-graph-scan 执行器（platform mode，方案 9.6）。照抄 code_impact_runner 子进程范式。

在被测仓库工作区内跑 claude CLI，用 Bash(git)/Read/Grep/Glob 提取代码事实图谱，输出结构化 JSON。
"""
from __future__ import annotations
import asyncio
import json
import os
import tempfile
from pathlib import Path

from app.config import settings
from app.agents.llm import _extract_json, _model_for
from app.services.code_impact_runner import _resolve_claude_exe

GRAPH_SCHEMA_HINT = {
    "schema_version": "1.0", "repo": "<repo>", "scan_mode": "full|incremental",
    "nodes": [{"node_id": "", "node_type": "page|component|api|service|file|db|mq", "name": "", "attrs": {}}],
    "edges": [{"from": "", "to": "", "edge_type": "calls|handled_by|accesses|defines|belongs_to", "source": "static_scan|llm_inferred", "confidence": 1.0, "evidence": ""}],
    "renames": [{"old": "", "new": ""}],
}


def _skill_dir() -> Path:
    base = Path(settings.skills_dir)
    if not base.is_absolute():
        base = (Path.cwd() / base).resolve()
    return base / "code-graph-scan"


def _build_prompt(repo_label: str, scan_mode: str, changed_files: list[str] | None) -> str:
    skill = _skill_dir()
    hint = ""
    if skill.exists():
        hint = (f"\n请先阅读并严格遵循 skill 文档：\n- {skill / 'SKILL.md'}\n- {skill / 'reference.md'}\n")
    scope = ""
    if scan_mode == "incremental" and changed_files:
        scope = "本次为增量扫描，只处理这些改动文件及其出边：\n" + "\n".join(f"- {f}" for f in changed_files[:200])
    else:
        scope = "本次为全量扫描：扫描整个仓库的前端路由/页面/组件/API 调用与后端 Controller/Service/DAO/DB/MQ。"
    return (
        f"你是代码事实图谱扫描器 (platform mode)。仓库标签={repo_label}。{hint}\n{scope}\n\n"
        "只输出一个符合下面结构的 JSON（不要 markdown 代码块、不要解释）。节点用稳定 ID，边必须带 evidence。\n"
        f"结构示例：\n{json.dumps(GRAPH_SCHEMA_HINT, ensure_ascii=False)}"
    )


async def _run_claude(cwd: Path, prompt: str, timeout: int) -> str:
    fd, pf = tempfile.mkstemp(suffix=".txt")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(prompt)
    exe = _resolve_claude_exe()
    flags = ["-p", "--output-format", "json", "--allowedTools", "Bash(git *),Read,Grep,Glob"]
    model = _model_for("claude_cli")
    if model:
        flags += ["--model", model]
    stdin_file = open(pf, "rb")
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, *flags, cwd=str(cwd),
            stdin=stdin_file, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise RuntimeError(f"code-graph-scan 超时（>{timeout}s）")
    finally:
        stdin_file.close()
        try:
            os.remove(pf)
        except OSError:
            pass
    if proc.returncode != 0:
        raise RuntimeError(f"claude CLI 失败(code={proc.returncode}): {err.decode(errors='ignore')[:300]}")
    envelope = json.loads(out.decode("utf-8", errors="ignore"))
    return envelope.get("result") if isinstance(envelope, dict) else str(envelope)


async def run_scan(workspace: Path, repo_label: str, *, scan_mode: str = "full",
                   changed_files: list[str] | None = None) -> dict:
    """执行扫描，返回 {status, scan(dict), error_message}。失败不抛，返回 failed。"""
    prompt = _build_prompt(repo_label, scan_mode, changed_files)
    try:
        raw = await _run_claude(workspace, prompt, settings.code_graph_timeout_sec)
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "scan": None, "error_message": str(e)[:500]}
    scan = _extract_json(raw)
    if not scan or not scan.get("nodes"):
        return {"status": "failed", "scan": None, "error_message": "扫描输出无法解析为合法图谱 JSON"}
    scan.setdefault("repo", repo_label)
    scan.setdefault("scan_mode", scan_mode)
    return {"status": "done", "scan": scan, "error_message": None}
