"""All ORM models for the AI Test Platform."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, Boolean, ForeignKey, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from pgvector.sqlalchemy import Vector
from app.config import settings as _settings
from app.database import Base

_EMBED_DIM = _settings.embed_dim


def _uuid() -> str:
    return str(uuid.uuid4())


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    product_line: Mapped[str | None] = mapped_column(String(50))
    case_id_prefix: Mapped[str] = mapped_column(String(20), default="CASE")
    feishu_webhook: Mapped[str | None] = mapped_column(String(500))
    feishu_doc_url: Mapped[str | None] = mapped_column(String(500))
    # 飞书项目(Meego)生产缺陷同步配置(留空则该项目不启用同步)
    feishu_project_space_id: Mapped[str | None] = mapped_column(String(100))
    feishu_project_defect_filter: Mapped[dict | None] = mapped_column(JSONB)  # 生产缺陷筛选条件
    feishu_project_rootcause_field: Mapped[str | None] = mapped_column(String(100))  # 「问题原因」字段 key
    ci_gate_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    pass_rate_threshold: Mapped[float] = mapped_column(Float, default=80.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    requirements: Mapped[list[Requirement]] = relationship(back_populates="project", cascade="all, delete-orphan")
    test_cases: Mapped[list[TestCase]] = relationship(back_populates="project", cascade="all, delete-orphan")
    executions: Mapped[list[Execution]] = relationship(back_populates="project", cascade="all, delete-orphan")
    quality_gate_config: Mapped[QualityGateConfig | None] = relationship(
        back_populates="project", uselist=False, cascade="all, delete-orphan"
    )
    page_structure_caches: Mapped[list[PageStructureCache]] = relationship(
        back_populates="project", cascade="all, delete-orphan"
    )


class Requirement(Base):
    __tablename__ = "requirements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    product_line: Mapped[str | None] = mapped_column(String(50))
    iteration: Mapped[str | None] = mapped_column(String(100))  # 迭代/版本号，看板按此维度筛选(5.2.2)
    source: Mapped[str] = mapped_column(String(50), default="manual")  # manual/feishu/jira
    source_record_id: Mapped[str | None] = mapped_column(String(100))  # 飞书Bitable record_id，用于同步去重
    status: Mapped[str] = mapped_column(String(30), default="pending_analysis")  # pending_analysis/analyzing/pending_case_generation/generating_cases/pending_test/testing/done
    attachment_path: Mapped[str | None] = mapped_column(String(500))  # 图片需求的存储路径
    # 问题点清单: {source_req_id, product_line, issue_points:[{issue_id,description,module,platforms[],confirmation_points:[]}]}
    analysis_result: Mapped[dict | None] = mapped_column(JSONB)
    analysis_confirmation: Mapped[str | None] = mapped_column(Text)  # 用户对分析结果的最终确认意见
    owner_name: Mapped[str | None] = mapped_column(String(200))  # 归属人=把需求添加/同步到平台的登录人姓名
    participant_names: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")  # 导入/参与过该需求的人员
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    project: Mapped[Project] = relationship(back_populates="requirements")
    test_cases: Mapped[list[TestCase]] = relationship(back_populates="requirement", cascade="all, delete-orphan")
    slices: Mapped[list[RequirementSlice]] = relationship(back_populates="requirement", cascade="all, delete-orphan")


class RequirementSlice(Base):
    """需求切片：同一需求可被多人按不同范围分头负责。

    原文(title/content)仍在 Requirement 单一保存；分析/确认/归属/范围/状态下沉到切片，
    各切片各自分析、生成用例、执行，互不覆盖。旧数据迁移为一条 is_default 的「全文」切片。
    """
    __tablename__ = "requirement_slices"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    requirement_id: Mapped[str] = mapped_column(ForeignKey("requirements.id"), index=True)
    owner_name: Mapped[str | None] = mapped_column(String(200))  # 该范围的负责人
    scope_label: Mapped[str] = mapped_column(String(200), default="全文")  # 范围名，如"支付模块"
    scope_text: Mapped[str | None] = mapped_column(Text)  # 圈选的原文片段（空=全文）；多次圈选累加
    pending_scope: Mapped[str | None] = mapped_column(Text)  # 上次分析之后新追加、尚未分析的增量原文
    scope_image_tokens: Mapped[list | None] = mapped_column(JSONB)  # 选区内的图片 token
    analysis_result: Mapped[dict | None] = mapped_column(JSONB)
    analysis_confirmation: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="pending_analysis")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)  # True=全文默认切片(旧数据/未拆分)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    requirement: Mapped[Requirement] = relationship(back_populates="slices")


class TestCase(Base):
    __tablename__ = "test_cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(String(50), unique=True, index=True)  # TC-{PREFIX}-{seq:04d}
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    requirement_id: Mapped[str | None] = mapped_column(ForeignKey("requirements.id"))
    slice_id: Mapped[str | None] = mapped_column(ForeignKey("requirement_slices.id"), index=True)  # 归属的需求切片
    product_line: Mapped[str | None] = mapped_column(String(50))
    source_req_id: Mapped[str | None] = mapped_column(String(50))
    modules: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    platforms: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)  # 含 backend_api(后端接口)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    priority: Mapped[str] = mapped_column(String(10), default="P2")  # P0/P1/P2
    preconditions: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    steps: Mapped[list] = mapped_column(JSONB, default=list)  # [{seq, action, expected}]
    expected_result: Mapped[str | None] = mapped_column(Text)
    source_issue_point: Mapped[str | None] = mapped_column(String(50))  # 关联 issue_id（增量重生成去重依据，勿动）
    secondary_feature: Mapped[str | None] = mapped_column(String(60))  # 二级功能分组(从需求原文提取，脑图中间层用)
    case_type: Mapped[str] = mapped_column(String(20), default="ui")  # ui/api
    last_status: Mapped[str] = mapped_column(String(20), default="not_run")  # passed/failed/skipped/not_run
    script: Mapped[str | None] = mapped_column(Text)  # 主产物正文（壳/test），兼容旧模型
    script_path: Mapped[str | None] = mapped_column(String(255))  # 执行入口的仓库相对路径
    script_status: Mapped[str] = mapped_column(String(30), default="pending")  # pending/generating/ready/failed
    framework_repo_id: Mapped[str | None] = mapped_column(String(36))  # 生成进哪个框架仓库（FrameworkRepo.id）
    # 原生产物文件集：{repo_type, primary_target, notes, artifacts:[{path,content,action}]}
    generated_artifacts: Mapped[dict | None] = mapped_column(JSONB)
    is_automated: Mapped[bool] = mapped_column(Boolean, default=False)  # 是否已生成自动化脚本
    # 是否已正式纳入用例库：用例库直接导入/手工新增=True；需求侧生成/导入=False，执行通过后置 True(单向，永久保留)
    in_library: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    review_status: Mapped[str | None] = mapped_column(String(30))  # None=normal, "pending_review"=需与库中已有用例确认
    similar_case_id: Mapped[str | None] = mapped_column(String(36))  # 相似用例的UUID（无FK，避免级联问题）
    tags: Mapped[dict | None] = mapped_column(JSONB)
    # ── AI 质量闭环：覆盖项与来源（方案 7.2/10.2）────────────────────────
    # covered_items: 质量判断单元(N↔N用例)，内嵌不建独立表。每项:
    #   {item_id, name, object, action, expected, scenario_type, risk_tags[], sources[],
    #    matched_rules[], priority, reason, coverage_status(not_covered/covered/failed),
    #    source_issue_id?, embedding:null}
    covered_items: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    sources: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")  # 用例级来源汇总
    risk_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")  # AI 打标，非强制
    matched_rules: Mapped[list[str]] = mapped_column(ARRAY(String), default=list, server_default="{}")  # 命中 QualityRule id 留痕
    reason: Mapped[str | None] = mapped_column(Text)  # 本用例为何生成/来源说明
    affected_page_nodes: Mapped[list | None] = mapped_column(JSONB)  # skill 影响面: 页面标识(V1 定标识体系占位)
    affected_api_nodes: Mapped[list | None] = mapped_column(JSONB)   # skill 影响面: 接口标识
    regression_flag: Mapped[str | None] = mapped_column(String(20))  # need_regression/need_adjust（阶段B命中已有用例时标）
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    project: Mapped[Project] = relationship(back_populates="test_cases")
    requirement: Mapped[Requirement | None] = relationship(back_populates="test_cases")
    results: Mapped[list[TestResult]] = relationship(back_populates="test_case", cascade="all, delete-orphan")


class TestCaseLog(Base):
    __tablename__ = "test_case_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    test_case_id: Mapped[str] = mapped_column(String(36), index=True)  # no FK — survives case lifecycle
    operation: Mapped[str] = mapped_column(String(20))  # create / update / delete
    operator: Mapped[str] = mapped_column(String(100), default="系统")
    snapshot: Mapped[dict | None] = mapped_column(JSONB)  # case data at this point in time
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    trigger: Mapped[str] = mapped_column(String(50), default="manual")  # manual/ci/scheduled
    status: Mapped[str] = mapped_column(String(30), default="pending")  # pending/running/done/failed
    total: Mapped[int] = mapped_column(Integer, default=0)
    # 本次执行覆盖的用例 id 列表（创建时落库）。用于任意查看者从服务端还原「哪些用例正在跑」，
    # 不再只依赖发起人浏览器 localStorage —— 否则别人打开同一需求看不到执行状态。
    case_ids: Mapped[list | None] = mapped_column(JSONB)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    pass_rate: Mapped[float] = mapped_column(Float, default=0.0)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    execution_mode: Mapped[str] = mapped_column(String(10), default="mock")  # mock/real
    runner_node: Mapped[str | None] = mapped_column(String(100))  # 执行机标识，便于审计
    ci_gate_result: Mapped[dict | None] = mapped_column(JSONB)  # {releasable: bool, blocking_reasons: [...]}
    error_message: Mapped[str | None] = mapped_column(Text)  # 批次级失败原因（崩溃/超时/调度失败），供前端展示与重试判断
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)

    project: Mapped[Project] = relationship(back_populates="executions")
    results: Mapped[list[TestResult]] = relationship(back_populates="execution", cascade="all, delete-orphan")


class TestResult(Base):
    __tablename__ = "test_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"))
    test_case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id"))
    status: Mapped[str] = mapped_column(String(20), nullable=False)  # passed/failed/skipped/error
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    screenshot_url: Mapped[str | None] = mapped_column(String(500))
    # 接口用例执行轨迹：{request:{method,url,headers,body}, response:{status,headers,body}, trace_id}
    api_trace: Mapped[dict | None] = mapped_column(JSONB)
    # App 真机执行的分步轨迹：[{seq, action, expected, shots:[url], note}]，供执行结果按步骤展示截图
    ui_trace: Mapped[list | None] = mapped_column(JSONB)
    failure_type: Mapped[str | None] = mapped_column(String(20))  # script_error/env_error/real_defect
    ai_diagnosis: Mapped[dict | None] = mapped_column(JSONB)
    repair_suggestion: Mapped[str | None] = mapped_column(Text)
    defect_status: Mapped[str] = mapped_column(String(30), default="none")  # none/pending_review/confirmed/ignored
    # ── AI 质量闭环：覆盖项级执行证据（方案 12.1）─────────────────────────
    # checked_points: 覆盖项维度验证证据(区别于 ui_trace 内步骤级 checks)
    #   [{item_id, covered_item_name, status(passed/failed/blocked/not_checked), evidence, screenshot_url, step_seq}]
    checked_points: Mapped[list | None] = mapped_column(JSONB)
    actual_visited_pages: Mapped[list | None] = mapped_column(JSONB)  # 运行时实际访问页面(来自 page_captures)
    actual_api_calls: Mapped[list | None] = mapped_column(JSONB)      # 运行时实际接口调用 [{method,url,status}]
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    execution: Mapped[Execution] = relationship(back_populates="results")
    test_case: Mapped[TestCase] = relationship(back_populates="results")
    defects: Mapped[list[Defect]] = relationship(back_populates="test_result", cascade="all, delete-orphan")


class Defect(Base):
    """缺陷诊断Agent(Agent5)输出落地表，为后续飞书多维表格(Bitable)同步预留字段。"""
    __tablename__ = "defects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    test_result_id: Mapped[str | None] = mapped_column(ForeignKey("test_results.id"), nullable=True)
    execution_id: Mapped[str | None] = mapped_column(ForeignKey("executions.id"), nullable=True)
    test_case_id: Mapped[str | None] = mapped_column(ForeignKey("test_cases.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="3级-一般")  # 缺陷等级，取枚举 category=severity
    confidence: Mapped[str] = mapped_column(String(10), default="MEDIUM")  # HIGH/MEDIUM/LOW
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/ticket_created/confirmed/ignored/duplicate
    draft_ticket: Mapped[dict | None] = mapped_column(JSONB)
    feishu_ticket_id: Mapped[str | None] = mapped_column(String(100))
    external_ticket_id: Mapped[str | None] = mapped_column(String(100))  # 外部缺陷单号(如 external task system 缺陷 id)
    external_ticket_url: Mapped[str | None] = mapped_column(String(500))  # 外部单据可访问 URL
    duplicate_of_defect_id: Mapped[str | None] = mapped_column(ForeignKey("defects.id"))
    # ── AI 质量闭环：逃逸缺陷回溯（方案 15.3）─────────────────────────────
    source: Mapped[str] = mapped_column(String(20), default="execution", server_default="execution")  # execution/production/manual
    covered_item_ids: Mapped[list | None] = mapped_column(JSONB)  # 逃逸缺陷落到哪些覆盖项(回溯用)
    retrospect: Mapped[dict | None] = mapped_column(JSONB)  # 逃逸回溯结论(遗留列, 已停用)
    root_cause: Mapped[str | None] = mapped_column(Text)  # 生产缺陷问题原因(飞书项目同步来), 用于沉淀经验
    external_source: Mapped[str | None] = mapped_column(String(30))  # 外部来源标识, 如 feishu_project
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    test_result: Mapped[TestResult] = relationship(back_populates="defects")
    duplicate_of: Mapped[Defect | None] = relationship(remote_side=[id])


class QualityGateConfig(Base):
    """质量看板规则引擎(设计文档5.3节)可配置阈值；P0相关规则为强制项，不在此配置。"""
    __tablename__ = "quality_gate_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), unique=True)
    overall_pass_rate_threshold: Mapped[float] = mapped_column(Float, default=95.0)
    enable_overall_pass_rate_gate: Mapped[bool] = mapped_column(Boolean, default=True)
    p1_failure_threshold: Mapped[int] = mapped_column(Integer, default=3)
    enable_p1_failure_gate: Mapped[bool] = mapped_column(Boolean, default=True)
    pass_rate_wow_drop_threshold: Mapped[float] = mapped_column(Float, default=5.0)
    coverage_threshold: Mapped[float] = mapped_column(Float, default=80.0)
    # 阶段六：AI 发布建议门禁策略（方案 13.3）——AI release_suggestion 作为门禁输入项
    release_policy: Mapped[str] = mapped_column(String(20), default="advisory", server_default="advisory")  # advisory(仅提示)/warn(卡人工确认)/block(卡流水线)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    project: Mapped[Project] = relationship(back_populates="quality_gate_config")


class PageStructureCache(Base):
    """页面结构缓存（设计文档7.3节）：存储URL模式对应的DOM区域结构及哈希，用于测试执行时的上下文注入。"""
    __tablename__ = "page_structure_caches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    base_url: Mapped[str | None] = mapped_column(String(300))  # PC 端基础地址（录制/探索时所选）
    url_pattern: Mapped[str] = mapped_column(String(500), nullable=False)  # 页面路径 e.g. /module/items/create
    page_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))  # 人工填写的描述（探索时填过则有，否则空）
    dom_hash: Mapped[dict | None] = mapped_column(JSONB)  # {region_name: hash_string}
    regions: Mapped[list | None] = mapped_column(JSONB)   # [{name, selector, elements:[{name,selector,type}]}]
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/stale/needs_update
    graph_node_id: Mapped[str | None] = mapped_column(String(300), index=True)  # 阶段五：对应图谱 Page 节点 ID

    project: Mapped[Project] = relationship(back_populates="page_structure_caches")


class PageCacheDiff(Base):
    """页面缓存差异提醒队列（设计文档7.3.5）。

    执行中发现区块hash与共享缓存不一致时，不立即更新缓存，而是在此排队，
    执行结束后统一推送提醒；测试人员确认后才把新结构写回共享缓存。
    """
    __tablename__ = "page_cache_diffs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    cache_id: Mapped[str | None] = mapped_column(String(36))  # 关联缓存条目（新页面时为空）
    url_pattern: Mapped[str] = mapped_column(String(500), nullable=False)
    page_name: Mapped[str] = mapped_column(String(200), nullable=False)
    changed_regions: Mapped[list | None] = mapped_column(JSONB)  # 变化的区块名列表
    new_regions: Mapped[list | None] = mapped_column(JSONB)      # 本次探索到的新区块结构 [{name,selector,elements}]
    new_dom_hash: Mapped[dict | None] = mapped_column(JSONB)     # 本次计算的新 {region: hash}
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/confirmed/dismissed
    resolved_by: Mapped[str | None] = mapped_column(String(100))  # 确认/忽略操作人
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class EnumDefinition(Base):
    __tablename__ = "enum_definitions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    category: Mapped[str] = mapped_column(String(100), nullable=False)
    key: Mapped[str] = mapped_column(String(100), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    parent_key: Mapped[str | None] = mapped_column(String(100))  # module按product_line分组等层级关系
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    __table_args__ = (UniqueConstraint("category", "key", name="uq_enum_category_key"),)


class EnumLog(Base):
    __tablename__ = "enum_logs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    category: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    enum_id: Mapped[str | None] = mapped_column(String(36))  # 对应 EnumDefinition.id，删除后仍可查
    operation: Mapped[str] = mapped_column(String(20))  # create / update / delete
    value: Mapped[str | None] = mapped_column(String(200))   # 操作时的 label 值
    operator: Mapped[str] = mapped_column(String(100), default="系统")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class PlatformUser(Base):
    """平台用户表：存储从 external task system 同步过来的用户及其在本平台的角色。"""
    __tablename__ = "platform_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # external task system SSO 用户的外部 id；本地账号用户可为空
    external_task_user_id: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin / user
    # 本地账号密码登录（external task system 不可用时）
    username: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # False = 已禁用
    auth_source: Mapped[str] = mapped_column(String(20), default="external_task")  # external_task / local
    # 该用户专属的 AI 中转 key：所有 AI 操作(分析/生成/执行/App)走发起人自己的 key；
    # 未配置则发起 AI 操作时报错"未分配key"，由管理员在用户管理里配置。
    ai_api_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class UserFavPhone(Base):
    """用户常用项：执行弹框输入时可「加入常用」，走输入框下拉快选/删除。kind 区分常用号码/常用租户。
    按用户(JWT sub)隔离——每人只见/选自己加的；同一账号下 PC 与 App 通用(不分端)。"""
    __tablename__ = "user_fav_phones"
    __table_args__ = (UniqueConstraint("user_sub", "kind", "phone", name="uq_fav_user_kind_value"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_sub: Mapped[str] = mapped_column(String(200), nullable=False, index=True)  # JWT sub(用户标识)
    kind: Mapped[str] = mapped_column(String(20), default="phone", server_default="phone")  # phone / tenant
    phone: Mapped[str] = mapped_column(String(80), nullable=False)  # 值(手机号或租户名)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class AutomationGenSwitch(Base):
    """自动化用例生成开关：按"端"(脚本类型)控制执行测试通过后是否自动生成自动化用例。

    platform 取自 script_generator.determine_script_type 的输出域：
    api / web / app / harmony / miniprogram。每端一行，enabled 默认 True
    （保持历史行为：不配置时一律生成）。仅管理员可改（见 routers/system_settings）。
    """
    __tablename__ = "automation_gen_switches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    platform: Mapped[str] = mapped_column(String(40), unique=True, nullable=False, index=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)  # 默认关闭，需管理员显式开启
    updated_by: Mapped[str | None] = mapped_column(String(200))  # 操作管理员姓名/邮箱
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class AppSetting(Base):
    """通用键值配置（仅管理员可改）。当前承载 SSO 对接认证地址(external_task_sso_url)等。"""
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_by: Mapped[str | None] = mapped_column(String(200))  # 操作管理员姓名/邮箱
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class FrameworkRepo(Base):
    """框架仓库登记表 —— 把"已有自动化框架的 git 仓库"绑定到平台。

    平台的执行模型从"用例=自包含临时脚本"改为"框架仓库绑定 + 索引驱动生成 + 仓库内执行"：
    - 生成阶段：按 repo_type 产出该框架的原生用例（接口=YAML+壳；UI=test/flow/page），
      并引用 index_json 里登记的真实积木（接口 AWFunc 清单 / UI pages/flows/components/fixtures）。
    - 执行阶段：在 local_path 的 checkout 内按 run_command 跑框架自身命令，而非空临时目录。

    一个仓库可同时含多端（web_ui_automation 的 master 分支同时有 ui_web 与 ui_app），
    用 repo_type 区分一条登记主要服务哪个端；同仓多端可登记多条共用 git_url/branch。
    """
    __tablename__ = "framework_repos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))  # 可空=全局框架，跨项目复用
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    repo_type: Mapped[str] = mapped_column(String(20), nullable=False)  # interface/web/app
    description: Mapped[str | None] = mapped_column(String(500))

    # ── git 绑定 ────────────────────────────────────────────────
    git_url: Mapped[str] = mapped_column(String(500), nullable=False)
    branch: Mapped[str] = mapped_column(String(100), default="main")  # app 框架在 master
    local_path: Mapped[str | None] = mapped_column(String(500))  # 执行机上的 checkout 路径（克隆后回填）

    # ── 目录约定（相对仓库根）────────────────────────────────────
    tests_root: Mapped[str | None] = mapped_column(String(300))   # 用例根：ui_web/tests, ui_app/tests, cases/
    data_root: Mapped[str | None] = mapped_column(String(300))    # 数据根：接口 YAML 目录；UI 端可空
    keyword_root: Mapped[str | None] = mapped_column(String(300)) # 接口 AWFunc 关键字库目录

    # ── 命令约定 ────────────────────────────────────────────────
    run_command: Mapped[str | None] = mapped_column(Text)         # 执行模板，支持 {target}/{marker}/{project} 占位
    install_command: Mapped[str | None] = mapped_column(Text)     # 依赖安装：pip install -r requirements.txt
    env_json: Mapped[dict | None] = mapped_column(JSONB)          # 执行环境变量/额外配置

    # ── 积木索引（P2 扫描产物缓存）──────────────────────────────
    # interface: {aw_funcs:[{class,func,params,doc,module}]}
    # web/app:  {pages:[...], flows:[...], components:[...], fixtures:[...]}
    index_json: Mapped[dict | None] = mapped_column(JSONB)
    index_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/indexing/ready/failed
    index_commit: Mapped[str | None] = mapped_column(String(60))  # 索引时的 git sha，判断是否需重扫
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime)

    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class MobileDevice(Base):
    """worker 上报的真机（执行机心跳时 upsert）。

    App 真机执行走"执行机 worker"模型：worker 跑在插真机的机器上，主动连平台心跳上报它连的设备，
    平台据此知道有哪些设备在线、归谁、是否公共默认、是否忙。任务按 serial 定向到对应 worker。
    """
    __tablename__ = "mobile_devices"
    __table_args__ = (UniqueConstraint("worker_id", "serial", name="uq_mobile_device_worker_serial"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    worker_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)  # 执行机配的 WORKER_ID
    worker_name: Mapped[str | None] = mapped_column(String(200))
    serial: Mapped[str] = mapped_column(String(200), nullable=False, index=True)     # adb serial（设备唯一）
    model: Mapped[str | None] = mapped_column(String(200))
    owner_user_id: Mapped[str | None] = mapped_column(String(36))   # 归属用户（空=公共）
    is_shared: Mapped[bool] = mapped_column(Boolean, default=False)  # 默认/公共设备：无 worker 的人兜底走它
    online: Mapped[bool] = mapped_column(Boolean, default=True)
    busy: Mapped[bool] = mapped_column(Boolean, default=False)       # 正在跑任务（一台同刻一条）
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class AppExecJob(Base):
    """App 真机执行任务：平台派发 → worker 领取 → 执行机本地连真机执行 → 回传结果。

    平台侧 WorkerDispatchRunner 建本任务后阻塞轮询其 status/result；worker 领取后用本地
    AndroidAgentRunner 执行，把 RunOutcome 写回 result。这样执行主循环（建 TestResult/缺陷/收尾）零改动。
    """
    __tablename__ = "app_exec_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"), index=True)
    test_case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id"))
    project_id: Mapped[str | None] = mapped_column(String(36))
    target_serial: Mapped[str | None] = mapped_column(String(200), index=True)  # 定向设备（空=兜底默认设备）
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)  # pending/claimed/running/succeeded/failed/error/timeout/cancelled
    payload: Mapped[dict | None] = mapped_column(JSONB)   # {case_id,title,steps,expected_result,base_url,platforms}
    result: Mapped[dict | None] = mapped_column(JSONB)    # worker 回传：{status,duration_ms,error_message,failure_type,ui_trace}
    claimed_worker: Mapped[str | None] = mapped_column(String(100))
    claimed_serial: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


# ════════════════════════════════════════════════════════════════════════
# AI 质量闭环平台 MVP（方案 docs/AI质量闭环平台设计方案.md V2.1）
# ════════════════════════════════════════════════════════════════════════


class BusinessRepo(Base):
    """被测业务代码仓库登记（与 FrameworkRepo 严格区分：那是自动化框架仓，这是被分析的业务仓）。

    供代码影响分析 worker clone/checkout 后直跑 code-change-test-impact skill。
    token 可空：手动「粘贴 diff / 本地已 checkout 路径」模式无需仓库凭证。
    """
    __tablename__ = "business_repos"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str | None] = mapped_column(ForeignKey("projects.id"))  # 可空=全局
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    git_url: Mapped[str] = mapped_column(String(500), nullable=False)
    default_branch: Mapped[str] = mapped_column(String(100), default="master")
    token: Mapped[str | None] = mapped_column(String(500))  # 只读 deploy token，可空
    workspace_path: Mapped[str | None] = mapped_column(String(500))  # worker 上的 checkout 路径（复用回填）
    clone_depth: Mapped[int] = mapped_column(Integer, default=50)  # 浅克隆深度
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class AppLoginRecipe(Base):
    """App 执行前自动登录的「配方」（配置驱动，替代硬编码在 app_login.py 里的每端 goals）。

    登录方式全端固定：手机号 + 固定验证码。各 App 只在「怎么选环境 / 要不要选租户」上不同：
    - match_keywords: 逗号分隔关键词，对 "{platform_key} {label}" 小写做「全部命中」匹配（如 "商,app"）。
    - env_steps: 选环境的自然语言步骤，一行一步，可用 {env} 占位；留空=该 App 无需选环境。
    - restart_after_env: 选完环境是否杀 App 重启（Android App 切环境后需重启才生效）。
    - needs_tenant: 是否需要登录后校验/切换租户（仅Android App）。
    启动页/引导页/权限弹窗由通用前置目标自动趟过，无需在配方里写。
    """
    __tablename__ = "app_login_recipes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(200), nullable=False)  # 显示名，如 "Android App"
    match_keywords: Mapped[str] = mapped_column(String(300), nullable=False)  # "商,app" 全命中匹配
    env_steps: Mapped[str | None] = mapped_column(Text)  # 选环境步骤(一行一步)，可空
    restart_after_env: Mapped[bool] = mapped_column(Boolean, default=False)
    needs_tenant: Mapped[bool] = mapped_column(Boolean, default=False)
    # 切租户/租户的自然语言步骤(可空)。空=用默认「首页左上角租户名」流程(Android App)；
    # 有值=按此流程切(如Android App：点『我的』→看当前租户→不符则点租户名进列表→选目标租户)。可用 {tenant} 占位。
    tenant_steps: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class AppLoginScript(Base):
    """一次成功登录录下的「动作轨迹」，供后续同 App 同分辨率设备**确定性回放**（省 token、更稳）。

    视觉登录成功后把每步动作(tap 坐标/input/swipe/wait)按序存下；下次登录先回放这套轨迹，
    仅当回放后判定未登录才回退视觉重录。按 (app_package, width, height) 唯一，分辨率不同各存一份。
    input 动作按 tag(phone/code)标记，回放时替换成当次账号/验证码。
    """
    __tablename__ = "app_login_scripts"
    __table_args__ = (UniqueConstraint("app_package", "width", "height", name="uq_login_script_pkg_res"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    app_package: Mapped[str] = mapped_column(String(200), nullable=False)
    width: Mapped[int] = mapped_column(Integer, nullable=False)
    height: Mapped[int] = mapped_column(Integer, nullable=False)
    script: Mapped[list] = mapped_column(JSONB, nullable=False)  # [{a,x,y,text,dir,sec,tag}, ...]
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class ChangeImpactRecord(Base):
    """一次代码影响分析的记录（skill platform mode 输出 + 状态）。方案 8.4/14.2。

    手动触发（无 GitLab webhook）：trigger_mode ∈ paste_diff / local_path / repo_branch。
    失败/超时不阻塞流程（status=failed/degraded），报告显式标注「影响分析缺失」。
    """
    __tablename__ = "change_impact_records"
    __table_args__ = (
        UniqueConstraint("business_repo_id", "mr_id", "head_sha", name="uq_change_impact_idem"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    requirement_id: Mapped[str | None] = mapped_column(ForeignKey("requirements.id"), index=True)
    business_repo_id: Mapped[str | None] = mapped_column(ForeignKey("business_repos.id"))
    trigger_mode: Mapped[str] = mapped_column(String(20), default="paste_diff")  # paste_diff/local_path/repo_branch
    mr_id: Mapped[str | None] = mapped_column(String(100))
    repo_label: Mapped[str | None] = mapped_column(String(200))  # 展示用仓库名/路径
    base_branch: Mapped[str | None] = mapped_column(String(100))
    target_branch: Mapped[str | None] = mapped_column(String(100))
    head_sha: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/failed/degraded
    schema_version: Mapped[str | None] = mapped_column(String(20))
    impact_json: Mapped[dict | None] = mapped_column(JSONB)  # 机读结构化影响(方案 8.4 契约)
    impact_md: Mapped[str | None] = mapped_column(Text)      # 人读完整报告
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)


class QualityRule(Base):
    """第一层硬规则引擎（确定性）。方案 6.1。

    MVP：从 skill reference.md blast-radius 导入只读初始集（seed 脚本），无编辑界面。
    命中逻辑最简：covered_item.risk_tags ∩ match_tags 非空即命中 → 抬优先级/补必测项/留痕。
    """
    __tablename__ = "quality_rules"

    id: Mapped[str] = mapped_column(String(50), primary_key=True)  # R-012 风格
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    match_tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)  # 命中风险标签集
    min_priority: Mapped[str | None] = mapped_column(String(10))  # 命中后优先级下限 P0/P1
    required_covered_items: Mapped[list | None] = mapped_column(JSONB)  # 强制必测覆盖项名
    source: Mapped[str | None] = mapped_column(String(300))  # 规则出处(skill 文档锚点)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class ReviewFeedback(Base):
    """测试 Review 反馈留痕（方案 11.3）。MVP 记录对覆盖项的增删改，为阶段三经验沉淀铺垫。"""
    __tablename__ = "review_feedbacks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    test_case_id: Mapped[str] = mapped_column(String(36), index=True)  # no FK — 跨用例生命周期留存
    requirement_id: Mapped[str | None] = mapped_column(String(36), index=True)
    target_type: Mapped[str] = mapped_column(String(30), default="covered_item")  # case/covered_item/experience_hit/impact_scope
    action: Mapped[str] = mapped_column(String(30))  # add_item/edit_item/delete_item/accept/reject
    before: Mapped[dict | None] = mapped_column(JSONB)
    after: Mapped[dict | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    operator: Mapped[str] = mapped_column(String(100), default="系统")
    found_bug_later: Mapped[bool | None] = mapped_column(Boolean)  # 由缺陷关联回填
    experience_id: Mapped[str | None] = mapped_column(String(36), index=True)  # 已沉淀为哪条经验(避免重复沉淀)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())


class Experience(Base):
    """历史经验库（方案 6.2，数据飞轮核心）。从反馈闭环沉淀，非人工预建。

    trigger_context 引用图谱节点稳定 ID（阶段五后），过渡期用 服务/接口名 + risk_tags。
    embedding = title + trigger_context 向量化，支持 pgvector 语义召回；缺 embedding 时降级标签召回。
    """
    __tablename__ = "experiences"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    # {affected_features[], service_node_ids[], api_node_ids[], risk_tags[]}
    trigger_context: Mapped[dict] = mapped_column(JSONB, default=dict)
    suggested_covered_items: Mapped[list] = mapped_column(JSONB, default=list)  # 建议加入的覆盖项名
    source: Mapped[str] = mapped_column(String(30), default="tester_feedback")  # tester_feedback/found_bug/production_issue/high_value_case
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[dict | None] = mapped_column(JSONB)  # {found_bug, bug_id}
    embedding: Mapped[list | None] = mapped_column(Vector(_EMBED_DIM))  # title+trigger_context 向量；无 key 时 null
    stats: Mapped[dict] = mapped_column(JSONB, default=lambda: {"hit_count": 0, "adopt_count": 0, "reject_count": 0})
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    status: Mapped[str] = mapped_column(String(20), default="candidate")  # candidate/active/dormant/stale
    merged_from: Mapped[list] = mapped_column(ARRAY(String), default=list, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class CoveredItemVec(Base):
    """覆盖项向量索引（方案 7.4，阶段四）。

    covered_items 仍内嵌 TestCase.covered_items(JSONB)；本表是其向量副本，随增删改维护，
    支持 ivfflat 语义检索（归并/复用判断），避免把 covered_items 迁出用例。
    """
    __tablename__ = "covered_item_vecs"
    __table_args__ = (UniqueConstraint("case_id", "item_id", name="uq_covered_item_vec"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    item_id: Mapped[str] = mapped_column(String(40), index=True)
    case_id: Mapped[str] = mapped_column(String(36), index=True)
    requirement_id: Mapped[str | None] = mapped_column(String(36), index=True)
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(500))
    struct_key: Mapped[str | None] = mapped_column(String(300), index=True)  # object+action 归一化
    embedding: Mapped[list | None] = mapped_column(Vector(_EMBED_DIM))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class GraphNode(Base):
    """代码事实图谱节点（方案 9.5）。7 类：file/component/page/api/service/db/mq。

    node_id = 稳定 ID（方案 9.3）：page:{project}:{url_pattern} / api:{method}:{path} /
    svc:{repo}:{Class} / comp:{repo}:{name} / file:{repo}:{path} / db:{schema.table} / mq:{topic}。
    """
    __tablename__ = "graph_node"

    node_id: Mapped[str] = mapped_column(String(400), primary_key=True)
    node_type: Mapped[str] = mapped_column(String(20), nullable=False, index=True)  # file/component/page/api/service/db/mq
    repo: Mapped[str | None] = mapped_column(String(200), index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    attrs: Mapped[dict] = mapped_column(JSONB, default=dict)  # path/route/module/language...
    seen_in_version: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="active", index=True)  # active/stale/removed
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class GraphEdge(Base):
    """代码事实图谱边（方案 9.4）。每条边带来源/置信度/证据/版本。"""
    __tablename__ = "graph_edge"
    __table_args__ = (
        UniqueConstraint("from_node", "to_node", "edge_type", "source", name="uq_graph_edge"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    from_node: Mapped[str] = mapped_column(String(400), index=True)
    to_node: Mapped[str] = mapped_column(String(400), index=True)
    edge_type: Mapped[str] = mapped_column(String(30), nullable=False)  # defines/belongs_to/calls/handled_by/accesses/visits
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # static_scan/runtime_capture/llm_inferred
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    evidence: Mapped[str | None] = mapped_column(Text)
    seen_in_version: Mapped[str | None] = mapped_column(String(60))
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/stale
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime, default=func.now())


class AppNavRecipe(Base):
    """App 导航路径缓存：记录"某 App 内到达某目标页/入口"的成功导航经验，供下次直接回放/提示，
    避免每次执行都靠 AI 视觉从零盲滑找入口（如"工作台→资源列表"要滑十几次）。

    按 (app_pkg, target) 唯一。entry=入口页(如 工作台)，swipes=进入 entry 后到目标需滚动的大致次数，
    direction=滚动方向，near_text=目标附近的分组/参照文案。命中率高 → 下次注入提示 + 原生 scroll.to 直达。"""
    __tablename__ = "app_nav_recipe"
    __table_args__ = (
        UniqueConstraint("app_pkg", "target", name="uq_app_nav_recipe"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    app_pkg: Mapped[str] = mapped_column(String(120), index=True)   # 目标 App 包名
    target: Mapped[str] = mapped_column(String(80))                 # 目标页/入口文案（如 资源列表）
    entry: Mapped[str | None] = mapped_column(String(80))           # 入口页（如 工作台）
    path: Mapped[list | None] = mapped_column(JSONB)                # 完整路径文案链（如 ["工作台","资源列表"]）
    swipes: Mapped[int] = mapped_column(Integer, default=0)         # 进入入口后到目标的大致滚动次数
    direction: Mapped[str] = mapped_column(String(10), default="up")  # 滚动方向（up=向下浏览）
    near_text: Mapped[str | None] = mapped_column(String(120))      # 目标附近的分组/参照文案
    hits: Mapped[int] = mapped_column(Integer, default=1)           # 命中/复用次数
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class AppStepRecipe(Base):
    """App 步骤操作经验：某测试步骤判【通过】时，把它成功的操作序列(点/输入/滑动)按
    (app_pkg, 步骤操作文本签名) 记下来，下次同 App 同步骤先【确定性回放】这套操作——把
    "怎么切搜索字段、点哪聚焦搜索框、输什么"这些难点直接复用, 不再每次靠 AI 视觉从零试错。

    actions: [{a:tap|input|swipe|back, text:文案目标, value:输入值, direction:方向, xr/yr:相对坐标}]"""
    __tablename__ = "app_step_recipe"
    __table_args__ = (
        UniqueConstraint("app_pkg", "step_sig", name="uq_app_step_recipe"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    app_pkg: Mapped[str] = mapped_column(String(120), index=True)   # 目标 App 包名
    step_sig: Mapped[str] = mapped_column(String(200))             # 步骤操作文本归一化签名
    actions: Mapped[list | None] = mapped_column(JSONB)            # 成功操作序列
    n_actions: Mapped[int] = mapped_column(Integer, default=0)
    hits: Mapped[int] = mapped_column(Integer, default=1)          # 复用次数
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class TestDataRequirement(Base):
    """用例的【数据要求】（测试数据准备与状态编排 方案 §9.2/§31.2）。

    一条用例可挂 0..N 条数据要求，各自声明一个数据对象及其目标状态/约束，用 alias 作执行变量名。
    MVP-0：strategy=MANUAL，测试人员在 Review 页把实际值直接填进 manual_values；执行前置阶段
    据此生成 ExecutionContext 变量（`${alias.field}`），确定性注入 web/app 步骤文本、api 变量、
    登录凭证——彻底替掉"账号/订单号写死在步骤里/preconditions 靠人肉"。
    后续阶段：strategy=AUTO 时由 scenario 绑定 + 已认证能力自动造数（本表字段已为其预留，多为可空）。
    """
    __tablename__ = "test_data_requirement"
    __table_args__ = (
        UniqueConstraint("case_id", "alias", name="uq_test_data_requirement_case_alias"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(String(64), index=True)     # 关联 test_cases.case_id（如 TC-ZN-0240）
    alias: Mapped[str] = mapped_column(String(60))                   # 执行变量名（如 targetOrder）
    data_type: Mapped[str | None] = mapped_column(String(60))        # 数据对象类型（如 order）
    schema_version: Mapped[str | None] = mapped_column(String(20))
    target_state: Mapped[dict | None] = mapped_column(JSONB)         # 目标状态（枚举态）
    constraints: Mapped[dict | None] = mapped_column(JSONB)          # 约束
    strategy: Mapped[str] = mapped_column(String(20), default="MANUAL")       # MANUAL / AUTO / FIND_EXISTING
    reuse_policy: Mapped[str] = mapped_column(String(20), default="CREATE_NEW")  # CREATE_NEW / REUSE_POOL(V4)
    isolation: Mapped[str] = mapped_column(String(20), default="EXCLUSIVE")   # EXCLUSIVE / SHARED_READONLY
    post_state: Mapped[dict | None] = mapped_column(JSONB)           # 测试后应达状态（后置校验，可空）
    cleanup_policy: Mapped[dict | None] = mapped_column(JSONB)       # on_success/on_failure/on_abort
    scenario_id: Mapped[str | None] = mapped_column(String(80))      # Review 敲定的场景绑定（AUTO 用）
    scenario_version: Mapped[str | None] = mapped_column(String(20))
    output_key: Mapped[str | None] = mapped_column(String(60))       # 绑定场景的哪个产出对象
    depends_on: Mapped[list | None] = mapped_column(JSONB)           # 同用例内 alias 依赖
    manual_values: Mapped[dict | None] = mapped_column(JSONB)        # MVP-0 人工直填的实际值 {field: value}
    required: Mapped[bool] = mapped_column(Boolean, default=True)    # 缺它是否阻断执行
    source: Mapped[list | None] = mapped_column(JSONB)               # requirement/code_impact/tester_review
    confidence: Mapped[float | None] = mapped_column(Float)          # AI 解析置信度
    review_status: Mapped[str] = mapped_column(String(20), default="draft")   # draft / pending / approved
    approved_snapshot: Mapped[dict | None] = mapped_column(JSONB)    # 审核快照
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class DataObjectSchema(Base):
    """数据对象 Schema 注册表（方案 §8/§31.1）：统一定义平台支持的数据对象及其字段/状态/枚举/操作符，
    防止 pay_status/payState/payFlag 各说各话。sensitive 字段在 schema_json 里标记，注入引擎据此把
    密码/令牌分流进凭证、不进步骤/AI。"""
    __tablename__ = "test_data_object_schema"
    __table_args__ = (UniqueConstraint("data_type", "schema_version", name="uq_data_object_schema"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    data_type: Mapped[str] = mapped_column(String(60), index=True)   # 如 order
    schema_version: Mapped[str] = mapped_column(String(20))
    schema_json: Mapped[dict | None] = mapped_column(JSONB)          # {states, constraints, sensitive...}
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")  # ACTIVE / DEPRECATED
    owner: Mapped[str | None] = mapped_column(String(80))
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class DataCapability(Base):
    """数据能力（方案 §9.4/§31.4）：被认证的"造/查数据"的执行单元（API_CASE/TEST_API/QUERY/WORKFLOW…）。
    只有 status=ACTIVE 且 approval_status=APPROVED 的能力才可被编排引用。生命周期 DRAFT→VERIFYING→ACTIVE。"""
    __tablename__ = "test_data_capability"
    __table_args__ = (UniqueConstraint("capability_id", "version", name="uq_data_capability"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    capability_id: Mapped[str] = mapped_column(String(120), index=True)   # 如 order.create
    version: Mapped[str] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(120))
    provider_type: Mapped[str] = mapped_column(String(20))   # API_CASE/TEST_API/QUERY/WORKFLOW/SCRIPT/UI_CASE/MOCK/POOL
    business_domain: Mapped[str | None] = mapped_column(String(60))
    executor_ref: Mapped[str | None] = mapped_column(String(200))   # 如 api-case://TC-ORDER-0012
    input_schema: Mapped[dict | None] = mapped_column(JSONB)
    parameter_mapping: Mapped[dict | None] = mapped_column(JSONB)
    output_extract: Mapped[dict | None] = mapped_column(JSONB)      # {字段: jsonpath}，复用 api_runner extract
    idempotency_supported: Mapped[bool] = mapped_column(Boolean, default=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, default=5)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    sla_seconds: Mapped[int | None] = mapped_column(Integer)
    side_effects: Mapped[list | None] = mapped_column(JSONB)
    cleanup_mode: Mapped[str] = mapped_column(String(30), default="TTL")   # DELETE/TTL/RELEASE/NONE...
    cleanup_capability_id: Mapped[str | None] = mapped_column(String(120))
    supports_strong_rollback: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_hours: Mapped[int] = mapped_column(Integer, default=24)
    supported_environments: Mapped[list | None] = mapped_column(JSONB)   # ["sit","dev"]
    owner: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")   # DRAFT/VERIFYING/ACTIVE/DEGRADED/DISABLED
    approval_status: Mapped[str] = mapped_column(String(20), default="PENDING")   # PENDING/APPROVED
    last_verify: Mapped[dict | None] = mapped_column(JSONB)   # 最近一次试运行认证结果
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


class DataScenario(Base):
    """数据场景（方案 §9.3/§31.3）：把若干能力编排成"造出满足某目标状态的数据对象"的固定、带版本流程。
    guarantees 声明本场景保证达成的目标状态（Recommender 据此筛、发布门禁据此校验 postconditions）。"""
    __tablename__ = "test_data_scenario"
    __table_args__ = (UniqueConstraint("scenario_id", "version", name="uq_data_scenario"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    scenario_id: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[str] = mapped_column(String(20))
    name: Mapped[str | None] = mapped_column(String(160))
    data_type: Mapped[str | None] = mapped_column(String(60))        # 主对象
    provides: Mapped[list | None] = mapped_column(JSONB)             # 本场景产出的全部对象
    supported_schema_versions: Mapped[list | None] = mapped_column(JSONB)
    supported_environments: Mapped[list | None] = mapped_column(JSONB)
    supported_constraints: Mapped[list | None] = mapped_column(JSONB)
    guarantees: Mapped[dict | None] = mapped_column(JSONB)           # {output_key: {state: value}}
    workflow: Mapped[list | None] = mapped_column(JSONB)             # 步骤编排（含 fallback）
    postconditions: Mapped[list | None] = mapped_column(JSONB)       # 发布门禁校验其覆盖 guarantees
    outputs: Mapped[dict | None] = mapped_column(JSONB)             # 按 output_key 导出
    credentials: Mapped[dict | None] = mapped_column(JSONB)         # 凭证库（只走登录链路）
    status: Mapped[str] = mapped_column(String(20), default="DRAFT")   # DRAFT/ACTIVE/DEPRECATED
    owner: Mapped[str | None] = mapped_column(String(80))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
