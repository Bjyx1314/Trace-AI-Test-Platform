import { useEffect, useState } from 'react'
import { Tooltip } from 'antd'
import { metricsApi } from '../api'
import { MONO_FONT, PANEL_CARD_STYLE } from '../styles/theme'

interface M {
  impact_accuracy: number; ai_case_modify_rate: number; experience_adopt_rate: number
  case_reuse_rate: number; coverage_verify_rate: number
  raw?: Record<string, number>
}

// key: 指标字段；denom: 判空所看的样本量字段(为0=暂无数据，显示 — 而非红)；goodLow: 越低越好
const ITEMS: { key: keyof M; denom: string; label: string; tip: string; goodLow?: boolean }[] = [
  { key: 'coverage_verify_rate', denom: 'covered_items_total', label: '覆盖项验证率', tip: '已验证覆盖项 / 全部覆盖项，衡量测得实不实' },
  { key: 'case_reuse_rate', denom: 'reuse_total', label: '用例复用率', tip: '(可复用+需调整) / 影响分析建议用例，衡量少重复生成' },
  { key: 'experience_adopt_rate', denom: 'experience_hits', label: '经验采纳率', tip: '采纳的经验命中 / 总命中，低=召回策略要调' },
  { key: 'ai_case_modify_rate', denom: 'covered_item_feedback', label: 'AI 用例修改率', tip: '被改/删的覆盖项占比，随时间下降=AI 学得越来越准', goodLow: true },
  { key: 'impact_accuracy', denom: 'impact_scope_feedback', label: '影响面准确率', tip: '影响面被采纳 / 总，校准代码影响分析' },
]

function healthColor(v: number, goodLow?: boolean): string {
  const score = goodLow ? 1 - v : v
  if (score >= 0.7) return '#16A34A'
  if (score >= 0.4) return '#E8930C'
  return '#EF4444'
}

/** 质量闭环指标（方案15）：证明体系是否「越用越好」。仅管理员、全部需求 tab 底部展示。 */
export default function QualityLoopMetrics({ projectId }: { projectId?: string }) {
  const [m, setM] = useState<M | null>(null)
  useEffect(() => { metricsApi.qualityLoop(projectId).then((r) => setM(r.data)).catch(() => {}) }, [projectId])
  if (!m) return null

  return (
    <div style={{ ...PANEL_CARD_STYLE, background: '#fff', padding: '20px 22px', marginTop: 18 }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 20 }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: '#0F172A' }}>质量闭环指标</span>
        <span style={{ fontSize: 12, color: '#94A3B8' }}>体系是否越用越好</span>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5, 1fr)', gap: 14 }}>
        {ITEMS.map((it) => {
          const v = Number(m[it.key] as number) || 0
          const hasData = (m.raw?.[it.denom] ?? 0) > 0
          const color = hasData ? healthColor(v, it.goodLow) : '#CBD5E1'
          const pct = Math.round(v * 100)
          return (
            <Tooltip key={it.key} title={it.tip}>
              <div style={{ border: '1px solid #ECEFF2', borderRadius: 12, padding: '14px 15px', cursor: 'default' }}>
                <div style={{ fontSize: 12, color: '#64748B', marginBottom: 10, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {it.label}{it.goodLow && <span style={{ color: '#B0BAC4', marginLeft: 4 }}>↓好</span>}
                </div>
                <div style={{ display: 'flex', alignItems: 'baseline', gap: 2 }}>
                  <span style={{ fontFamily: MONO_FONT, fontSize: 26, fontWeight: 600, lineHeight: 1, color: hasData ? '#0F172A' : '#CBD5E1' }}>
                    {hasData ? pct : '—'}
                  </span>
                  {hasData && <span style={{ fontFamily: MONO_FONT, fontSize: 13, color: '#94A3B8' }}>%</span>}
                </div>
                <div style={{ height: 4, background: '#F1F4F6', borderRadius: 999, marginTop: 11, overflow: 'hidden' }}>
                  <div style={{ height: '100%', borderRadius: 999, background: color, width: hasData ? `${pct}%` : '0%', transition: 'width .4s ease' }} />
                </div>
                {!hasData && <div style={{ fontSize: 10.5, color: '#B0BAC4', marginTop: 6 }}>暂无数据</div>}
              </div>
            </Tooltip>
          )
        })}
      </div>
    </div>
  )
}
