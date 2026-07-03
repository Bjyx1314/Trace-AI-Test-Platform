import api from './client'
import type {
  Project,
  Requirement,
  TestCase,
  TestCaseLog,
  TestResult,
  Execution,
  Defect,
  QualityGateConfig,
  EnumDefinition,
  PageStructureCache,
  DashboardSummary,
  DashboardBreakdown,
  GateResult,
  RequirementsQualityResponse,
  RequirementsGateResponse,
  PipelineStatus,
} from '../types/api'

// ─── Projects ────────────────────────────────────────────────────────────────
type ProjectCreateInput = Pick<Project, 'name'> &
  Partial<Pick<Project, 'description' | 'product_line' | 'case_id_prefix' | 'feishu_webhook' | 'feishu_doc_url' | 'ci_gate_enabled' | 'pass_rate_threshold'>>

export const projectsApi = {
  list: () => api.get<Project[]>('/projects'),
  get: (id: string) => api.get<Project>(`/projects/${id}`),
  create: (data: ProjectCreateInput) => api.post<Project>('/projects', data),
  update: (id: string, data: Partial<ProjectCreateInput>) => api.put<Project>(`/projects/${id}`, data),
  delete: (id: string) => api.delete<void>(`/projects/${id}`),
}

// ─── Requirements ─────────────────────────────────────────────────────────────
type RequirementCreateInput = {
  project_id: string
  title: string
  content: string
  product_line?: string | null
  source?: string
}

type ConfirmationPointUpdateInput = {
  confirmation?: string
  no_confirmation_needed?: boolean
}

