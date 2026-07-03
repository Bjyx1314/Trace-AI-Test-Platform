from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    anthropic_api_key: Optional[str] = None

    # ── AI 调用层（多 provider 可配置）────────────────────────────
    ai_provider: str = "anthropic"        # anthropic | openai | claude_cli(订阅方式)
    ai_api_key: Optional[str] = None      # 通用 Key；为空时回退 anthropic_api_key（claude_cli 不需要）
    ai_base_url: Optional[str] = None      # 自定义/中转地址（anthropic、openai 适用）
    ai_model: Optional[str] = None         # 模型名；无内置默认值，使用 AI 前必须显式配置
    claude_cli_path: str = "claude"        # 订阅方式所用的 claude CLI 路径
    adb_path: Optional[str] = None         # 移动端真机直连探测用 adb 路径；留空自动探测 PATH/常见SDK位置
    feishu_webhook_url: Optional[str] = None
    feishu_app_id: Optional[str] = None
    feishu_app_secret: Optional[str] = None
    feishu_bitable_app_token: Optional[str] = None
    feishu_requirements_table_id: Optional[str] = None
    feishu_defects_table_id: Optional[str] = None
    database_url: str = "postgresql+asyncpg://testplatform:testplatform@localhost:5432/test_platform"
    mock_mode: bool = False
    # 是否允许"失败回退 mock / 占位 mock 数据"——仅本地开发用；服务器真实环境必须为 False(报错而非 mock)
    allow_mock: bool = False
    jwt_secret: str = "change-this-secret-before-production"
    external_task_api_url: Optional[str] = None  # 可选的外部 SSO/任务系统地址
    external_task_api_key: Optional[str] = None  # 可选外部任务系统 API Key

    # 本地账号：首次启动自动创建；非本地环境必须覆盖默认密码。
    default_admin_username: str = "admin"
    default_admin_password: str = "admin"

    # ── 执行引擎（详设第 0/6 章）──────────────────────────────
    execution_mode: str = "mock"          # mock | real（real 时未就绪的 Runner 自动回退 mock）
    execution_inproc: bool = False        # real 模式下无 RQ/Redis 时在进程内直接执行(本地调试用；服务器用队列)
    runner_api_enabled: bool = True       # 接口 Runner（无环境依赖，默认开）
    runner_web_enabled: bool = False      # PC web（Playwright）
    # PC web 执行登录态目录：每个端一个 Playwright storageState JSON（文件名=端名）。
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

    # ── 任务超时兜底（仅防“真挂起”导致状态永久卡在“进行中”而无法重试）──
    # 注意：这是“卡死”的最后兜底，不是正常时长限制；务必设得远大于最长的真实分析/执行耗时，
    # 正常(哪怕特别慢的)分析会在此之前完成，不受影响。只有 socket 永久阻塞/进程卡死才会命中。
    ai_call_timeout_sec: int = 1800        # 单次 AI 调用（需求分析/用例生成）超时，默认 30 分钟，可用 .env 调
    case_exec_timeout_sec: int = 1800      # 单条用例真实执行超时，默认 30 分钟，可用 .env 调

    # ── 框架集成（框架仓库绑定 + 索引驱动生成 + 仓库内执行）──────
    framework_workspace: str = "./framework_repos"  # 框架仓库本地 checkout 根目录

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
