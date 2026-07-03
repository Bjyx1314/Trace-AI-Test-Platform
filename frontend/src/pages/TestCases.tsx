import { useEffect, useState } from 'react'
import { Typography, Card, Row, Col, Spin, Empty } from 'antd'
import { useNavigate } from 'react-router-dom'
import { dashboardApi } from '../api'
import { PANEL_CARD_STYLE, MONO_FONT } from '../styles/theme'

interface BreakdownItem { key: string | null; count: number; id?: string; auto?: number; manual?: number }
interface Breakdown {
  cases_by_project: BreakdownItem[]
  cases_by_module: BreakdownItem[]
  cases_by_platform: BreakdownItem[]
  cases_by_case_type: BreakdownItem[]
  cases_by_priority: BreakdownItem[]
  cases_total: number
  cases_automated: number
  cases_auto_executed?: number
}

// 柱体配色：纯色柔和（粉蓝清新系），按 key 取色
const C_BLUE = { bar: '#7BB8E8', text: '#2D6E9E' }
const C_GREEN = { bar: '#7ECBAF', text: '#1F7A5A' }
const C_PURPLE = { bar: '#B5A8F0', text: '#5446A8' }
const C_RED = { bar: '#F4907A', text: '#C04030' }
const C_YELLOW = { bar: '#F9C06A', text: '#9A6010' }
const C_ORANGE = { bar: '#F4C08A', text: '#9A6010' }
const BAR_COLORS: Record<string, { bar: string; text: string }> = {
  'trade-order': C_BLUE, backend_api: C_BLUE, api: C_BLUE,
  'delivery-fulfillment': C_GREEN, web: C_GREEN,
  admin: C_PURPLE, android: C_PURPLE, ios: C_PURPLE,
  P0: C_RED, P1: C_YELLOW, P2: C_BLUE, P3: C_PURPLE,
  ui: C_ORANGE,
}
const PALETTE = [C_BLUE, C_GREEN, C_PURPLE, C_ORANGE, C_YELLOW, C_RED]
const colorFor = (key: string | null, i: number) => BAR_COLORS[key || ''] || PALETTE[i % PALETTE.length]

// 执行方式配色（全图表统一，便于图例）：自动=绿、手动=琥珀、未执行=浅灰
const K_AUTO = '#7ECBAF', K_MANUAL = '#F4B860', K_NOTRUN = '#E3E8EE'

/** 自定义堆叠柱状图：每柱按 自动/手动/未执行 拆分；点击某段跳转带对应筛选 */
function BarGroup({
  data, gap, priority, onBar,
}: {
  data: BreakdownItem[]
  gap: number
  priority?: boolean
  onBar: (d: BreakdownItem, auto?: 'auto' | 'manual') => void
}) {
  if (!data || data.length === 0) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无数据" style={{ padding: 8 }} />
  const max = Math.max(1, ...data.map((d) => d.count))
  return (
    <>
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'flex-end', gap, height: 100, padding: '0 8px' }}>
        {data.map((d, i) => {
          const total = d.count || 0
          const auto = d.auto || 0
          const manual = d.manual || 0
          const notrun = Math.max(0, total - auto - manual)
          const barH = Math.max(8, Math.round((total / max) * 90))
          const seg = (n: number) => (total ? Math.round((barH * n) / total) : 0)
          const hA = seg(auto), hM = seg(manual)
          const hN = Math.max(0, barH - hA - hM)
          const c = colorFor(d.key, i)
          const title = `${d.key ?? '未分类'}：自动 ${auto} · 手动 ${manual} · 未执行 ${notrun}`
          return (
            <div key={i} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
              <span style={{ fontSize: 12, fontWeight: 600, fontFamily: MONO_FONT, color: c.text }}>{total}</span>
              <div title={title} style={{ width: 38, height: barH, borderRadius: '6px 6px 2px 2px', overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                {hN > 0 && <div onClick={() => onBar(d)} style={{ height: hN, background: K_NOTRUN, cursor: 'pointer' }} />}
                {hM > 0 && <div onClick={() => onBar(d, 'manual')} style={{ height: hM, background: K_MANUAL, cursor: 'pointer' }} />}
                {hA > 0 && <div onClick={() => onBar(d, 'auto')} style={{ height: hA, background: K_AUTO, cursor: 'pointer' }} />}
              </div>
              {priority ? (
                <span onClick={() => onBar(d)} style={{ cursor: 'pointer', fontSize: 11, fontWeight: 600, padding: '1px 8px', borderRadius: 999, background: c.bar + '22', color: c.text }}>{d.key}</span>
              ) : (
                <span onClick={() => onBar(d)} style={{ cursor: 'pointer', fontSize: 10.5, color: '#64748B', fontFamily: MONO_FONT, whiteSpace: 'nowrap', maxWidth: 80, overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.key ?? '未分类'}</span>
              )}
            </div>
          )
        })}
      </div>
      <div style={{ display: 'flex', justifyContent: 'center', gap: 16, marginTop: 12 }}>
        {[['自动测试', K_AUTO], ['手动测试', K_MANUAL], ['未执行', K_NOTRUN]].map(([l, col]) => (
          <span key={l} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 11, color: '#64748B' }}>
            <i style={{ width: 9, height: 9, borderRadius: 2, background: col, display: 'inline-block' }} />{l}
          </span>
        ))}
      </div>
    </>
  )
}

