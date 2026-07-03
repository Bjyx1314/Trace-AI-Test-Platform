"""All ORM models for the AI Test Platform."""
from __future__ import annotations
import uuid
from datetime import datetime
from sqlalchemy import String, Text, Integer, Float, Boolean, ForeignKey, DateTime, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


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
    source_issue_point: Mapped[str | None] = mapped_column(String(50))  # 关联 issue_id
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    execution: Mapped[Execution] = relationship(back_populates="results")
    test_case: Mapped[TestCase] = relationship(back_populates="results")
    defects: Mapped[list[Defect]] = relationship(back_populates="test_result", cascade="all, delete-orphan")


class Defect(Base):
    """缺陷诊断Agent(Agent5)输出落地表，为后续飞书多维表格(Bitable)同步预留字段。"""
    __tablename__ = "defects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    test_result_id: Mapped[str] = mapped_column(ForeignKey("test_results.id"))
    execution_id: Mapped[str] = mapped_column(ForeignKey("executions.id"))
    test_case_id: Mapped[str] = mapped_column(ForeignKey("test_cases.id"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), default="3级-一般")  # 缺陷等级，取枚举 category=severity
    confidence: Mapped[str] = mapped_column(String(10), default="MEDIUM")  # HIGH/MEDIUM/LOW
    status: Mapped[str] = mapped_column(String(20), default="draft")  # draft/ticket_created/confirmed/ignored/duplicate
    draft_ticket: Mapped[dict | None] = mapped_column(JSONB)
    feishu_ticket_id: Mapped[str | None] = mapped_column(String(100))
    external_ticket_id: Mapped[str | None] = mapped_column(String(100))  # 外部缺陷单号
    external_ticket_url: Mapped[str | None] = mapped_column(String(500))  # 外部单据可访问 URL
    duplicate_of_defect_id: Mapped[str | None] = mapped_column(ForeignKey("defects.id"))
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
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())

    project: Mapped[Project] = relationship(back_populates="quality_gate_config")


class PageStructureCache(Base):
    """页面结构缓存（设计文档7.3节）：存储URL模式对应的DOM区域结构及哈希，用于测试执行时的上下文注入。"""
    __tablename__ = "page_structure_caches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"))
    base_url: Mapped[str | None] = mapped_column(String(300))  # PC 端基础地址（录制/探索时所选）
    url_pattern: Mapped[str] = mapped_column(String(500), nullable=False)  # 页面路径 e.g. /admin/users/create
    page_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(500))  # 人工填写的描述（探索时填过则有，否则空）
    dom_hash: Mapped[dict | None] = mapped_column(JSONB)  # {region_name: hash_string}
    regions: Mapped[list | None] = mapped_column(JSONB)   # [{name, selector, elements:[{name,selector,type}]}]
    captured_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())
    last_hit_at: Mapped[datetime | None] = mapped_column(DateTime)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/stale/needs_update

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
    """平台用户表：存储外部 SSO 或本地创建的用户及其平台角色。"""
    __tablename__ = "platform_users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    # 外部 SSO 用户 id；本地账号用户可为空
    external_user_id: Mapped[str | None] = mapped_column(String(200), unique=True, nullable=True, index=True)
    email: Mapped[str | None] = mapped_column(String(200))
    name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="user")  # admin / user
    # 本地账号密码登录（未配置外部 SSO 时使用）
    username: Mapped[str | None] = mapped_column(String(100), unique=True, nullable=True, index=True)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)  # False = 已禁用
    auth_source: Mapped[str] = mapped_column(String(20), default="external")  # external / local
    # 该用户专属的 AI 中转 key：所有 AI 操作(分析/生成/执行/App)走发起人自己的 key；
    # 未配置则发起 AI 操作时报错"未分配key"，由管理员在用户管理里配置。
    ai_api_key: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=func.now(), onupdate=func.now())


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
    """通用键值配置（仅管理员可改），承载 SSO 和 AI 等运行配置。"""
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
