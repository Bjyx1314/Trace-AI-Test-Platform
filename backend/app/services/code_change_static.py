"""变更静态抽取（req 2/3/7：静态扫描为主，LLM 只吃结构化摘要 + 受限 diff）。

设计要点：
- **不调 LLM**：文件清单、增删行、层次分类、风险打分、功能簇全部由确定性规则算出；
- LLM 只在下一步（runner）拿到本模块产出的 `bounded_diff` + `summary`，不再放它满仓库 grep；
- **大变更限流（req 7）**：按风险分排序，只把高风险核心文件的 hunk 装进 bounded_diff，
  超 `code_impact_max_files` / `code_impact_max_diff_bytes` 的截断并如实登记 truncated；
- 两种入口：`extract_from_git`（local_path/repo_branch，跑 git diff base...head）、
  `extract_from_diff`（paste_diff，直接解析粘贴的 unified diff）。
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings

# ── 风险启发（path 关键词 → 加分；对应 skill reference.md 的 blast-radius 清单）──────────
_HIGH_RISK_KEYWORDS = (
    "pay", "payment", "billing", "refund", "settle", "settlement", "money", "amount",
    "price", "order", "trade", "wallet", "account", "coupon", "discount", "auth",
    "login", "permission", "token", "security", "tenant", "risk", "credit",
)
# 层次分类：(正则, 层名, 基础风险分)。先命中先归属。
_LAYER_RULES: list[tuple[re.Pattern, str, int]] = [
    (re.compile(r"(?i)(^|/)(db/migration|migrations?|flyway|liquibase)/|\.sql$"), "db_migration", 5),
    (re.compile(r"(?i)Controller[^/]*\.(java|kt)$"), "backend_controller", 4),
    (re.compile(r"(?i)Service(Impl)?[^/]*\.(java|kt)$"), "backend_service", 4),
    (re.compile(r"(?i)(Mapper|Repository|Dao)[^/]*\.(java|kt|xml)$"), "backend_mapper", 3),
    (re.compile(r"(?i)\.(yml|yaml|properties|conf|ini|env)$|(^|/)application[^/]*\."), "config", 3),
    (re.compile(r"(?i)(^|/)(views?|pages?)/.*\.(vue|tsx|jsx|ts)$"), "frontend_page", 2),
    (re.compile(r"(?i)(^|/)(api|services?|request)/.*\.(ts|js)$"), "frontend_api", 2),
    (re.compile(r"(?i)(^|/)components?/.*\.(vue|tsx|jsx|ts)$"), "frontend_component", 2),
    (re.compile(r"(?i)(test|spec|__tests__|mock)"), "test", 0),
    (re.compile(r"(?i)\.(md|txt|rst)$|(^|/)docs?/"), "doc", 0),
    (re.compile(r"(?i)\.(java|kt)$"), "backend_other", 2),
    (re.compile(r"(?i)\.(vue|tsx|jsx|ts|js)$"), "frontend_other", 1),
]


@dataclass
class ChangedFile:
    path: str
    status: str = "M"          # A/M/D/R
    additions: int = 0
    deletions: int = 0
    layer: str = "other"
    risk_score: int = 0
    diff: str = ""             # 本文件的 unified diff 片段（截断前的原文）

    def to_summary(self) -> dict:
        return {
            "path": self.path, "status": self.status,
            "additions": self.additions, "deletions": self.deletions,
            "layer": self.layer, "risk_score": self.risk_score,
        }


@dataclass
class StaticSummary:
    base: str | None = None
    head: str | None = None
    head_sha: str | None = None
    changed_files: list[ChangedFile] = field(default_factory=list)
    bounded_diff: str = ""
    truncated_files: list[str] = field(default_factory=list)  # 因限流未送 LLM 的文件
    total_files: int = 0

    def summary_dict(self) -> dict:
        by_layer: dict[str, int] = {}
        for f in self.changed_files:
            by_layer[f.layer] = by_layer.get(f.layer, 0) + 1
        # 功能簇：按二级目录粗聚（确定性，不靠 LLM）
        clusters: dict[str, int] = {}
        for f in self.changed_files:
            parts = f.path.split("/")
            key = "/".join(parts[:2]) if len(parts) > 1 else parts[0]
            clusters[key] = clusters.get(key, 0) + 1
        top_clusters = [k for k, _ in sorted(clusters.items(), key=lambda x: -x[1])[:6]]
        return {
            "total_changed_files": self.total_files,
            "by_layer": by_layer,
            "feature_clusters": top_clusters,
            "high_risk_files": [f.path for f in self.changed_files if f.risk_score >= 4][:20],
            "truncated_for_llm": self.truncated_files,
        }


def classify(path: str) -> tuple[str, int]:
    """返回 (layer, risk_score)。risk = 层基础分 + 高风险关键词命中加分。"""
    layer, base = "other", 1
    for pat, name, score in _LAYER_RULES:
        if pat.search(path):
            layer, base = name, score
            break
    low = path.lower()
    if any(k in low for k in _HIGH_RISK_KEYWORDS):
        base += 2
    return layer, base


# ── unified diff 解析（确定性）────────────────────────────────────────────────
_DIFF_HEADER = re.compile(r"^diff --git a/(.+?) b/(.+?)$")
_HUNK = re.compile(r"^@@ ")


def parse_unified_diff(diff_text: str) -> list[ChangedFile]:
    """把一段 unified diff 拆成每文件的 ChangedFile（含增删行统计与原始片段）。"""
    files: list[ChangedFile] = []
    cur: ChangedFile | None = None
    buf: list[str] = []

    def flush():
        nonlocal cur, buf
        if cur is not None:
            cur.diff = "\n".join(buf)
            files.append(cur)
        cur, buf = None, []

    for line in diff_text.splitlines():
        m = _DIFF_HEADER.match(line)
        if m:
            flush()
            path = m.group(2) or m.group(1)
            layer, risk = classify(path)
            cur = ChangedFile(path=path, layer=layer, risk_score=risk)
            buf = [line]
            continue
        if cur is None:
            continue
        buf.append(line)
        if line.startswith("new file"):
            cur.status = "A"
        elif line.startswith("deleted file"):
            cur.status = "D"
        elif line.startswith("rename from") or line.startswith("rename to"):
            cur.status = "R"
        elif line.startswith("+") and not line.startswith("+++"):
            cur.additions += 1
        elif line.startswith("-") and not line.startswith("---"):
            cur.deletions += 1
    flush()
    return files


def _git(args: list[str], cwd: Path, timeout: int = 120) -> str:
    proc = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def _build_bounded(files: list[ChangedFile]) -> tuple[str, list[str]]:
    """按风险分（再按改动规模）排序，装 bounded_diff，超限截断（req 7）。返回 (bounded_diff, truncated_paths)。"""
    ordered = sorted(files, key=lambda f: (-f.risk_score, -(f.additions + f.deletions)))
    max_files = settings.code_impact_max_files
    max_bytes = settings.code_impact_max_diff_bytes
    chosen: list[str] = []
    truncated: list[str] = []
    total = 0
    for i, f in enumerate(ordered):
        piece = f.diff or ""
        if i >= max_files or total + len(piece) > max_bytes:
            truncated.append(f.path)
            continue
        chosen.append(piece)
        total += len(piece)
    return "\n".join(chosen), truncated


def _finalize(files: list[ChangedFile], *, base=None, head=None, head_sha=None) -> StaticSummary:
    bounded, truncated = _build_bounded(files)
    return StaticSummary(
        base=base, head=head, head_sha=head_sha,
        changed_files=files, bounded_diff=bounded,
        truncated_files=truncated, total_files=len(files),
    )


def extract_from_diff(diff_text: str) -> StaticSummary:
    """paste_diff：直接解析粘贴的 unified diff。"""
    return _finalize(parse_unified_diff(diff_text or ""))


def extract_from_git(workspace: Path, base: str, head: str | None) -> StaticSummary:
    """local_path/repo_branch：git diff base...head（三点=合并基对比，与 skill 一致）。

    head 为 None 时取工作区当前 HEAD。base 默认 master 由调用方传入。
    """
    head_ref = head or "HEAD"
    # rename 检测(-M) 让改名归并为一条，不误判成删+增
    diff_text = _git(["diff", "-M", f"{base}...{head_ref}"], workspace)
    files = parse_unified_diff(diff_text)
    try:
        head_sha = _git(["rev-parse", head_ref], workspace).strip()
    except Exception:  # noqa: BLE001
        head_sha = None
    return _finalize(files, base=base, head=head, head_sha=head_sha)
