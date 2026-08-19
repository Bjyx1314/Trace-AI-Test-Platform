"""框架原生用例生成器 —— 把平台测试用例转成"目标框架的原生产物"。

与旧实现（生成自包含 pytest 脚本写进空临时目录）的根本区别：
- 产物是该框架的**原生文件集**，沉淀回仓库：
  - interface：用例 YAML（steps 引用真实 AWFunc 关键字 / 裸 HTTP）+ 3 行壳（class+MyMetaClass+case_yml_list）。
  - web/app：POM 风格 test（必要时附 flow/page），引用真实 pages/flows 方法。
- 生成**受 index 约束**：prompt 注入框架积木清单，只允许引用真实存在的关键字/页面方法，
  从源头杜绝"幻觉 API"。mock 兜底同样从 index 里挑真实积木，保证离线也产出可校验产物。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

from app.agents.base_agent import BaseAgent


@dataclass
class Artifact:
    path: str                 # 仓库相对路径
    content: str
    action: str = "create"    # create=新建/覆盖；append_case_yml=向壳的 case_yml_list 追加


@dataclass
class GeneratedArtifacts:
    repo_type: str
    artifacts: list[Artifact] = field(default_factory=list)
    primary_target: str = ""  # 执行入口（壳 test_*.py / UI test）
    notes: str | None = None

    def to_json(self) -> dict:
        return {
            "repo_type": self.repo_type,
            "primary_target": self.primary_target,
            "notes": self.notes,
            "artifacts": [asdict(a) for a in self.artifacts],
        }


def _safe(name: str, limit: int = 40) -> str:
    s = re.sub(r"[^0-9A-Za-z_一-鿿]+", "_", str(name or "case")).strip("_")
    return (s[:limit] or "case")


# ── 给 LLM 的积木摘要（控制 token，按需裁剪）───────────────────────────────

def _index_digest_interface(index: dict, max_kw: int = 200) -> str:
    lines = []
    budget = max_kw
    for c in index.get("aw_classes", []):
        kws = c.get("keywords", [])
        if not kws:
            continue
        take = kws[:max(1, budget)]
        lines.append(f"- AW类 {c['class']}（{c.get('doc') or ''}）: {', '.join(take)}")
        budget -= len(take)
        if budget <= 0:
            lines.append("- …(关键字过多已截断，可按需检索)")
            break
    return "\n".join(lines) or "(索引为空)"


def _index_digest_ui(index: dict, max_items: int = 40) -> str:
    def fmt(entries):
        out = []
        for e in entries[:max_items]:
            methods = ", ".join(m["name"] for m in e.get("methods", []))
            out.append(f"  {e['class']} ({e['module']}): {methods}")
        return out
    parts = ["[Flows]"] + fmt(index.get("flows", [])) + ["[Pages]"] + fmt(index.get("pages", []))
    fx = index.get("fixtures", [])
    if fx:
        parts += ["[Fixtures]", "  " + ", ".join(f["name"] for f in fx[:max_items])]
    return "\n".join(parts) or "(索引为空)"


# ── mock 兜底：从 index 挑真实积木，产出可校验的原生产物 ─────────────────────

def _mock_interface(case: dict, index: dict, data_root: str, tests_root: str) -> GeneratedArtifacts:
    safe = _safe(case.get("title"))
    module = (case.get("modules") or ["generated"])[0] or "generated"
    yaml_rel = f"/{data_root.strip('/')}/generated/test_{safe}.yaml"

    # 从索引挑第一个有关键字的 AW 类作为示例步骤
    aw_class = next((c for c in index.get("aw_classes", []) if c.get("keywords")), None)
    if aw_class:
        step = (
            f"    - name: {case.get('title','步骤1')}\n"
            f"      module: {module}\n"
            f"      AWFunc: {aw_class['keywords'][0]}\n"
            f"      aw_params: {{}}\n"
        )
        note = f"mock：引用真实 AW 关键字 {aw_class['class']}.{aw_class['keywords'][0]}"
    else:
        step = (
            f"    - name: {case.get('title','步骤1')}\n"
            f"      module: {module}\n"
            f"      api:\n        url: /TODO\n        method: GET\n"
            f"      validation: []\n"
        )
        note = "mock：索引无关键字，生成裸 HTTP 步骤占位"

    yaml_content = (
        f"# 自动生成（mock）：{case.get('title','')}\n"
        f"- case: {case.get('title','用例')}\n"
        f"  feature: {module}\n"
        f"  severity: {case.get('priority','P2')}\n"
        f"  steps:\n{step}"
    )
    shell_rel = f"{tests_root.strip('/')}/generated/test_{safe}.py"
    shell_content = (
        "# -*- coding: utf-8 -*-\n"
        "from utils.base.myMetaClass import MyMetaClass\n\n\n"
        f"class Test_{safe}(metaclass=MyMetaClass):\n\n"
        f"    case_yml_list = [\n        '{yaml_rel}',\n    ]\n"
    )
    return GeneratedArtifacts(
        repo_type="interface",
        artifacts=[
            Artifact(path=yaml_rel.lstrip("/"), content=yaml_content),
            Artifact(path=shell_rel, content=shell_content),
        ],
        primary_target=shell_rel,
        notes=note,
    )


def _mock_ui(case: dict, repo_type: str, index: dict, tests_root: str) -> GeneratedArtifacts:
    safe = _safe(case.get("title"))
    flow = next((f for f in index.get("flows", []) if f.get("methods")), None)
    page = next((p for p in index.get("pages", []) if p.get("methods")), None)

    if flow:
        m = flow["methods"][0]["name"]
        body = (
            f"    flow = {flow['class']}(page)\n"
            f"    result = flow.{m}()\n"
            f"    assert result is not None\n"
        )
        imp = f"# 引用真实 flow: {flow['module']} -> {flow['class']}.{m}"
        note = f"mock：引用真实 flow {flow['class']}.{m}"
    elif page:
        m = page["methods"][0]["name"]
        body = (
            f"    po = {page['class']}(page)\n"
            f"    po.{m}()\n"
        )
        imp = f"# 引用真实 page: {page['module']} -> {page['class']}.{m}"
        note = f"mock：引用真实 page {page['class']}.{m}"
    else:
        body = "    assert False, 'index 无可用 page/flow，需先补积木'\n"
        imp = "# index 为空"
        note = "mock：索引为空，生成占位断言"

    test_rel = f"{tests_root.strip('/')}/generated/test_{safe}.py"
    content = (
        "# -*- coding: utf-8 -*-\n"
        f"{imp}\n"
        "import pytest\n\n\n"
        "@pytest.mark.smoke\n"
        f"@pytest.mark.{'app' if repo_type == 'app' else 'web'}\n"
        f"def test_{safe}(page):\n"
        f'    """{case.get("title","")} —— 自动生成（mock）"""\n'
        f"{body}"
    )
    return GeneratedArtifacts(
        repo_type=repo_type,
        artifacts=[Artifact(path=test_rel, content=content)],
        primary_target=test_rel,
        notes=note,
    )


def mock_artifacts(case: dict, repo_type: str, index: dict, *, data_root: str, tests_root: str) -> GeneratedArtifacts:
    """纯函数 mock 生成（无 LLM），从 index 挑真实积木，便于离线 + 单测。"""
    if repo_type == "interface":
        return _mock_interface(case, index, data_root, tests_root)
    return _mock_ui(case, repo_type, index, tests_root)


# ── LLM 生成 ────────────────────────────────────────────────────────────────

_GEN_TOOL = {
    "description": "产出目标框架的原生用例文件集",
    "type": "object",
    "properties": {
        "files": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "仓库相对路径"},
                    "content": {"type": "string"},
                    "action": {"type": "string", "enum": ["create", "append_case_yml"]},
                },
                "required": ["path", "content"],
            },
        },
        "primary_target": {"type": "string", "description": "执行入口文件路径"},
        "notes": {"type": "string"},
    },
    "required": ["files", "primary_target"],
}

_SYSTEM = {
    "interface": (
        "你是接口自动化专家，熟悉数据驱动 AW 关键字框架。根据用例生成：\n"
        "1) 一份用例 YAML（steps 优先引用给定的真实 AWFunc 关键字；无合适关键字时用裸 HTTP 步骤 api.url/method/params+validation）。\n"
        "2) 一份 3 行壳 test_*.py：class Test_x(metaclass=MyMetaClass) + case_yml_list=[yaml路径]。\n"
        "严禁引用清单外不存在的关键字。"
    ),
    "ui": (
        "你是 UI 自动化专家，熟悉 Page Object/Flow 分层框架。根据用例生成 POM 风格 test，"
        "优先复用给定的真实 flows/pages 方法编排；确需新方法时同时产出对应 page/flow 文件。"
        "严禁引用清单外不存在的页面/方法。"
    ),
}


class FrameworkGeneratorAgent(BaseAgent):
    async def generate(self, case: dict, repo_type: str, index: dict, *,
                       data_root: str = "data", tests_root: str = "tests") -> GeneratedArtifacts:
        fallback = mock_artifacts(case, repo_type, index, data_root=data_root, tests_root=tests_root)
        if self.use_mock:
            return fallback

        kind = "interface" if repo_type == "interface" else "ui"
        digest = (_index_digest_interface(index) if kind == "interface"
                  else _index_digest_ui(index))
        user = (
            f"## 用例\n{json.dumps(case, ensure_ascii=False, indent=2)}\n\n"
            f"## 可用框架积木（只能引用以下真实存在的）\n{digest}\n\n"
            f"## 目录约定\ndata_root={data_root}  tests_root={tests_root}\n\n"
            "请产出原生用例文件集。"
        )
        result = await self.call_claude_tool(
            _SYSTEM[kind], user, "emit_files", _GEN_TOOL,
            max_tokens=4096, mock_result=None,
        )
        files = result.get("files") if isinstance(result, dict) else None
        if not files:
            return fallback
        return GeneratedArtifacts(
            repo_type=repo_type,
            artifacts=[Artifact(path=f["path"], content=f.get("content", ""),
                                action=f.get("action", "create")) for f in files],
            primary_target=result.get("primary_target") or fallback.primary_target,
            notes=result.get("notes"),
        )