export const requirementsApi = {
  list: (params?: { project_id?: string; iteration?: string; owner?: string }) =>
    api.get<Requirement[]>('/requirements', { params }),
  get: (id: string) => api.get<Requirement>(`/requirements/${id}`),
  create: (data: RequirementCreateInput) => api.post<Requirement>('/requirements', data),
  delete: (id: string) => api.delete<void>(`/requirements/${id}`),
  syncFeishuLink: (projectId: string, link: string) =>
    api.post<Requirement>('/requirements/sync-feishu-link', { link }, { params: { project_id: projectId } }),
  update: (id: string, data: Partial<RequirementCreateInput> & { analysis_confirmation?: string | null }) =>
    api.patch<Requirement>(`/requirements/${id}`, data),
  complete: (id: string) => api.post<Requirement>(`/requirements/${id}/complete`),
  coverage: (id: string) => api.get<{ coverage_percent: number; total_points?: number; covered_points?: string[]; uncovered_points: string[]; case_count: number; scoped?: boolean }>(`/requirements/${id}/coverage`, { timeout: 600000 }),
  updateConfirmationPoint: (reqId: string, pointId: string, data: ConfirmationPointUpdateInput, sliceId?: string) =>
    api.patch<Requirement>(`/requirements/${reqId}/confirmation-points/${pointId}`, data, { params: sliceId ? { slice_id: sliceId } : undefined }),
  batchNoConfirm: (reqId: string, pointIds: string[], sliceId?: string) =>
    api.post<Requirement>(`/requirements/${reqId}/confirmation-points/batch-no-confirm`, { point_ids: pointIds }, { params: sliceId ? { slice_id: sliceId } : undefined }),
  getAttachmentUrl: (id: string) => `/api/requirements/attachment/${id}`,
  upload: (projectId: string, file: File, productLine?: string) => {
    const formData = new FormData()
    formData.append('project_id', projectId)
    formData.append('file', file)
    if (productLine) formData.append('product_line', productLine)
    return api.post<Requirement>('/requirements/upload', formData, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
  syncFeishu: (projectId: string) =>
    api.post<Requirement[]>('/requirements/sync-feishu', null, { params: { project_id: projectId } }),
  externalProjects: () =>
    api.get<{ id: string; name: string; status?: string }[]>('/requirements/external-system/projects'),
  syncExternal: (projectId: string, externalProjectId?: string) =>
    api.post<Requirement[]>('/requirements/sync-external', null, { params: { project_id: projectId, external_project_id: externalProjectId } }),
}

// ─── Test Cases ───────────────────────────────────────────────────────────────
type TestCaseCreateInput = Pick<TestCase, 'project_id' | 'title' | 'priority' | 'case_type'> &
  Partial<Pick<TestCase, 'requirement_id' | 'modules' | 'platforms' | 'preconditions' | 'steps' | 'expected_result'>>

export const testCasesApi = {
  list: (params?: { project_id?: string; requirement_id?: string; priority?: string; library_only?: boolean }) =>
    api.get<TestCase[]>('/testcases', { params }),
  get: (id: string) => api.get<TestCase>(`/testcases/${id}`),
  create: (data: TestCaseCreateInput) => api.post<TestCase>('/testcases', data),
  update: (id: string, data: Partial<TestCaseCreateInput>) => api.put<TestCase>(`/testcases/${id}`, data),
  delete: (id: string) => api.delete<void>(`/testcases/${id}`),
  trash: (params?: { project_id?: string }) =>
    api.get<TestCase[]>('/testcases/trash', { params }),
  restore: (id: string) => api.post<TestCase>(`/testcases/${id}/restore`),
  purge: (id: string) => api.delete<void>(`/testcases/${id}/purge`),
  manualPass: (id: string) => api.post<TestCase>(`/testcases/${id}/manual-pass`),
  manualFail: (id: string) => api.post<TestCase>(`/testcases/${id}/manual-fail`),
  review: (id: string, action: string) => api.post<TestCase>(`/testcases/${id}/review`, { action }),
  batchReview: (caseIds: string[], action: string) =>
    api.post<{ status: string; count: number }>('/testcases/batch-review', { case_ids: caseIds, action }),
  results: (id: string) => api.get<TestResult[]>(`/testcases/${id}/results`),
  logs: (id: string) => api.get<TestCaseLog[]>(`/testcases/${id}/logs`),
  exportUrl: (format: 'md' | 'xlsx', params: { projectId?: string; requirementId?: string; ids?: string[] }) => {
    const qs = new URLSearchParams({ format })
    if (params.ids && params.ids.length) qs.set('ids', params.ids.join(','))
    else if (params.projectId) qs.set('project_id', params.projectId)
    if (params.requirementId) qs.set('requirement_id', params.requirementId)
    return `/api/testcases/export?${qs.toString()}`
  },
  importCases: (file: File, projectId: string, requirementId?: string) => {
    const fd = new FormData()
    fd.append('file', file)
    fd.append('project_id', projectId)
    if (requirementId) fd.append('requirement_id', requirementId)
    return api.post<{ status: string; created: number; titles: string[] }>('/testcases/import', fd, {
      headers: { 'Content-Type': 'multipart/form-data' },
    })
  },
}

// ─── Executions ───────────────────────────────────────────────────────────────
type AccountOverride = { role?: string; username?: string; password?: string; tenant_name?: string }
type ExecutionCreateInput = {
  project_id: string
  name: string
  trigger?: string
  case_ids?: string[]
  requirement_id?: string
  run_mode?: string
  account_overrides?: Record<string, AccountOverride>
  reorder?: boolean
  target_device?: string | null   // App 指定真机 serial；不传走兜底默认设备
  env?: string                     // PC/Web 执行环境 sit(默认)/dev；缺该环境地址的端回退 SIT
  package_overrides?: Record<string, string>  // App 换测试包：{app端名: 包版本id}；执行前卸旧装新
}

export type RequirementExecutionOverview = {
  requirement_id: string
  title: string
  product_line: string | null
  status: string
  case_count: number
  execution_count: number
  last_execution: {
    execution_id: string
    created_at: string | null
    status: string
    pass_rate: number
    passed: number
    failed: number
    skipped: number
    total: number
    ci_gate_result: { releasable: boolean; blocking_reasons?: unknown[] } | null
  } | null
}

export const executionsApi = {
  list: (projectId?: string, requirementId?: string) =>
    api.get<Execution[]>('/executions', { params: { project_id: projectId, requirement_id: requirementId } }),
  requirementOverview: (projectId?: string) =>
    api.get<RequirementExecutionOverview[]>('/executions/requirement-overview', { params: { project_id: projectId } }),
  get: (id: string) => api.get<Execution>(`/executions/${id}`),
  devices: () => api.get<{ adb_available: boolean; devices: { serial: string; model: string; status?: string; worker_name?: string; is_shared?: boolean; is_public?: boolean; busy?: boolean; owner_user_id?: string | null }[]; sonic_devices?: { serial: string; model: string; busy?: boolean; occupied_by?: string | null }[]; sonic_enabled?: boolean; sonic_error?: string | null; app_queue?: number; error: string | null }>('/executions/devices'),
  webAccounts: (platforms: string[]) =>
    api.get<Record<string, { covered: boolean; accounts: { role: string; label: string }[] }>>(
      '/executions/web-accounts', { params: { platforms: platforms.join(',') } }),
  create: (data: ExecutionCreateInput) => api.post<Execution>('/executions', data),
  // App「更换测试包」下拉数据源：某 app 端可选的包版本（真实接口待接入，后端现返回内置测试项）
  appPackages: (app: string) =>
    api.get<{ app: string; packages: { id: string; label: string; version?: string }[] }>(
      '/executions/app-packages', { params: { app } }),
  results: (id: string) => api.get<TestResult[]>(`/executions/${id}/results`),
  updateDefect: (resultId: string, defect_status: string) =>
    api.patch<TestResult>(`/executions/results/${resultId}/defect`, { defect_status }),
}

// ─── App 真机执行机 worker（连接我的真机）──────────────────────────────────
export const workerApi = {
  installInfo: (os?: string) => api.get<{ exe_available: boolean; win_available: boolean; mac_available: boolean; worker_token: string; owner_user_id: string }>('/worker/install-info', { params: os ? { os } : undefined }),
  downloadUrl: (os?: string) => `/api/worker/download${os ? `?os=${os}` : ''}`,
}

// ─── Pipeline ─────────────────────────────────────────────────────────────────
export const pipelineApi = {
  analyze: (requirement_id: string, scope_text?: string, scope_image_tokens?: string[], slice_id?: string, mode?: string) =>
    api.post<PipelineStatus>('/pipeline/analyze', { requirement_id, scope_text, scope_image_tokens, slice_id, mode }),
  generateCases: (requirement_id: string, regenerate = false, scope_text?: string, scope_image_tokens?: string[], slice_id?: string, mode?: string) =>
    api.post<PipelineStatus>('/pipeline/generate-cases', { requirement_id, regenerate, scope_text, scope_image_tokens, slice_id, mode }),
  status: (requirementId: string, sliceId?: string) =>
    api.get<PipelineStatus>(`/pipeline/status/${requirementId}`, { params: sliceId ? { slice_id: sliceId } : undefined }),
  confirmPlatforms: (requirement_id: string, platforms: string[], slice_id?: string) =>
    api.post<{ status: string; platforms: string[] }>('/pipeline/confirm-platforms', { requirement_id, platforms, slice_id }),
}

// ─── Requirement Slices（需求切片：多人多范围）──────────────────────────────────
export type RequirementSliceT = {
  id: string
  requirement_id: string
  owner_name?: string | null
  scope_label: string
  scope_text?: string | null
  scope_image_tokens?: string[] | null
  analysis_result?: any | null
  analysis_confirmation?: string | null
  status: string
  is_default: boolean
  has_pending?: boolean
  appended?: boolean
  created_at: string
  updated_at: string
}
export const slicesApi = {
  list: (reqId: string) => api.get<RequirementSliceT[]>(`/requirements/${reqId}/slices`),
  create: (reqId: string, data: { scope_label?: string; scope_text?: string; scope_image_tokens?: string[]; owner_name?: string }) =>
    api.post<RequirementSliceT>(`/requirements/${reqId}/slices`, data),
  update: (sliceId: string, data: { scope_label?: string; scope_text?: string; scope_image_tokens?: string[]; owner_name?: string }) =>
    api.patch<RequirementSliceT>(`/requirements/slices/${sliceId}`, data),
  remove: (sliceId: string) => api.delete<{ status: string; unlinked_cases: number }>(`/requirements/slices/${sliceId}`),
}

// ─── Dashboard ────────────────────────────────────────────────────────────────
export const dashboardApi = {
  summary: (projectId?: string) =>
    api.get<DashboardSummary>('/dashboard/summary', { params: { project_id: projectId } }),
  qualityGate: (projectId: string) =>
    api.get<GateResult>('/dashboard/quality-gate', { params: { project_id: projectId } }),
  breakdown: (projectId?: string) =>
    api.get<DashboardBreakdown>('/dashboard/breakdown', { params: { project_id: projectId } }),
  requirementsQuality: (params?: { project_id?: string; iteration?: string; status?: string; platform?: string; owner?: string }) =>
    api.get<RequirementsQualityResponse>('/dashboard/requirements-quality', { params }),
  requirementsGate: (requirement_ids: string[]) =>
    api.post<RequirementsGateResponse>('/dashboard/requirements-gate', { requirement_ids }),
}

// ─── Page Structure Cache ─────────────────────────────────────────────────────
type PageCacheCreateInput = {
  project_id: string
  url_pattern: string
  page_name: string
  status?: 'active' | 'stale' | 'needs_update'
  regions?: unknown
}

type ExplorePathItem = { path: string; description?: string }

type PageExploreInput = {
  project_id: string
  base_url: string
  paths: ExplorePathItem[]
  overwrite?: boolean
}

type ExploreResult = {
  base_url: string
  explored_count: number
  created_count: number
  updated_count: number
  existing_paths: { path: string; url_pattern: string; page_name: string }[]
  entries: PageStructureCache[]
}

type PageRecordInput = {
  project_id: string
  base_url: string
  start_path?: string
  overwrite?: boolean
}

type RecordResult = {
  base_url: string
  recorded_count: number
  created_count: number
  updated_count: number
  existing_paths: { url_pattern: string; page_name: string }[]
  entries: PageStructureCache[]
}

export const pageCacheApi = {
  list: (projectId?: string) =>
    api.get<PageStructureCache[]>('/page-cache', { params: { project_id: projectId } }),
  get: (id: string) => api.get<PageStructureCache>(`/page-cache/${id}`),
  create: (data: PageCacheCreateInput) => api.post<PageStructureCache>('/page-cache', data),
  update: (id: string, data: Partial<PageCacheCreateInput>) =>
    api.put<PageStructureCache>(`/page-cache/${id}`, data),
  delete: (id: string) => api.delete<void>(`/page-cache/${id}`),
  invalidate: (id: string) => api.post<PageStructureCache>(`/page-cache/${id}/invalidate`),
  // 探索/录制是耗时的有头浏览器+AI 操作，远超默认 30s：关掉超时，避免前端误报"失败"而后端仍在跑并最终写库
  explore: (data: PageExploreInput) => api.post<ExploreResult>('/page-cache/explore', data, { timeout: 0 }),
  recorderStatus: () => api.get<{ available: boolean; cli_path: string | null }>('/page-cache/recorder/status'),
  record: (data: PageRecordInput) => api.post<RecordResult>('/page-cache/record', data, { timeout: 0 }),
}

// ─── Enums ────────────────────────────────────────────────────────────────────
type EnumCreateInput = Pick<EnumDefinition, 'category' | 'key' | 'label'> &
  Partial<Pick<EnumDefinition, 'parent_key' | 'sort_order' | 'is_active'>>

export const enumsApi = {
  list: (category?: string) => api.get<EnumDefinition[]>('/enums', { params: { category } }),
  create: (data: EnumCreateInput) => api.post<EnumDefinition>('/enums', data),
  update: (id: string, data: Partial<EnumCreateInput>) => api.put<EnumDefinition>(`/enums/${id}`, data),
  delete: (id: string) => api.delete<void>(`/enums/${id}`),
  logs: (category: string) => api.get<any[]>('/enums/logs', { params: { category } }),
  urlMatrix: () => api.get<UrlMatrix>('/enums/url-matrix'),
}

// PC 端地址矩阵：行=端，列=环境，单元格=已配地址(含 enum id)或 null
export type UrlMatrixEnv = { key: string; label: string; category: string }
export type UrlMatrixCell = { id: string; url: string } | null
export type UrlMatrix = {
  envs: UrlMatrixEnv[]
  platforms: { key: string; label: string; urls: Record<string, UrlMatrixCell> }[]
}

// ─── Defects ──────────────────────────────────────────────────────────────────
export const defectsApi = {
  list: (params?: { project_id?: string; requirement_id?: string; status?: string; severity?: string }) =>
    api.get<Defect[]>('/defects', { params }),
  get: (id: string) => api.get<Defect>(`/defects/${id}`),
  update: (id: string, data: { status?: string; severity?: string; duplicate_of_defect_id?: string; title?: string; draft_ticket?: Record<string, any> }) =>
    api.patch<Defect>(`/defects/${id}`, data),
}

// ─── Framework Repos（框架仓库集成）────────────────────────────────────────────
export type FrameworkRepo = {
  id: string
  name: string
  repo_type: 'interface' | 'web' | 'app'
  project_id: string | null
  description: string | null
  git_url: string
  branch: string
  local_path: string | null
  tests_root: string | null
  data_root: string | null
  keyword_root: string | null
  run_command: string | null
  install_command: string | null
  env_json: Record<string, unknown> | null
  index_status: 'pending' | 'indexing' | 'ready' | 'failed'
  index_commit: string | null
  indexed_at: string | null
  index_summary: Record<string, number | undefined>
  enabled: boolean
  created_at: string | null
  index_json?: Record<string, unknown>
}

type FrameworkRepoCreateInput = Pick<FrameworkRepo, 'name' | 'repo_type' | 'git_url'> &
  Partial<Pick<FrameworkRepo, 'branch' | 'project_id' | 'description' | 'local_path' |
    'tests_root' | 'data_root' | 'keyword_root' | 'run_command' | 'install_command' | 'env_json'>>

export const frameworksApi = {
  list: (params?: { project_id?: string; repo_type?: string }) =>
    api.get<FrameworkRepo[]>('/frameworks', { params }),
  get: (id: string, withIndex = false) =>
    api.get<FrameworkRepo>(`/frameworks/${id}`, { params: { with_index: withIndex } }),
  create: (data: FrameworkRepoCreateInput) => api.post<FrameworkRepo>('/frameworks', data),
  update: (id: string, data: Partial<FrameworkRepoCreateInput> & { enabled?: boolean }) =>
    api.patch<FrameworkRepo>(`/frameworks/${id}`, data),
  delete: (id: string) => api.delete<void>(`/frameworks/${id}`),
  reindex: (id: string, sync_git = true) =>
    api.post<FrameworkRepo>(`/frameworks/${id}/reindex`, { sync_git }),
  generateCase: (caseId: string) =>
    api.post<{ case_id: string; script_path: string; framework_repo_id: string; generated_artifacts: any }>(
      `/frameworks/cases/${caseId}/generate`),
  reviewCase: (caseId: string) =>
    api.post<{ ok: boolean; issues: string[]; warnings: string[] }>(`/frameworks/cases/${caseId}/review`),
  commitCase: (caseId: string, push = false) =>
    api.post<{ branch: string; commit: string; files: string[]; pushed: boolean }>(
      `/frameworks/cases/${caseId}/commit`, { push }),
}

// ─── Auth ─────────────────────────────────────────────────────────────────────
export const authApi = {
  verify: (token: string) => api.post<{ jwt: string; user: any }>('/auth/verify', { token }),
  login: (username: string, password: string) =>
    api.post<{ jwt: string; user: any }>('/auth/login', { username, password }),
  me: () => api.get<any>('/auth/me'),
  // 公开读取外部 SSO 地址，供未登录页面跳转换票。
  ssoConfig: () => api.get<{ external_sso_url: string }>('/auth/sso-config'),
}

// ─── Users ────────────────────────────────────────────────────────────────────
export const usersApi = {
  list: () => api.get<any[]>('/users'),
  updateRole: (userId: string, role: string) => api.patch(`/users/${userId}/role`, { role }),
  create: (data: { username: string; password: string; name?: string; role?: string }) =>
    api.post('/users', data),
  setActive: (userId: string, is_active: boolean) =>
    api.patch(`/users/${userId}/active`, { is_active }),
  setAiKey: (userId: string, ai_api_key: string | null) =>
    api.patch(`/users/${userId}/ai-key`, { ai_api_key }),
}

// ─── System Settings ──────────────────────────────────────────────────────────
export type AutomationSwitch = {
  platform: string   // api / web / app / harmony / miniprogram
  label: string
  enabled: boolean
  updated_by: string | null
  updated_at: string | null
}

export type SsoConfig = { external_sso_url: string; resolved: string; default: string }
export type AiConfig = {
  provider: string; model: string; base_url: string
  api_key_set: boolean; api_key_masked: string
  providers: { value: string; label: string }[]
}

export const systemApi = {
  automationSwitches: () => api.get<AutomationSwitch[]>('/system/automation-switches'),
  setAutomationSwitch: (platform: string, enabled: boolean) =>
    api.put<AutomationSwitch>('/system/automation-switches', { platform, enabled }),
  getSsoConfig: () => api.get<SsoConfig>('/system/sso-config'),
  setSsoConfig: (external_sso_url: string) =>
    api.put<SsoConfig>('/system/sso-config', { external_sso_url }),
  getAiConfig: () => api.get<AiConfig>('/system/ai-config'),
  setAiConfig: (data: { provider: string; model: string; base_url: string; api_key?: string }) =>
    api.put<AiConfig>('/system/ai-config', data),
}

// ─── Quality Gate Config ──────────────────────────────────────────────────────
type QualityGateConfigUpdateInput = Partial<Pick<QualityGateConfig,
  'overall_pass_rate_threshold' | 'enable_overall_pass_rate_gate' |
  'p1_failure_threshold' | 'enable_p1_failure_gate' |
  'pass_rate_wow_drop_threshold' | 'coverage_threshold'>>

export const qualityGateConfigApi = {
  get: (projectId: string) => api.get<QualityGateConfig>(`/projects/${projectId}/quality-gate-config`),
  update: (projectId: string, data: QualityGateConfigUpdateInput) =>
    api.put<QualityGateConfig>(`/projects/${projectId}/quality-gate-config`, data),
}
