from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, Field


# ── Project ────────────────────────────────────────────────────────────────
class ProjectCreate(BaseModel):
    name: str
    description: Optional[str] = None
    product_line: Optional[str] = None
    case_id_prefix: str = "CASE"
    feishu_webhook: Optional[str] = None
    feishu_doc_url: Optional[str] = None
    ci_gate_enabled: bool = False
    pass_rate_threshold: float = 80.0


class ProjectOut(ProjectCreate):
    id: str
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


# ── Requirement ────────────────────────────────────────────────────────────
class ConfirmationPoint(BaseModel):
    point_id: str
    content: str
    status: str = "pending_confirmation"  # pending_confirmation / confirmed
    confirmation: Optional[str] = None
    no_confirmation_needed: bool = False


class IssuePoint(BaseModel):
    issue_id: str
    description: str  # 需求文档原文中有争议/表述不清的内容片段（原文引用）
    module: Optional[str] = None  # 内部使用：供Agent2选择modules，不在UI展示
    platforms: list[str] = Field(default_factory=list)  # 内部使用：供Agent2选择platforms/case_type，不在UI展示
    confirmation_points: list[ConfirmationPoint] = Field(default_factory=list)


class AnalysisResult(BaseModel):
    source_req_id: Optional[str] = None
    product_line: Optional[str] = None
    issue_points: list[IssuePoint] = Field(default_factory=list)


class RequirementCreate(BaseModel):
    project_id: str
    title: str
    content: str
    product_line: Optional[str] = None
    iteration: Optional[str] = None
    source: str = "manual"


class RequirementOut(RequirementCreate):
    id: str
    status: str
    attachment_path: Optional[str] = None
    analysis_result: Optional[dict[str, Any]] = None
    analysis_confirmation: Optional[str] = None
    source_record_id: Optional[str] = None
    owner_name: Optional[str] = None
    slice_count: int = 0  # 该需求的「负责范围」数(不含默认全文)；>0 时列表行才可展开看切片
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class RequirementUpdate(BaseModel):
    analysis_confirmation: Optional[str] = None


# ── RequirementSlice（需求切片：多人多范围）────────────────────────────────────
class SliceCreate(BaseModel):
    scope_label: str = "全文"
    scope_text: Optional[str] = None
    scope_image_tokens: Optional[list[str]] = None
    owner_name: Optional[str] = None


class SliceUpdate(BaseModel):
    scope_label: Optional[str] = None
    scope_text: Optional[str] = None
    scope_image_tokens: Optional[list[str]] = None
    owner_name: Optional[str] = None


class SliceOut(BaseModel):
    id: str
    requirement_id: str
    owner_name: Optional[str] = None
    scope_label: str
    scope_text: Optional[str] = None
    scope_image_tokens: Optional[list[str]] = None
    analysis_result: Optional[dict[str, Any]] = None
    analysis_confirmation: Optional[str] = None
    status: str
    is_default: bool
    has_pending: bool = False  # 是否有「上次分析后新追加、尚未分析」的增量范围
    appended: bool = True       # 「加入我的范围」时本次圈选是否真正追加(False=已在范围内被去重跳过)
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class ConfirmationPointUpdate(BaseModel):
    confirmation: Optional[str] = None
    no_confirmation_needed: bool = False


class FeishuLinkSyncRequest(BaseModel):
    link: str


# ── TestCase ───────────────────────────────────────────────────────────────
class TestStep(BaseModel):
    seq: int
    action: str
    expected: str = ""


class TestCaseCreate(BaseModel):
    project_id: str
    requirement_id: Optional[str] = None
    product_line: Optional[str] = None
    source_req_id: Optional[str] = None
    modules: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    title: str
    priority: str = "P2"  # P0/P1/P2
    preconditions: list[str] = Field(default_factory=list)
    steps: list[TestStep] = Field(default_factory=list)
    expected_result: Optional[str] = None
    source_issue_point: Optional[str] = None
    case_type: str = "ui"  # ui/api
    tags: Optional[list[str]] = None


