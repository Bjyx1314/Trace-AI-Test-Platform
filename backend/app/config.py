from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    anthropic_api_key: Optional[str] = None

    # ── AI 调用层（多 provider 可配置）────────────────────────────
    ai_provider: str = "anthropic"        # anthropic | openai | claude_cli(订阅方式)
    ai_api_key: Optional[str] = None      # 通用 Key；为空时回退 anthropic_api_key（claude_cli 不需要）
    ai_base_url: Optional[str] = None      # 自定义/中转地址（anthropic、openai 适用）
    ai_model: Optional[str] = None         # 模型名；开源版不内置默认模型，使用 AI 前必须显式配置
    claude_cli_path: str = "claude"        # 订阅方式所用的 claude CLI 路径
    adb_path: Optional[str] = None         # 移动端真机直连探测用 adb 路径；留空自动探测 PATH/常见SDK位置
    feishu_webhook_url: Optional[str] = None
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_bitable_app_token: Optional[str] = None
    feishu_requirements_table_id: Optional[str] = None
    feishu_defects_table_id: Optional[str] = None
    feishu_project_plugin_id: Optional[str] = None
    feishu_project_plugin_secret: Optional[str] = None
    feishu_project_user_key: Optional[str] = None
    database_url: str = "postgresql+asyncpg://testplatform:testplatform_dev_pwd@localhost:5432/test_platform"
    mock_mode: bool = False
    # 是否允许"失败回退 mock / 占位 mock 数据"——仅本地开发用；服务器真实环境必须为 False(报错而非 mock)
    allow_mock: bool = False
    jwt_secret: str = "test-platform-jwt-secret-change-in-prod"
    external_task_api_url: str = "http://sso.example.test"  # external task system(SSO IdP)默认地址；可被后台配置或 env 覆盖
    external_task_api_key: Optional[str] = None  # external task system 对外 API Key(abk_...)，部门服务调用用

    # 本地账号：首次启动若无本地管理员则自动创建（external task system 不可用时用于登录）
    default_admin_username: str = "admin"
    default_admin_password: str = "admin"

    # ── 执行引擎（详设第 0/6 章）──────────────────────────────
    execution_mode: str = "mock"          # mock | real（real 时未就绪的 Runner 自动回退 mock）
    execution_inproc: bool = False        # real 模式下无 RQ/Redis 时在进程内直接执行(本地调试用；服务器用队列)
    runner_api_enabled: bool = True       # 接口 Runner（无环境依赖，默认开）
    runner_web_enabled: bool = False      # PC web（Playwright）
    # PC web 执行登录态目录：每个端一个 Playwright storageState JSON（文件名=端名，如「demo-web.json」）。
    # 用现有登录脚本跑一次导出（context.storage_state(path=...)），执行时按用例 platforms 注入→浏览器直接已登录。
    web_login_state_dir: str = "./login_states"
    runner_android_enabled: bool = False
    runner_ios_enabled: bool = False
    runner_harmony_enabled: bool = False
    runner_miniprogram_enabled: bool = False
    task_queue_url: str = "redis://localhost:6379/0"  # RQ 任务队列
    run_callback_base: str = "http://localhost:8000"  # 执行机回填结果时回调后端的地址

    # ── App 真机执行机 worker（详见 deploy/worker）──────────────
    worker_token: Optional[str] = None     # 执行机 worker 调平台 /api/worker/* 的共享令牌；为空则不鉴权(仅本地)
    app_job_timeout_sec: int = 1800        # 单条 App 任务等待 worker 执行的超时（秒）
    worker_exe_path: str = "/app/worker-dist/tp-worker.exe"  # Windows 版 tp-worker.exe 路径（服务器放置/挂载）
    worker_exe_path_mac: str = "/app/worker-dist/tp-worker"  # macOS 版 tp-worker 原生二进制路径（服务器放置/挂载）

    # ── Sonic 云真机（远程真机执行；详见 services/sonic_client.py）──────────────
    sonic_enabled: bool = False            # 开启后执行弹框可选「远程真机(Sonic)」，无本地真机也能跑 App
    sonic_base_url: Optional[str] = None    # Sonic 网关地址，含前缀，如 http://host:3000/api/controller
    sonic_username: Optional[str] = None    # 账号密码登录换 token（推荐）
    sonic_password: Optional[str] = None
    sonic_token: Optional[str] = None       # 或直接配长效 token(generateToken 生成)，配了则优先用，免登录
    sonic_sas_port_min: int = 30000         # 占用设备时开的远程 adb(SAS)端口范围，backend 主机需能连到 agent 该端口
    sonic_sas_port_max: int = 30100

    # 视觉动作循环(App/PC 逐步点击)给推理模型的推理档：low/minimal 大幅省 token 且对"看图输出点击"更稳；
    # 留空则不下发 reasoning 参数(兜底：万一中转网关不认该参数，设为空即回退默认行为)。
    vision_reasoning_effort: str = "low"

    # ── Jenkins 测试包（换测试包的「包版本」下拉数据源；详见 services/app_packages.py）──
    jenkins_build_api: Optional[str] = None   # 构建记录接口，如 http://ci.example.test/extend/jenkins/buildRecord/list；为空则「更换测试包」无可选版本
    jenkins_project_prefix: str = ""      # 查询时 projectName 前缀（产品名前拼此前缀）

    # ── 任务超时兜底（仅防“真挂起”导致状态永久卡在“进行中”而无法重试）──
    # 注意：这是“卡死”的最后兜底，不是正常时长限制；务必设得远大于最长的真实分析/执行耗时，
    # 正常(哪怕特别慢的)分析会在此之前完成，不受影响。只有 socket 永久阻塞/进程卡死才会命中。
    ai_call_timeout_sec: int = 1800        # 单次 AI 调用（需求分析/用例生成）超时，默认 30 分钟，可用 .env 调
    case_exec_timeout_sec: int = 1800      # 单条用例真实执行超时，默认 30 分钟，可用 .env 调

    # ── 框架集成（框架仓库绑定 + 索引驱动生成 + 仓库内执行）──────
    framework_workspace: str = "./framework_repos"  # 框架仓库本地 checkout 根目录

    # ── AI 质量闭环：代码影响分析（platform mode 直跑 skill）──────
    business_repo_workspace: str = "./business_repos"  # 被测业务仓库 checkout 根目录
    skills_dir: str = "../deploy/skills"               # vendored skill 目录（含 code-change-test-impact / code-graph-scan）
    code_impact_timeout_sec: int = 900                 # skill 分析超时（方案 8.2：15min，超时不阻塞）
    code_graph_timeout_sec: int = 1800                 # 全量图谱扫描超时（比增量分析长）
    # 变更分析：静态扫描为主、LLM 只吃结构化摘要+受限 diff（req 2/3/7）。
    code_impact_max_files: int = 40                    # 送入 LLM 的最多变更文件数（超出按风险截断，只留高风险核心文件）
    code_impact_max_diff_bytes: int = 60000            # 送入 LLM 的 diff 字节上限（大变更限流，防 token 失控）
    code_impact_skill_budget_chars: int = 9000         # 装入 prompt 的 skill 方法论字符预算（截断，避免全量文档吃 token）

    # ── Guardian 集成（合入态代码图谱的确定性影响半径；默认关，降级安全）──────
    # 后台「系统设置」可覆盖这些默认值(app_settings)。不可达/未接入/超时一律降级为无，不阻塞影响分析。
    guardian_enabled: bool = False                     # 总开关：关时完全不调用 Guardian
    guardian_base_url: Optional[str] = None            # envoy MCP HTTP 端点，如 http://guardian.example.test
    guardian_pat: Optional[str] = None                 # 平台专用 PAT（Bearer）
    guardian_product: str = "guardian"                 # 目标 product（业务仓接入后改；当前 envoy 侧固定 dogfood=guardian）
    guardian_timeout_sec: int = 15                     # 单次 MCP 调用超时
    guardian_max_impact_files: int = 20                # 单次影响分析最多向 Guardian 查询的变更文件数

    # ── AI 质量闭环：向量嵌入（经验召回/覆盖项归并语义通道）──────
    # embedding 独立于 chat provider（anthropic/claude_cli 无 embeddings API），走 OpenAI 兼容端点。
    # 缺 key 时 services/embedding.py 返回 None，召回/归并自动降级为标签精确+结构键匹配，不阻塞。
    embed_enabled: bool = True
    embed_model: str = "text-embedding-3-small"
    embed_dim: int = 1536
    embed_api_key: Optional[str] = None   # 为空回退 ai_api_key / anthropic_api_key
    embed_base_url: Optional[str] = None  # 为空回退 ai_base_url / https://api.openai.com/v1

    @property
    def mock_allowed(self) -> bool:
        """是否允许"失败回退/占位假数据"mock。

        硬约束（生产永不 mock）：execution_mode=real 时一律返回 False，与 allow_mock 无关——
        生产即 real 模式，执行/AI/飞书等任何环节都不会回退假数据，端环境未就绪只产出真实
        env_error 报错。即使误把 ALLOW_MOCK 设成 true 也不会破防。
        仅当 execution_mode≠real（本地调试）且显式 ALLOW_MOCK=true 时才允许 mock 数据。
        与 mock_mode 解耦——mock_mode 仅控制本地免 SSO 登录便利，不连带开启数据 mock。"""
        if self.execution_mode == "real":
            return False
        return self.allow_mock

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()
