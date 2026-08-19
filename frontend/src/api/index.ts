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
  CoveredItem,
  ChangeImpactRecord,
  BusinessRepo,
  CoverageMatrixResponse,
  Experience,
  ExperienceRecallResponse,
  GraphNode,
  GraphExpandResponse,
} from '../types/api'

// ─── Projects ────────────────────────────────────────────────────────────────
type ProjectCreateInput = Pick<Project, 'name'> &
  Partial<Pick<Project, 'description' | 'product_line' | 'case_id_prefix' | 'feishu_webhook' | 'feishu_doc_url' |
    'feishu_project_space_id' | 'feishu_project_rootcause_field' | 'feishu_project_defect_filter' |
    'ci_gate_enabled' | 'pass_rate_threshold'>>

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
  agentBoardProjects: () =>
    api.get<{ id: string; name: string; status?: string }[]>('/requirements/external task system/projects'),
  syncExternalTasks: (projectId: string, abProjectId?: string) =>
    api.post<Requirement[]>('/requirements/sync-external task system', null, { params: { project_id: projectId, ab_project_id: abProjectId } }),
}

// ─── Test Cases ───────────────────────────────────────────────────────────────
type TestCaseCreateInput = Pick<TestCase, 'project_id' | 'title' | 'priority' | 'case_type'> &
  Partial<Pick<TestCase, 'requirement_id' | 'modules' | 'platforms' | 'preconditions' | 'steps' | 'expected_result'>>

export const testCasesApi = {
  list: (params?: { project_id?: string; requirement_id?: string; priority?: string; library_only?: boolean }) =>
    api.get<TestCase[]>('/testcases', { params }),
  get: (id: string) => api.get<TestCase>(`/testcases/${id}`),
  // 新增用例后端会同步自动生成步骤锚点(AI)，可能耗时数秒，关闭超时
  create: (data: TestCaseCreateInput) => api.post<TestCase>('/testcases', data, { timeout: 0 }),
  update: (id: string, data: Partial<TestCaseCreateInput>) => api.put<TestCase>(`/testcases/${id}`, data),
  regenCheckpoints: (data: { title?: string; steps: Array<{ action?: string; expected?: string; check_points?: string[] }> }) =>
    api.post<{ steps: Array<{ action?: string; expected?: string; check_points?: string[] }> }>('/testcases/regen-checkpoints', data, { timeout: 0 }),
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
  // App 自动登录：{app端key: {env(选环境名), account(手机号), code(验证码,默认SIT码), tenant(期望租户,Android App), label(端名)}}
  app_login?: Record<string, { env?: string; account?: string; code?: string; tenant?: string; label?: string }>
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
  // 进行中执行（服务端口径，供任意查看者看到「正在执行的用例」）
  active: (requirementId?: string, projectId?: string) =>
    api.get<{ id: string; status: string; case_ids: string[]; name: string }[]>(
      '/executions/active', { params: { requirement_id: requirementId, project_id: projectId } }),
  devices: () => api.get<{ adb_available: boolean; devices: { serial: string; model: string; status?: string; worker_name?: string; is_shared?: boolean; is_public?: boolean; busy?: boolean; owner_user_id?: string | null }[]; sonic_devices?: { serial: string; model: string; busy?: boolean; occupied_by?: string | null }[]; sonic_enabled?: boolean; sonic_error?: string | null; app_queue?: number; error: string | null }>('/executions/devices'),
  webAccounts: (platforms: string[]) =>
    api.get<Record<string, { covered: boolean; accounts: { role: string; label: string }[] }>>(
      '/executions/web-accounts', { params: { platforms: platforms.join(',') } }),
  // 各 App 端是否需要选/切租户（执行弹框据此决定是否显示「期望租户」框）
  appTenantSupport: (apps: string[]) =>
    api.get<Record<string, boolean>>('/executions/app-tenant-support', { params: { apps: apps.join(',') } }),
  create: (data: ExecutionCreateInput) => api.post<Execution>('/executions', data),
  // App「更换测试包」下拉数据源：某 app 端可选的包版本（后端查 Jenkins 构建记录，按「<端名>」匹配）
  appPackages: (app: string) =>
    api.get<{ app: string; packages: { id: string; label: string; version?: string }[] }>(
      '/executions/app-packages', { params: { app } }),
  results: (id: string) => api.get<TestResult[]>(`/executions/${id}/results`),
  // 执行实时日志（轮询增量拉取，after=上次最大 seq）；给 caseId 则只看该用例的日志(批量执行时)
  logs: (id: string, after = 0, caseId?: string) =>
    api.get<{ status: string; logs: { seq: number; ts: number; text: string; level: string; case_id?: string | null }[] }>(
      `/executions/${id}/logs`, { params: { after, case_id: caseId } }),
  cancel: (id: string) => api.post<{ ok: boolean; status: string; message?: string }>(`/executions/${id}/cancel`),
  cancelCase: (id: string, caseId: string) =>
    api.post<{ ok: boolean; status: string; message?: string }>(`/executions/${id}/cancel-case/${caseId}`),
  updateDefect: (resultId: string, defect_status: string) =>
    api.patch<TestResult>(`/executions/results/${resultId}/defect`, { defect_status }),
}