class TestCaseOut(TestCaseCreate):
    id: str
    case_id: str
    last_status: str
    script: Optional[str] = None
    script_path: Optional[str] = None
    script_status: str
    is_automated: bool = False
    in_library: bool = False
    review_status: Optional[str] = None
    similar_case_id: Optional[str] = None
    similar_case_case_id: Optional[str] = None  # computed: similar case's display ID
    similar_case_title: Optional[str] = None    # computed: similar case's title
    deleted_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class CaseReviewAction(BaseModel):
    action: str  # "keep" | "update_existing"


class BatchCaseReviewAction(BaseModel):
    case_ids: list[str]
    action: str  # "keep" | "update_existing"


class BatchNoConfirmRequest(BaseModel):
    point_ids: list[str]


class PlatformsConfirmRequest(BaseModel):
    requirement_id: str
    slice_id: Optional[str] = None
    platforms: list[str]


# ── Execution ──────────────────────────────────────────────────────────────
class ExecutionCreate(BaseModel):
    project_id: str
    name: str
    trigger: str = "manual"
    case_ids: Optional[list[str]] = None
    requirement_id: Optional[str] = None
    run_mode: str = "fresh"  # fresh=执行后通过则生成/更新脚本; automated=运行已有脚本不更新
    # PC web 执行的账号覆盖：{端名: {role} 选已配账号 | {username,password,tenant_name?} 临时账号(用完即弃,不入框架)}
    account_overrides: Optional[dict] = None
    # 批内按功能块排序(操作先于查询)。仅需求详情批量执行传 True；用例库执行不传。
    reorder: bool = False
    # App 真机执行的目标设备 serial：指定则派发到该真机的执行机 worker；不传则走公共默认设备兜底。
    target_device: Optional[str] = None
    # PC/Web 执行环境：sit(默认) / dev。决定 base_url 取哪个环境的地址；缺该环境地址的端回退 SIT。
    env: str = "sit"
    # App 换测试包：{app端名: 包版本id}。执行前按 app 解析下载链接，推到真机先卸旧包再装新包；不传=不换包。
    package_overrides: Optional[dict] = None


class BlockingReason(BaseModel):
    rule: str
    message: str
    severity: str = "block"  # block / warn


class CIGateResult(BaseModel):
    releasable: bool
    blocking_reasons: list[BlockingReason] = Field(default_factory=list)


class ExecutionOut(BaseModel):
    id: str
    project_id: str
    name: str
    trigger: str
    status: str
    total: int
    passed: int
    failed: int
    skipped: int
    pass_rate: float
    duration_ms: int
    ci_gate_result: Optional[dict[str, Any]] = None
    error_message: Optional[str] = None
    created_at: datetime
    finished_at: Optional[datetime] = None
    model_config = {"from_attributes": True}


# ── TestResult ─────────────────────────────────────────────────────────────
class TestResultOut(BaseModel):
    id: str
    execution_id: str
    test_case_id: str
    status: str
    duration_ms: int
    error_message: Optional[str] = None
    screenshot_url: Optional[str] = None
    api_trace: Optional[dict[str, Any]] = None
    ui_trace: Optional[list[Any]] = None
    failure_type: Optional[str] = None
    ai_diagnosis: Optional[dict[str, Any]] = None
    repair_suggestion: Optional[str] = None
    defect_status: str
    created_at: datetime
    model_config = {"from_attributes": True}


class DefectReviewUpdate(BaseModel):
    defect_status: str  # confirmed / ignored / pending_review


# ── Defect ─────────────────────────────────────────────────────────────────
class DefectOut(BaseModel):
    id: str
    test_result_id: str
    execution_id: str
    test_case_id: str
    requirement_id: Optional[str] = None       # 关联需求（由 test_case 派生）
    requirement_title: Optional[str] = None
    title: str
    severity: str
    confidence: str
    status: str
    draft_ticket: Optional[dict[str, Any]] = None
    feishu_ticket_id: Optional[str] = None
    external_ticket_id: Optional[str] = None
    external_ticket_url: Optional[str] = None
    duplicate_of_defect_id: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    model_config = {"from_attributes": True}


