import type { CoveredItemSource, CoverageStatus } from '../types/api'

// 覆盖项来源：中文标签 + 颜色（chips 与覆盖矩阵共用，避免各处硬编码）
export const SOURCE_LABEL: Record<CoveredItemSource, string> = {
  requirement: '需求',
  code_impact: '代码变更',
  rule: '规则',
  historical_feedback: '经验',
  tester_added: '补充',
  production_issue: '线上问题',
  backfill: '回填',
}

export const SOURCE_COLOR: Record<CoveredItemSource, string> = {
  requirement: 'blue',
  code_impact: 'purple',
  rule: 'red',
  historical_feedback: 'gold',
  tester_added: 'green',
  production_issue: 'volcano',
  backfill: 'default',
}

export const COVERAGE_STATUS_LABEL: Record<CoverageStatus, string> = {
  not_covered: '未覆盖',
  covered: '已验证',
  failed: '失败',
}

export const COVERAGE_STATUS_COLOR: Record<CoverageStatus, string> = {
  not_covered: 'default',
  covered: 'success',
  failed: 'error',
}

export const RISK_LEVEL_LABEL: Record<string, string> = { low: '低', mid: '中', high: '高' }
export const RISK_LEVEL_COLOR: Record<string, string> = { low: 'green', mid: 'orange', high: 'red' }

export function sourceLabel(s: string): string {
  return SOURCE_LABEL[s as CoveredItemSource] || s
}
export function sourceColor(s: string): string {
  return SOURCE_COLOR[s as CoveredItemSource] || 'default'
}