// ─── 常用项（按用户隔离，PC/App 通用）：kind=phone 常用号码 / tenant 常用租户 ──────
export const favPhonesApi = {
  list: (kind: 'phone' | 'tenant' = 'phone') =>
    api.get<{ id: string; phone: string }[]>('/fav-phones', { params: { kind } }),
  add: (phone: string, kind: 'phone' | 'tenant' = 'phone') =>
    api.post<{ id: string; phone: string }>('/fav-phones', { phone, kind }),
  remove: (id: string) => api.delete(`/fav-phones/${id}`),
}

// ─── 用例数据要求（测试数据准备 MVP-0：人工填 manual_values）────────────────
export interface DataRequirement {
  id?: string
  case_id: string
  alias: string
  data_type?: string | null
  target_state?: Record<string, any> | null
  constraints?: Record<string, any> | null
  strategy?: string
  manual_values?: Record<string, any> | null
  scenario_id?: string | null
  scenario_version?: string | null
  output_key?: string | null
  required?: boolean
  review_status?: string
}
export const dataRequirementsApi = {
  list: (caseId: string) =>
    api.get<{ requirements: DataRequirement[]; referenced_placeholders: string[] }>(
      '/data-requirements', { params: { case_id: caseId } }),
  upsert: (body: DataRequirement) => api.post<DataRequirement>('/data-requirements', body),
  remove: (id: string) => api.delete(`/data-requirements/${id}`),
}

// ─── 数据编排注册表（能力/场景/Schema）——MVP-1 管理台 ─────────────────────
export const dataRegistriesApi = {
  listCapabilities: () => api.get<any[]>('/data-capabilities'),
  upsertCapability: (body: any) => api.post<any>('/data-capabilities', body),
  activateCapability: (id: string) => api.post<any>(`/data-capabilities/${id}/activate`),
  disableCapability: (id: string) => api.post<any>(`/data-capabilities/${id}/disable`),
  removeCapability: (id: string) => api.delete(`/data-capabilities/${id}`),
  listScenarios: () => api.get<any[]>('/data-scenarios'),
  upsertScenario: (body: any) => api.post<any>('/data-scenarios', body),
  publishScenario: (id: string) => api.post<any>(`/data-scenarios/${id}/publish`),
  removeScenario: (id: string) => api.delete(`/data-scenarios/${id}`),
  listSchemas: () => api.get<any[]>('/data-object-schemas'),
  upsertSchema: (body: any) => api.post<any>('/data-object-schemas', body),
}

// ─── App 真机执行机 worker（连接我的真机）──────────────────────────────────
export const workerApi = {
  installInfo: (os?: string) => api.get<{ exe_available: boolean; win_available: boolean; mac_available: boolean; worker_token: string; owner_user_id: string }>('/worker/install-info', { params: os ? { os } : undefined }),
  downloadUrl: (os?: string) => `/api/worker/download${os ? `?os=${os}` : ''}`,
}