class DefectUpdate(BaseModel):
    status: Optional[str] = None  # draft(待复核)/confirmed(待处理)/ignored,duplicate(无需处理)/ticket_created
    severity: Optional[str] = None  # 缺陷等级（取枚举 category=severity）
    duplicate_of_defect_id: Optional[str] = None
    title: Optional[str] = None  # 复核时可编辑标题
    draft_ticket: Optional[dict[str, Any]] = None  # 复核时可编辑缺陷草稿(摘要/复现步骤等)


# ── Enum ───────────────────────────────────────────────────────────────────
class EnumCreate(BaseModel):
    category: str
    key: str
    label: str
    parent_key: Optional[str] = None
    sort_order: int = 0
    is_active: Optional[bool] = None  # 启用/停用；None=不改动（仅在明确切换启用状态时传）


class EnumOut(EnumCreate):
    id: str
    is_active: bool
    created_at: datetime
    model_config = {"from_attributes": True}


# ── Quality Gate Config ───────────────────────────────────────────────────
class QualityGateConfigOut(BaseModel):
    id: str
    project_id: str
    overall_pass_rate_threshold: float
    enable_overall_pass_rate_gate: bool
    p1_failure_threshold: int
    enable_p1_failure_gate: bool
    pass_rate_wow_drop_threshold: float
    coverage_threshold: float
    model_config = {"from_attributes": True}


class QualityGateConfigUpdate(BaseModel):
    overall_pass_rate_threshold: Optional[float] = None
    enable_overall_pass_rate_gate: Optional[bool] = None
    p1_failure_threshold: Optional[int] = None
    enable_p1_failure_gate: Optional[bool] = None
    pass_rate_wow_drop_threshold: Optional[float] = None
    coverage_threshold: Optional[float] = None


# ── Page Structure Cache（7.3）─────────────────────────────────────────────
class PageElement(BaseModel):
    name: str
    selector: str
    type: str = "unknown"  # input/button/link/...


class PageRegion(BaseModel):
    name: str
    selector: str
    elements: list[PageElement] = Field(default_factory=list)


# ── Agent4/Agent5 tool_use 输出 ────────────────────────────────────────────
class RepairSuggestion(BaseModel):
    root_cause: str
    fix_type: str  # selector_change/timing/data/environment/logic/unknown
    failure_type: str  # script_error/env_error/real_defect
    suggestion: str
    fixed_script: Optional[str] = None


class DefectDraft(BaseModel):
    is_real_defect: bool
    severity: str  # 缺陷等级，取枚举 category=severity（1级-致命/2级-严重/3级-一般/4级-轻微）
    confidence: str  # HIGH/MEDIUM/LOW
    title: str
    summary: str
    type: str  # functional/ui/performance/security/compatibility/other
    reproduction_steps: list[str] = Field(default_factory=list)
    affected_scope: str = ""


# ── Agent Pipeline ─────────────────────────────────────────────────────────
class PipelineRequest(BaseModel):
    requirement_id: str
    regenerate: bool = False
    scope_text: Optional[str] = None  # 仅针对选中的部分需求内容进行分析/生成；为空则用全文
    scope_image_tokens: Optional[list[str]] = None  # 选区内包含的图片 token(精确只发这些图)
    slice_id: Optional[str] = None  # 目标切片；为空或默认全文切片走需求级既有逻辑，非默认切片走切片级
    mode: Optional[str] = None  # 切片级分析/生成：full=全量(整段范围)，incremental=增量(仅新追加范围)；默认 full


class PipelineStatus(BaseModel):
    requirement_id: str
    status: str
    message: str
    failed: bool = False  # 分析/生成是否刚失败(状态已回退，仅作提示，不是独立状态)
    cases_generated: int = 0
    scripts_generated: int = 0
