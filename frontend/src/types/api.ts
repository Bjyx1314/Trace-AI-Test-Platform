/** 与后端 schemas.py / models.py 对应的前端类型定义 */

// ─── 项目 ───────────────────────────────────────────────────────────────────
export interface Project {
  id: string
  name: string
  description?: string | null
  product_line?: string | null
  case_id_prefix: string
  feishu_webhook?: string | null
  feishu_doc_url?: string | null
  ci_gate_enabled: boolean
  pass_rate_threshold: number
  created_at: string
  updated_at: string
}

// ─── 需求 ───────────────────────────────────────────────────────────────────
export interface ConfirmationPoint {
  point_id: string
  content: string
  status: 'pending_confirmation' | 'confirmed'
  confirmation?: string | null
  no_confirmation_needed: boolean
}

export interface IssuePoint {
  issue_id: string
  description: string
  module?: string | null
  platforms: string[]
  confirmation_points: ConfirmationPoint[]
}

export interface AnalysisResult {
  source_req_id?: string | null
  product_line?: string | null
  issue_points: IssuePoint[]
}

export interface Requirement {
  id: string
  project_id: string
  title: string
  content: string
  product_line?: string | null
  iteration?: string | null
  source: string
  source_record_id?: string | null
  status: string
  attachment_path?: string | null
  analysis_result?: AnalysisResult | null
  analysis_confirmation?: string | null
  owner_name?: string | null
  slice_count?: number
  created_at: string
  updated_at: string
}

// ─── 测试用例 ────────────────────────────────────────────────────────────────
export interface TestStep {
  seq: number
  action: string
  expected: string
}

export interface TestCase {
  id: string
  case_id: string
  project_id: string
  requirement_id?: string | null
  product_line?: string | null
  source_req_id?: string | null
  modules: string[]
  platforms: string[]
  title: string
  priority: 'P0' | 'P1' | 'P2'
  preconditions: string[]
  steps: TestStep[]
  expected_result?: string | null
  source_issue_point?: string | null
  case_type: 'ui' | 'api'
  last_status: 'not_run' | 'passed' | 'failed' | 'skipped'
  script?: string | null
  script_status: string
  is_automated: boolean
  in_library?: boolean
  review_status?: string | null
  similar_case_id?: string | null
  similar_case_case_id?: string | null
  similar_case_title?: string | null
  tags?: Record<string, unknown> | null
  deleted_at?: string | null
  created_at: string
  updated_at: string
}

export interface TestCaseLog {
  id: string
  operation: 'create' | 'update' | 'delete'
  operator: string
  snapshot?: Record<string, unknown> | null
  created_at: string
}

// ─── 执行 & 结果 ──────────────────────────────────────────────────────────────
export interface CiGateBlockingReason {
  rule: string
  message: string
  severity: 'block' | 'warn'
}

export interface CiGateResult {
  releasable: boolean
  blocking_reasons: CiGateBlockingReason[]
}

export interface Execution {
  id: string
  project_id: string
  name: string
  trigger: string
  status: 'pending' | 'running' | 'done' | 'failed'
  total: number
  passed: number
  failed: number
  skipped: number
  pass_rate: number
  duration_ms: number
  ci_gate_result?: CiGateResult | null
  error_message?: string | null
  created_at: string
  finished_at?: string | null
}

export interface TestResult {
  id: string
  execution_id: string
  execution_name?: string
  test_case_id: string
  status: 'passed' | 'failed' | 'skipped' | 'error'
  duration_ms: number
  error_message?: string | null
  screenshot_url?: string | null
  api_trace?: {
    trace_id?: string
    request?: { method?: string; url?: string; headers?: Record<string, string>; body?: unknown }
    response?: { status?: number; headers?: Record<string, string>; body?: unknown }
  } | null
  failure_type?: string | null
  ai_diagnosis?: Record<string, unknown> | null
  repair_suggestion?: string | null
  defect_status: string
  created_at: string
}

// ─── 缺陷 ───────────────────────────────────────────────────────────────────
export interface DefectDraftTicket {
  summary?: string | null
  reproduction_steps?: string[]
  affected_scope?: string | null
  severity?: string | null
  type?: string | null
}

export interface Defect {
  id: string
  test_result_id: string
  execution_id: string
  test_case_id: string
  title: string
  severity: 'P0' | 'P1' | 'P2'
  confidence: 'HIGH' | 'MEDIUM' | 'LOW'
  status: 'draft' | 'ticket_created' | 'confirmed' | 'ignored' | 'duplicate'
  draft_ticket?: DefectDraftTicket | null
  feishu_ticket_id?: string | null
  external_ticket_id?: string | null
  external_ticket_url?: string | null
  duplicate_of_defect_id?: string | null
  created_at: string
  updated_at: string
}