function StatCard({ icon, label, value, iconColor, iconBg }: { icon: string; label: string; value: number | string; iconColor: string; iconBg: string }) {
  return (
    <Card bordered={false} className="tech-card" styles={{ body: { padding: '15px 18px' } }} style={PANEL_CARD_STYLE}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 14 }}>
        <div style={{ width: 42, height: 42, borderRadius: 12, flexShrink: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', background: iconBg }}>
          <span className="ms" style={{ fontSize: 22, color: iconColor }}>{icon}</span>
        </div>
        <div style={{ minWidth: 0 }}>
          <div style={{ fontSize: 12, color: '#64748B', marginBottom: 3 }}>{label}</div>
          <div style={{ fontFamily: MONO_FONT, fontSize: 24, fontWeight: 600, color: '#0F172A' }}>{value}</div>
        </div>
      </div>
    </Card>
  )
}

export default function TestCases() {
  const navigate = useNavigate()
  const [breakdown, setBreakdown] = useState<Breakdown | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    dashboardApi.breakdown().then((r) => setBreakdown(r.data)).finally(() => setLoading(false))
  }, [])

  const goList = (params: Record<string, string>) => {
    navigate(`/testcases/list?${new URLSearchParams(params).toString()}`)
  }

  const sum = (items?: BreakdownItem[]) => (items || []).reduce((acc, x) => acc + (x.count || 0), 0)
  const totalCases = breakdown?.cases_total ?? sum(breakdown?.cases_by_case_type)
  const coverage = totalCases > 0 ? Math.round(((breakdown?.cases_automated ?? 0) / totalCases) * 100) : 0
  // 自动测试占比：最近一次经「执行测试」自动跑过的用例占比（只看执行方式，不看结果；手动测试/未执行不算）
  const autoTestRate = totalCases > 0 ? Math.round(((breakdown?.cases_auto_executed ?? 0) / totalCases) * 100) : 0

  const stats = [
    { icon: 'description', label: '用例总数', value: totalCases, iconColor: '#D97757', iconBg: '#FEF3EE' },
    { icon: 'schema', label: '覆盖模块', value: breakdown?.cases_by_module?.length ?? 0, iconColor: '#6B4FD6', iconBg: '#F0EDFD' },
    { icon: 'devices', label: '适用端', value: breakdown?.cases_by_platform?.length ?? 0, iconColor: '#0B8276', iconBg: '#E6F6F4' },
    { icon: 'bolt', label: '自动测试占比', value: `${autoTestRate}%`, iconColor: '#E8930C', iconBg: '#FEF6E7' },
  ]

  const charts: Array<{ key: keyof Breakdown; title: string; icon: string; iconColor: string; paramKey: string; gap: number; priority?: boolean; getId?: (d: BreakdownItem) => string }> = [
    { key: 'cases_by_module', title: '按模块分布', icon: 'bar_chart', iconColor: '#7BB8E8', paramKey: 'module', gap: 20 },
    { key: 'cases_by_priority', title: '按优先级分布', icon: 'stacked_bar_chart', iconColor: '#F4907A', paramKey: 'priority', gap: 28, priority: true },
    { key: 'cases_by_platform', title: '按端分布', icon: 'bar_chart', iconColor: '#7ECBAF', paramKey: 'platform', gap: 20 },
    { key: 'cases_by_case_type', title: '按场景类型分布', icon: 'bar_chart', iconColor: '#B5A8F0', paramKey: 'case_type', gap: 36 },
  ]

  return (
    <div style={{ padding: '24px 28px 40px', animation: 'fadeUp .35s ease both' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
        <Typography.Text style={{ fontSize: 13, color: '#64748B' }}>全局用例资产概览 · 点击图表柱状条可跳转对应筛选的用例列表</Typography.Text>
        <button onClick={() => navigate('/testcases/list')} style={{ display: 'flex', alignItems: 'center', gap: 6, height: 36, padding: '0 15px', background: 'linear-gradient(140deg,#E8930C,#D97757)', color: '#fff', border: 'none', borderRadius: 9, fontSize: 13, fontWeight: 500, cursor: 'pointer', boxShadow: '0 4px 12px -5px rgba(217,119,87,.5)' }}>
          <span className="ms" style={{ fontSize: 18 }}>list_alt</span>进入用例列表
        </button>
      </div>

      {loading ? (
        <Spin style={{ display: 'block', margin: '60px auto' }} />
      ) : (
        <>
          <div style={{ display: 'flex', gap: 14, marginBottom: 18, flexWrap: 'wrap' }}>
            {stats.map((s) => (
              <div key={s.label} style={{ flex: 1, minWidth: 158 }}><StatCard {...s} /></div>
            ))}
            <div style={{ flex: 1.2, minWidth: 200 }}>
              <div className="tech-stat-card" style={{ ...PANEL_CARD_STYLE, border: '1px solid #F5D6C8', padding: '15px 18px', boxShadow: '0 4px 14px -8px rgba(217,119,87,.25)' }}>
                <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12 }}>
                  <div style={{ width: 50, height: 50, flex: 'none', borderRadius: '50%', background: `conic-gradient(#D97757 ${coverage * 3.6}deg, #FDEAE0 0deg)`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                    <div style={{ width: 36, height: 36, borderRadius: '50%', background: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO_FONT, fontSize: 11, fontWeight: 600, color: '#B5600A' }}>{coverage}</div>
                  </div>
                  <div>
                    <div style={{ fontSize: 12, color: '#64748B', marginBottom: 3 }}>自动化覆盖率</div>
                    <div style={{ fontFamily: MONO_FONT, fontSize: 22, fontWeight: 600, color: '#B5600A' }}>{coverage}%</div>
                  </div>
                </div>
              </div>
            </div>
          </div>

          <Row gutter={[14, 14]}>
            {charts.map(({ key, title, icon, iconColor, paramKey, gap, priority, getId }) => (
              <Col xs={24} lg={12} key={key}>
                <Card bordered={false} style={PANEL_CARD_STYLE} styles={{ body: { padding: '20px 22px' } }}>
                  <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 18 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <span className="ms" style={{ fontSize: 18, color: iconColor }}>{icon}</span>
                      <span style={{ fontSize: 14, fontWeight: 700, color: '#0F172A' }}>{title}</span>
                    </div>
                    <span onClick={() => navigate('/testcases/list')} style={{ fontSize: 12, color: '#B5600A', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 2 }}>
                      查看列表<span className="ms" style={{ fontSize: 16 }}>chevron_right</span>
                    </span>
                  </div>
                  <BarGroup
                    data={(breakdown?.[key] as BreakdownItem[]) || []}
                    gap={gap}
                    priority={priority}
                    onBar={(d, auto) => { const v = getId ? getId(d) : d.key; if (v) goList({ [paramKey]: v, ...(auto ? { auto } : {}) }) }}
                  />
                </Card>
              </Col>
            ))}
          </Row>
        </>
      )}
    </div>
  )
}
