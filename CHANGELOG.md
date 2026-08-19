# Changelog

## 2026-08-19

### 中文

- 发布最新开源版本，补充正式的产品定位、功能说明和本次开源更新说明。
- 同步质量闭环能力，包括覆盖项、质量规则、经验沉淀、代码影响分析、数据准备、页面缓存和执行证据。
- 增强接口、Web、App 多执行端路由与执行体验，减少对私有端名、私有环境和历史硬编码的依赖。
- 泛化外部任务系统、接口登录、Web 登录和自动化框架配置，确保开源仓库不携带私有业务接入信息。
- 完成发布前隐私复查，确认仓库不包含真实账号、密钥、登录态、上传文件、构建产物、安装包或私有业务数据。

### English

- Published the latest open-source update with formal product positioning, feature overview, and release notes.
- Synced quality-loop capabilities including covered items, quality rules, experience mining, code impact analysis, test data preparation, page cache, and execution evidence.
- Improved API, Web, and App execution routing while reducing reliance on private platform names, private environments, and historical hard-coded mappings.
- Generalized external task system, interface login, Web login, and automation framework configuration for public repository use.
- Completed a pre-release privacy review and confirmed that the repository does not include real accounts, secrets, login states, uploads, build artifacts, app packages, or private business data.

## 2026-07-03

- 接口真实执行支持按 `tags.api_spec.service` / `base_url` 解析环境地址，减少手工维护接口网关地址。
- 执行路由支持按平台枚举 `parent_key` 判端，并拦截 App 用例误分到 Web 的错误兜底执行。
- Web AI 执行器支持点击后自动接管新开的标签页/窗口，适配 工作台 等新开页场景。
- 临时账号登录补充环境地址透传，框架未覆盖端可降级走通用账密登录。
- 新增执行路由与 Web 新开页相关回归测试，并补充接口环境解析模块。
- 通用化外部任务系统集成，清理业务示例与本机路径，并增强 Git 元数据审计。
- 移除代码中的默认 AI 模型，模型改为显式必填配置。
- 批量同步需求改用通用项目拉取说明，移除页面中的特定外部系统名称。
- 空数据库启动时自动创建不含业务数据的“示例项目”，本地与容器启动行为保持一致。
- 开源版首次启动默认创建 `admin` / `admin` 管理员，并补充生产改密提示。
- 同步开源基线功能并完成脱敏审计。
- 重设计枚举管理页面，支持启用/停用和操作人审计。
- PC 地址支持在同一弹窗编辑 SIT 与开发环境。
- App 端新增应用包名配置，执行前可直接启动目标应用。
- 修复执行弹窗中的设备和安装包下拉层级问题。
- 再次执行开源脱敏、后端测试和前端生产构建。

## 2026-07-02

- 创建首个脱敏开源发行版。
- 添加 Apache-2.0 许可证、贡献指南、安全政策、部署文档和 CI。