// ─── 质量门禁配置 ─────────────────────────────────────────────────────────────
export interface QualityGateConfig {
  id: string
  project_id: string
  overall_pass_rate_threshold: number
  enable_overall_pass_rate_gate: boolean
  p1_failure_threshold: number
  enable_p1_failure_gate: boolean
  pass_rate_wow_drop_threshold: number
  coverage_threshold: number
  created_at: string
  updated_at: string
}

// ─── 枚举 ───────────────────────────────────────────────────────────────────
export interface EnumDefinition {
  id: string
  category: string
  key: string
  label: string
  parent_key?: string | null
  sort_order: number
  is_active: boolean
  created_at: string
}

// ─── Dashboard ────────────────────────────────────────────────────────────────
export interface DashboardSummary {
  total_cases: number
  total_requirements: number
  total_executions: number
  last_pass_rate: number | null
  last_execution_status: string | null
  confirmed_defects: number
  trend: Array<{ name: string; pass_rate: number; date: string }>
}

export interface BreakdownItem {
  key: string | null
  count: number
}

export interface DashboardBreakdown {
  cases_by_project: BreakdownItem[]
  cases_by_product_line: BreakdownItem[]
  cases_by_module: BreakdownItem[]
  cases_by_platform: BreakdownItem[]
  cases_by_case_type: BreakdownItem[]
  cases_by_priority: BreakdownItem[]
  cases_total: number
  cases_automated: number
  defects_by_severity: BreakdownItem[]
  defects_by_status: BreakdownItem[]
}

export interface GateResult {
  gate: 'pass' | 'fail' | 'skip'
  reason?: string
  pass_rate?: number | null
  blocking_reasons?: CiGateBlockingReason[]
  releasable?: boolean
  execution_id?: string
}

export interface SliceQuality {
  id: string
  scope_label: string
  owner_name?: string | null
  is_default: boolean
  status: string
  analyzed?: boolean
  total_cases: number
  passed: number
  skipped: number
  total_defects: number
  fixed_defects: number
  sev_open?: Record<string, number>
  releasability: 'pass' | 'warn' | 'block' | 'not_started'
}

export interface RequirementQuality {
  id: string
  title: string
  status: string
  iteration?: string | null
  owner_name?: string | null
  slices?: SliceQuality[]
  project_name: string
  project_id: string
  total_cases: number
  passed: number
  failed: number
  skipped: number
  not_run: number
  pass_rate: number
  p0_open: number
  p1_open: number
  p2_open: number
  p0_total: number
  p1_total: number
  p2_total: number
  total_defects: number
  open_defects: number
  fixed_defects: number
  sev_open?: Record<string, number>
  sev_total?: Record<string, number>
  releasability: 'pass' | 'warn' | 'block' | 'not_started'
  blocking_reasons: string[]
}

export interface RequirementQualitySummary {
  total_requirements: number
  done_requirements: number
  blocked_requirements: number
  test_progress: number
  total_cases: number
  total_defects: number
  p0_open_defects: number
  p1_open_defects: number
  p2_open_defects: number
  p0_total_defects: number
  p1_total_defects: number
  p2_total_defects: number
  fixed_defects: number
  severity_breakdown?: Record<string, { total: number; open: number }>
}

export interface RequirementsQualityResponse {
  requirements: RequirementQuality[]
  summary: RequirementQualitySummary
}

export interface RequirementsGateResponse {
  releasable: boolean
  blocked_reqs: Array<{ req_id: string; title: string; reasons: string[] }>
}

// ─── 页面结构缓存 ─────────────────────────────────────────────────────────────
export interface CacheRegionElement {
  name: string
  selector: string
  type: string
}

export interface CacheRegion {
  name: string
  selector: string
  elements?: CacheRegionElement[]
}

export interface PageStructureCache {
  id: string
  project_id: string
  base_url?: string | null
  url_pattern: string
  page_name: string
  description?: string | null
  dom_hash?: Record<string, string> | null
  regions?: CacheRegion[] | null
  region_count: number
  element_count: number
  captured_at?: string | null
  updated_at?: string | null
  last_hit_at?: string | null
  hit_count: number
  status: 'active' | 'stale' | 'needs_update'
}

// ─── Pipeline ─────────────────────────────────────────────────────────────────
export interface PipelineStatus {
  requirement_id: string
  status: string
  message?: string | null
  failed?: boolean
  cases_generated?: number
  scripts_generated?: number
}