// ─── Pipeline ─────────────────────────────────────────────────────────────────
export const pipelineApi = {
  analyze: (requirement_id: string, scope_text?: string, scope_image_tokens?: string[], slice_id?: string, mode?: string, supplement?: string) =>
    api.post<PipelineStatus>('/pipeline/analyze', { requirement_id, scope_text, scope_image_tokens, slice_id, mode, supplement }),
  generateCases: (requirement_id: string, regenerate = false, scope_text?: string, scope_image_tokens?: string[], slice_id?: string, mode?: string, supplement?: string) =>
    api.post<PipelineStatus>('/pipeline/generate-cases', { requirement_id, regenerate, scope_text, scope_image_tokens, slice_id, mode, supplement }),
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
  requirementsQuality: (params?: { project_id?: string; iteration?: string; status?: string; platform?: string; owner?: string; mine?: boolean }) =>
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

export type RecordSession = {
  session_id: string
  base_url: string
  start_path: string | null
  status: 'recording' | 'parsed' | 'done' | 'error'
  error: string | null
  created_count: number
  updated_count: number
  existing_paths: { url_pattern: string; page_name: string }[]
  page_count: number
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
  // 会话式录制（多窗口）：start 非阻塞开窗；sessions 轮询状态并自动入库；commit 覆盖已存在；close 关闭会话
  recordStart: (data: { project_id: string; base_url: string; start_path?: string }) =>
    api.post<{ session: RecordSession; reused: boolean }>('/page-cache/record/start', data),
  recordSessions: (projectId: string) =>
    api.get<{ sessions: RecordSession[] }>('/page-cache/record/sessions', { params: { project_id: projectId } }),
  recordCommit: (session_id: string, overwrite: boolean) =>
    api.post<RecordResult>('/page-cache/record/commit', { session_id, overwrite }),
  recordClose: (session_id: string) =>
    api.post<{ ok: boolean }>('/page-cache/record/close', { session_id }),
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
  syncProduction: (projectId: string) =>
    api.post<{ synced: number; sedimented: number; skipped: number; reason?: string }>('/defects/sync-production', null, { params: { project_id: projectId } }),
}

// ─── AI 质量闭环：经验库 ──────────────────────────────────────────────────────
export const experiencesApi = {
  recall: (params: { requirement_id?: string; impact_id?: string; top_n?: number }) =>
    api.get<ExperienceRecallResponse>('/experiences/recall', { params }),
  adopt: (id: string, data: { requirement_id?: string; reason?: string }) =>
    api.post<{ status: string; confidence: number; exp_status: string }>(`/experiences/${id}/adopt`, data),
  ignore: (id: string, data: { requirement_id?: string; reason?: string }) =>
    api.post<{ status: string; confidence: number; exp_status: string }>(`/experiences/${id}/ignore`, data),
  notApplicable: (id: string, data: { requirement_id?: string; reason: string }) =>
    api.post<{ status: string; confidence: number; exp_status: string }>(`/experiences/${id}/not-applicable`, data),
  list: (params?: { project_id?: string; status?: string; limit?: number }) =>
    api.get<Experience[]>('/experiences', { params }),
  update: (id: string, data: { status?: string; title?: string; reason?: string }) =>
    api.patch<Experience>(`/experiences/${id}`, data),
  runMerge: (project_id?: string) => api.post<{ merged: number }>('/experiences/maintenance/merge', null, { params: { project_id } }),
}

// ─── AI 质量闭环：代码事实图谱 ────────────────────────────────────────────────
export const graphApi = {
  scan: (payload: { trigger_mode: 'local_path' | 'repo_branch'; repo_label?: string; repo_path?: string; business_repo_id?: string; branch?: string; version?: string }) =>
    api.post<{ status: string; version: string }>('/graph/scan', payload, { timeout: 0 }),
  seedPages: (project_id?: string) => api.post<{ seeded: number }>('/graph/seed-pages', null, { params: { project_id } }),
  nodes: (params?: { node_type?: string; repo?: string; q?: string; limit?: number }) =>
    api.get<GraphNode[]>('/graph/nodes', { params }),
  expand: (node: string, max_hops = 2) => api.get<GraphExpandResponse>('/graph/expand', { params: { node, max_hops } }),
  stats: () => api.get<{ nodes: number; edges: number; by_type: Record<string, number> }>('/graph/stats'),
}

// ─── AI 质量闭环：硬规则编辑 + 发布报告 + 度量 ────────────────────────────────
export type QualityRule = {
  id: string; name: string; match_tags: string[]; min_priority?: string | null
  required_covered_items?: string[] | null; source?: string | null; active: boolean; created_at?: string
}
export const qualityRulesApi = {
  list: (active?: boolean) => api.get<QualityRule[]>('/quality-rules', { params: { active } }),
  create: (data: Partial<QualityRule>) => api.post<QualityRule>('/quality-rules', data),
  update: (id: string, data: Partial<QualityRule>) => api.put<QualityRule>(`/quality-rules/${id}`, data),
  remove: (id: string) => api.delete<{ status: string }>(`/quality-rules/${id}`),
}
export const releaseApi = {
  report: (reqId: string) => api.get<{ release_suggestion: 'pass' | 'warn' | 'block'; reasons: string[]; summary: Record<string, unknown>; entry_coverage_matrix?: unknown[] }>(`/requirements/${reqId}/release-report`, { timeout: 600000 }),
}
export const metricsApi = {
  qualityLoop: (project_id?: string) => api.get<{
    impact_accuracy: number; ai_case_modify_rate: number; experience_adopt_rate: number
    case_reuse_rate: number; coverage_verify_rate: number
    raw: Record<string, number>
  }>('/metrics/quality-loop', { params: { project_id } }),
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
  // 公开:拿 SSO 对接认证地址(external task system 地址),未登录跳转发券入口用。
  ssoConfig: () => api.get<{ external_task_url: string }>('/auth/sso-config'),
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

export type SsoConfig = { external_task_url: string; resolved: string; default: string }
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
  setSsoConfig: (external_task_url: string) =>
    api.put<SsoConfig>('/system/sso-config', { external_task_url }),
  getAiConfig: () => api.get<AiConfig>('/system/ai-config'),
  setAiConfig: (data: { provider: string; model: string; base_url: string; api_key?: string }) =>
    api.put<AiConfig>('/system/ai-config', data),
  getGuardianConfig: () => api.get<GuardianConfig>('/system/guardian-config'),
  setGuardianConfig: (data: { enabled: boolean; base_url: string; pat?: string }) =>
    api.put<GuardianConfig>('/system/guardian-config', data),
  pingGuardian: () => api.post<{ ok: boolean; detail: unknown }>('/system/guardian-ping'),
}

export type GuardianConfig = {
  enabled: boolean
  base_url: string
  pat_set: boolean
  pat_masked: string
  product: string
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

// ─── AI 质量闭环：覆盖项 Review（挂 testcase 子资源）───────────────────────────
export const coveredItemsApi = {
  add: (caseId: string, item: Partial<CoveredItem> & { source?: string; reason?: string }) =>
    api.post<{ status: string; covered_items: CoveredItem[] }>(`/testcases/${caseId}/covered-items`, item),
  update: (caseId: string, itemId: string, patch: Partial<CoveredItem> & { reason?: string }) =>
    api.patch<{ status: string; covered_items: CoveredItem[] }>(`/testcases/${caseId}/covered-items/${itemId}`, patch),
  remove: (caseId: string, itemId: string, reason?: string) =>
    api.delete<{ status: string; covered_items: CoveredItem[] }>(`/testcases/${caseId}/covered-items/${itemId}`, { data: { reason } }),
  backfill: (params: { project_id?: string; limit?: number; only_in_library?: boolean }) =>
    api.post<{ scanned: number; filled: number; failed: number }>('/testcases/backfill-covered-items', null, { params }),
  backfillPending: (projectId?: string) =>
    api.get<{ pending: number }>('/testcases/backfill-covered-items/pending', { params: { project_id: projectId } }),
}

// ─── AI 质量闭环：代码影响分析（手动触发）─────────────────────────────────────
export const codeImpactApi = {
  trigger: (payload: {
    trigger_mode: 'paste_diff' | 'local_path' | 'repo_branch'
    requirement_id?: string
    diff_text?: string
    repo_label?: string
    repo_path?: string
    business_repo_id?: string
    base_branch?: string
    target_branch?: string
    commit_id?: string
    mr_id?: string
  }) => api.post<ChangeImpactRecord>('/code-impact/analyze', payload, { timeout: 0 }),
  get: (impactId: string) => api.get<ChangeImpactRecord>(`/code-impact/${impactId}`),
  listByRequirement: (requirementId?: string, limit = 50) =>
    api.get<ChangeImpactRecord[]>('/code-impact', { params: { requirement_id: requirementId, limit } }),
  reportUrl: (impactId: string) => `/api/code-impact/${impactId}/report.md`,
}

// ─── 被测业务仓库登记（代码变更分析「下拉选仓库」数据源）─────────────────────────
export const businessRepoApi = {
  list: (projectId?: string) =>
    api.get<BusinessRepo[]>('/business-repos', { params: projectId ? { project_id: projectId } : undefined }),
  create: (payload: { name: string; git_url: string; default_branch?: string; token?: string; project_id?: string }) =>
    api.post<BusinessRepo>('/business-repos', payload),
  remove: (id: string) => api.delete(`/business-repos/${id}`),
}

// ─── App 自动登录配方（配置驱动，无需改代码就把各 App 登录放进平台）─────────────
export type AppLoginRecipe = {
  id: string
  name: string
  match_keywords: string
  env_steps?: string | null
  restart_after_env: boolean
  needs_tenant: boolean
  enabled: boolean
  created_at?: string
  updated_at?: string
}
export type AppLoginRecipeInput = {
  name: string
  match_keywords: string
  env_steps?: string | null
  restart_after_env: boolean
  needs_tenant: boolean
  enabled: boolean
}
export const appLoginRecipesApi = {
  list: () => api.get<AppLoginRecipe[]>('/app-login-recipes'),
  create: (payload: AppLoginRecipeInput) => api.post<AppLoginRecipe>('/app-login-recipes', payload),
  update: (id: string, payload: AppLoginRecipeInput) => api.put<AppLoginRecipe>(`/app-login-recipes/${id}`, payload),
  remove: (id: string) => api.delete(`/app-login-recipes/${id}`),
}

// ─── AI 质量闭环：覆盖矩阵 ────────────────────────────────────────────────────
export const coverageMatrixApi = {
  byRequirement: (reqId: string) =>
    api.get<CoverageMatrixResponse>(`/requirements/${reqId}/coverage-matrix`, { timeout: 600000 }),
  byExecution: (execId: string) =>
    api.get<{ execution_id: string; checked_points: unknown[]; summary: { total: number; passed: number; verify_rate: number } }>(`/executions/${execId}/coverage`),
}
