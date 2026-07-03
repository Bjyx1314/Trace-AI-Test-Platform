import React, { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Spin, Empty, Select, Table, Typography, Tooltip } from 'antd'
import { dashboardApi, projectsApi, enumsApi } from '../api'
import { useProjectStore } from '../store/projectStore'
import { useAuthStore } from '../store/authStore'
import { PANEL_CARD_STYLE, MONO_FONT } from '../styles/theme'
import GateTag, { toGateState } from '../components/GateTag'

// 缺陷等级配色（按枚举顺序：由重到轻）
const LEVEL_COLORS = ['#EF4444', '#F97316', '#E8930C', '#1683CC', '#64748B']
const LEVEL_TINT = [
  { bg: '#FDECEC', color: '#C9332B' }, { bg: '#FEEFE5', color: '#C2410C' },
  { bg: '#FEF6E7', color: '#B7791F' }, { bg: '#E8F3FB', color: '#0E6FB0' },
  { bg: '#F1F5F9', color: '#64748B' },
]
const shortLevel = (label: string) => label.split(/[-·]/)[0]
import type { RequirementQuality, RequirementQualitySummary, Project } from '../types/api'

const REQ_STATUS_LABEL: Record<string, string> = {
  // 与需求列表(Requirements.tsx STATUS_LABEL)保持一致
  pending_analysis: '待需求分析', analyzing: '分析中', analyzed: '已分析', done: '已完成',
  pending_case_generation: '待生成用例', generating_cases: '生成用例中', pending_test: '待测试', testing: '测试中',
}
// 状态胶囊配色（设计稿风格）
// 状态胶囊配色（对齐 README 状态色：成功绿/信息蓝/分析紫/警告琥珀/中性灰）
const REQ_STATUS_PILL: Record<string, { bg: string; color: string }> = {
  done: { bg: '#E9F8EF', color: '#128A43' },
  testing: { bg: '#EAF4FB', color: '#1577C2' },
  generating_cases: { bg: '#EAF4FB', color: '#1577C2' },
  analyzing: { bg: '#F0EDFF', color: '#6B4FD6' },
  pending_test: { bg: '#FEF6E7', color: '#B5710A' },
  pending_analysis: { bg: '#F1F5F9', color: '#64748B' },
  pending_case_generation: { bg: '#F1F5F9', color: '#64748B' },
  analyzed: { bg: '#F1F5F9', color: '#64748B' },
}
const pillStyle = (s: string) => REQ_STATUS_PILL[s] || { bg: '#F1F5F9', color: '#64748B' }

export default function Dashboard() {
  const navigate = useNavigate()
  const currentProject = useProjectStore((s) => s.currentProject)
  const isAdmin = useAuthStore((s) => s.isAdmin)

  const [activeTab, setActiveTab] = useState<'my' | 'all'>('all')
  const [allProjects, setAllProjects] = useState<Project[]>([])
  const [projectFilter, setProjectFilter] = useState<string | undefined>()
  const [iterationFilter, setIterationFilter] = useState<string | undefined>()
  const [statusFilter, setStatusFilter] = useState<string | undefined>()
  const [ownerFilter, setOwnerFilter] = useState<string | undefined>()
  const [iterationOptions, setIterationOptions] = useState<string[]>([])
  const [ownerOptions, setOwnerOptions] = useState<string[]>([])
  const [sevLevels, setSevLevels] = useState<{ key: string; label: string }[]>([])

  const [reqQuality, setReqQuality] = useState<RequirementQuality[]>([])
  const [qualitySummary, setQualitySummary] = useState<RequirementQualitySummary | null>(null)
  const [reqLoading, setReqLoading] = useState(false)
  const [expandedId, setExpandedId] = useState<string | undefined>()  // 单行展开(手风琴)
  useEffect(() => { if (!isAdmin) setActiveTab('my') }, [isAdmin])

  const BRAND = '#D97757'  // brand-solid 橙

  const effectiveProjectId = activeTab === 'my' ? (projectFilter ?? currentProject?.id) : projectFilter

  useEffect(() => { projectsApi.list().then((r) => setAllProjects(r.data)) }, [])
  useEffect(() => { enumsApi.list('severity').then((r) => setSevLevels(r.data.map((x: any) => ({ key: x.key, label: x.label })))) }, [])

  useEffect(() => {
    if (activeTab === 'my' && !effectiveProjectId) return
    dashboardApi.requirementsQuality({ project_id: effectiveProjectId }).then((r) => {
      const iters = [...new Set(r.data.requirements.map((req: any) => req.iteration).filter(Boolean))] as string[]
      setIterationOptions(iters)
      const ownerSet = new Set<string>()
      r.data.requirements.forEach((req: any) => {
        if (req.owner_name) ownerSet.add(req.owner_name)
        ;(req.slices || []).forEach((s: any) => { if (s.owner_name) ownerSet.add(s.owner_name) })
      })
      setOwnerOptions([...ownerSet])
    })
  }, [effectiveProjectId]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (activeTab === 'my' && !effectiveProjectId) return
    setReqLoading(true)
    dashboardApi.requirementsQuality({ project_id: effectiveProjectId, iteration: iterationFilter, status: statusFilter, owner: ownerFilter })
      .then((r) => { setReqQuality(r.data.requirements || []); setQualitySummary(r.data.summary || null) })
      .finally(() => setReqLoading(false))
  }, [effectiveProjectId, iterationFilter, statusFilter, ownerFilter]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!currentProject && activeTab === 'my' && !projectFilter) {
    return <Empty description="请先选择项目" style={{ marginTop: 80 }} />
  }

  const qs = qualitySummary
  const totalPassed = reqQuality.reduce((a, r) => a + r.passed, 0)
  const totalSkipped = reqQuality.reduce((a, r) => a + r.skipped, 0)
  const totalCases = reqQuality.reduce((a, r) => a + r.total_cases, 0)
  const testProgressPct = totalCases > 0 ? Math.round(((totalPassed + totalSkipped) / totalCases) * 100) : 0
  const testProgressOf = (r: RequirementQuality) => r.total_cases > 0 ? ((r.passed + r.skipped) / r.total_cases) * 100 : 0
  const sortedReqQuality = [...reqQuality].sort((a, b) => testProgressOf(a) - testProgressOf(b))

  const sevBreakdown = qs?.severity_breakdown || {}
  const defectByLevel = sevLevels.map((lv, i) => ({
    key: lv.key, label: lv.label,
    total: sevBreakdown[lv.key]?.total ?? 0,
    open: sevBreakdown[lv.key]?.open ?? 0,
    color: LEVEL_COLORS[i % LEVEL_COLORS.length],
  }))
  const maxDefectTotal = Math.max(1, ...defectByLevel.map((d) => d.total))
  const defectFixByReqData = reqQuality
    .filter((r) => r.total_defects > 0)
    .map((r) => ({ id: r.id, title: r.title, fixed: r.fixed_defects, total: r.total_defects, pct: Math.round((r.fixed_defects / r.total_defects) * 100) }))
    .sort((a, b) => a.pct - b.pct).slice(0, 8)

  const completionPct = qs && qs.total_requirements > 0 ? Math.round((qs.done_requirements / qs.total_requirements) * 100) : 0
  const fixPct = qs && qs.total_defects > 0 ? Math.round((qs.fixed_defects / qs.total_defects) * 100) : 0

  const reqColumns = [
    {
      title: '需求', dataIndex: 'title', key: 'title', ellipsis: false,
      render: (v: string, row: any) => {
        if (row.__sub) {
          return (
            <span style={{ display: 'flex', alignItems: 'center', gap: 10, whiteSpace: 'normal' }}>
              <span style={{ width: 3, height: 18, borderRadius: 2, background: BRAND, flex: 'none' }} />
              <span style={{ fontSize: 13, fontWeight: 500, color: '#334155', paddingLeft: 4 }}>{row.scope_label}</span>
              {row.owner_name && <><span style={{ color: '#D1D9E0' }}>·</span><span style={{ fontSize: 12, color: '#94A3B8' }}>{row.owner_name}</span></>}
            </span>
          )
        }
        const hasSub = (row.slices || []).some((s: any) => !s.is_default)
        const expanded = expandedId === row.id
        return (
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {hasSub ? (
              <span className="ms" onClick={(e) => { e.stopPropagation(); setExpandedId(expanded ? undefined : row.id) }}
                style={{ fontSize: 16, color: expanded ? BRAND : '#B0BAC4', transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform .2s, color .15s', cursor: 'pointer', flex: 'none' }}>chevron_right</span>
            ) : <span style={{ width: 16, flex: 'none' }} />}
            <a className="row-title" onClick={() => navigate(`/requirements/${row.id}`)}>{v}</a>
          </span>
        )
      },
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 96,
      render: (v: string) => {
        const s = pillStyle(v)
        return <span style={{ fontSize: 12, fontWeight: 500, padding: '3px 10px', borderRadius: 7, background: s.bg, color: s.color }}>{REQ_STATUS_LABEL[v] || v}</span>
      },
    },
    {
      title: '测试进度', key: 'test_progress', width: 100,
      render: (_: unknown, row: any) => {
        const pct = row.total_cases === 0 ? 0 : Math.round(((row.passed + row.skipped) / row.total_cases) * 100)
        if (row.__sub) {
          const c = pct === 0 ? '#94A3B8' : pct >= 100 ? '#128A43' : '#E8930C'
          return <span style={{ fontFamily: MONO_FONT, fontSize: 12.5, fontWeight: 600, color: c }}>{pct}%</span>
        }
        const color = pct >= 95 ? '#16A34A' : pct >= 80 ? '#E8930C' : '#EF4444'
        return <span style={{ fontFamily: MONO_FONT, fontWeight: 600, color }}>{pct}%</span>
      },
    },
    {
      title: '缺陷等级', key: 'defects', width: 170,
      render: (_: unknown, row: any) => {
        const so = row.sev_open || {}
        const items = sevLevels.map((lv, i) => ({ lv, i, n: so[lv.key] || 0 })).filter((x) => x.n > 0)
        if (!items.length) return <span style={{ color: '#CBD5E1' }}>—</span>
        return (
          <span style={{ display: 'inline-flex', gap: 6, flexWrap: 'wrap' }}>
            {items.map(({ lv, i, n }) => {
              const t = LEVEL_TINT[i % LEVEL_TINT.length]
              return <span key={lv.key} style={{ fontSize: 11.5, fontWeight: 500, padding: '2px 8px', borderRadius: 6, background: t.bg, color: t.color }}>{shortLevel(lv.label)}×{n}</span>
            })}
          </span>
        )
      },
    },
    {
      title: '修复进度', key: 'fix', width: 200,
      render: (_: unknown, row: any) => {
        if (row.total_defects === 0) return <span style={{ color: '#CBD5E1' }}>—</span>
        const pct = Math.round((row.fixed_defects / row.total_defects) * 100)
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <div style={{ flex: 1, height: row.__sub ? 5 : 6, background: '#EEF2F5', borderRadius: 999, overflow: 'hidden' }}>
              <div style={{ height: '100%', borderRadius: 999, background: '#16A34A', width: `${pct}%` }} />
            </div>
            <span style={{ fontFamily: MONO_FONT, fontSize: 11.5, color: '#94A3B8' }}>{row.fixed_defects}/{row.total_defects}</span>
          </div>
        )
      },
    },
    {
      title: (<span style={{ display: 'inline-flex', alignItems: 'center', gap: 3 }}>可发布<Tooltip title="测试进度100% 且 2级bug数为0 才可发布"><span className="ms" style={{ fontSize: 14, color: '#CBD5E1' }}>help</span></Tooltip></span>),
      dataIndex: 'releasability', key: 'releasability', width: 110,
      render: (v: string, row: any) => row.__sub ? <span style={{ color: '#CBD5E1' }}>—</span> : <GateTag state={toGateState(v)} />,
    },
  ]

  // 扁平化：父需求行 + (展开时)其非全文子范围作为真实子 <tr> 插入
  const flatData: any[] = []
  sortedReqQuality.forEach((r) => {
    flatData.push(r)
    if (expandedId === r.id) {
      (r.slices || []).filter((s) => !s.is_default).forEach((s) => flatData.push({ __sub: true, __key: `${r.id}:${s.id}`, ...s }))
    }
  })

  const tabItems = isAdmin ? [{ key: 'all', label: '全部需求' }, { key: 'my', label: '我的需求' }] : [{ key: 'my', label: '我的需求' }]
  const handleTabChange = (key: string) => { setActiveTab(key as 'my' | 'all'); setProjectFilter(undefined); setIterationFilter(undefined); setStatusFilter(undefined); setOwnerFilter(undefined) }

  // ── 设计稿样式辅助 ──
  const card: React.CSSProperties = { flex: 1, background: '#fff', border: '1px solid #ECEFF2', borderRadius: 14, padding: '16px 18px', boxShadow: '0 1px 2px rgba(16,24,40,.04)' }
  const cardLabel: React.CSSProperties = { fontSize: 12.5, color: '#64748B', marginBottom: 12 }
  const bigNum: React.CSSProperties = { fontFamily: MONO_FONT, fontSize: 30, fontWeight: 600, color: '#0F172A' }
  const filterChip = (label: string, value: string | undefined, onChange: (v?: string) => void, options: { value: string; label: string }[]) => (
    <Select value={value} onChange={onChange} allowClear placeholder={label} variant="outlined"
      style={{ minWidth: 120, height: 36 }} options={options} />
  )

  return (
    <div style={{ padding: '24px 28px 40px', maxWidth: 1480, margin: '0 auto', animation: 'fadeUp .35s ease both' }}>
      {/* tabs + filters */}
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 18, gap: 10, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', gap: 3, background: '#EBEFF3', padding: 3, borderRadius: 11 }}>
          {tabItems.map((t) => {
            const on = activeTab === t.key
            return (
              <div key={t.key} onClick={() => handleTabChange(t.key)}
                style={{ padding: '7px 16px', borderRadius: 8, fontSize: 13, fontWeight: on ? 600 : 500, cursor: 'pointer', color: on ? '#0F172A' : '#64748B', background: on ? '#fff' : 'transparent', boxShadow: on ? '0 1px 2px rgba(16,24,40,.06)' : 'none' }}>
                {t.label}
              </div>
            )
          })}
        </div>
        <div style={{ flex: 1 }} />
        <span className="ms" style={{ fontSize: 18, color: '#94A3B8' }}>filter_list</span>
        {filterChip('项目', projectFilter, setProjectFilter, allProjects.map((p) => ({ value: p.id, label: p.name })))}
        {filterChip('迭代', iterationFilter, setIterationFilter, iterationOptions.map((i) => ({ value: i, label: i })))}
        {filterChip('需求状态', statusFilter, setStatusFilter, [
          { value: 'pending_analysis', label: '待需求分析' }, { value: 'analyzing', label: '分析中' },
          { value: 'pending_case_generation', label: '待生成用例' }, { value: 'generating_cases', label: '生成用例中' },
          { value: 'pending_test', label: '待测试' }, { value: 'testing', label: '测试中' },
          { value: 'done', label: '已完成' },
        ])}
        {filterChip('归属人', ownerFilter, setOwnerFilter, [
          ...ownerOptions.map((o) => ({ value: o, label: o })),
          { value: '__unassigned__', label: '未分配' },
        ])}
      </div>

      <Spin spinning={reqLoading}>
        {/* metric cards */}
        <div style={{ display: 'flex', gap: 14, marginBottom: 18 }}>
          <div style={card}>
            <div style={cardLabel}>需求完成进度</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}><span style={bigNum}>{qs?.done_requirements ?? 0}</span><span style={{ fontFamily: MONO_FONT, fontSize: 15, color: '#94A3B8' }}>/{qs?.total_requirements ?? 0}</span></div>
            <div style={{ height: 5, background: '#EEF2F5', borderRadius: 999, marginTop: 12, overflow: 'hidden' }}><div style={{ height: '100%', width: `${completionPct}%`, borderRadius: 999, background: '#5BA8D4' }} /></div>
            <div style={{ fontSize: 11.5, color: '#94A3B8', marginTop: 7, fontFamily: MONO_FONT }}>{completionPct}%</div>
          </div>

          <div className="tech-stat-card" style={{ ...card, border: '1px solid #F5D6C8', boxShadow: '0 4px 14px -8px rgba(217,119,87,.32)' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', position: 'relative' }}>
              <div>
                <div style={cardLabel}>测试进度</div>
                <div style={{ fontFamily: MONO_FONT, fontSize: 30, fontWeight: 600, color: testProgressPct >= 95 ? '#16A34A' : testProgressPct >= 80 ? '#E8930C' : '#D97757' }}>{testProgressPct}%</div>
                <div style={{ fontSize: 11.5, color: '#94A3B8', marginTop: 7 }}>已测 {totalPassed + totalSkipped} / {totalCases}</div>
              </div>
              <div style={{ width: 62, height: 62, borderRadius: '50%', background: `conic-gradient(#D97757 ${testProgressPct * 3.6}deg, #FDEAE0 0deg)`, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <div style={{ width: 46, height: 46, borderRadius: '50%', background: '#fff', display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: MONO_FONT, fontSize: 13, fontWeight: 600, color: '#D97757' }}>{testProgressPct}</div>
              </div>
            </div>
          </div>

          <div style={card}>
            <div style={cardLabel}>缺陷总数</div>
            <div style={bigNum}>{qs?.total_defects ?? 0}</div>
            <div style={{ display: 'flex', gap: 13, marginTop: 13, fontSize: 11.5, color: '#64748B', flexWrap: 'wrap' }}>
              {defectByLevel.map((d) => (
                <span key={d.key} style={{ display: 'inline-flex', alignItems: 'center', gap: 5 }}><span style={{ width: 7, height: 7, borderRadius: '50%', background: d.color }} />{shortLevel(d.label)} {d.open}</span>
              ))}
            </div>
          </div>

          <div style={card}>
            <div style={cardLabel}>缺陷修复进度</div>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 4 }}><span style={bigNum}>{qs?.fixed_defects ?? 0}</span><span style={{ fontFamily: MONO_FONT, fontSize: 15, color: '#94A3B8' }}>/{qs?.total_defects ?? 0}</span></div>
            <div style={{ height: 5, background: '#EEF2F5', borderRadius: 999, marginTop: 12, overflow: 'hidden' }}><div style={{ height: '100%', width: `${fixPct}%`, borderRadius: 999, background: '#16A34A' }} /></div>
            <div style={{ fontSize: 11.5, color: '#94A3B8', marginTop: 7 }}>已修复 <span style={{ fontFamily: MONO_FONT, color: '#16A34A' }}>{fixPct}%</span></div>
          </div>

          <div style={card}>
            <div style={cardLabel}>阻塞中需求</div>
            <div style={{ fontFamily: MONO_FONT, fontSize: 30, fontWeight: 600, color: (qs?.blocked_requirements ?? 0) > 0 ? '#EF4444' : '#16A34A' }}>{qs?.blocked_requirements ?? 0}</div>
            {(qs?.blocked_requirements ?? 0) > 0 && (
              <div style={{ display: 'inline-flex', alignItems: 'center', gap: 5, marginTop: 13, fontSize: 11.5, color: '#C9332B', background: '#FDECEC', border: '1px solid #F7C9C9', padding: '3px 9px', borderRadius: 999 }}><span className="ms" style={{ fontSize: 14 }}>priority_high</span>需关注</div>
            )}
          </div>
        </div>

        {/* requirements table */}
        <style>{`
          .slice-tbl .ant-table-tbody > tr > td, .slice-tbl .ant-table-thead > tr > th { white-space: nowrap; }
          .slice-tbl .ant-table-tbody > tr > td:first-child, .slice-tbl .ant-table-thead > tr > th:first-child { white-space: normal; }
          .slice-tbl tr.slice-sub > td { background: #FAFBFC; border-top: 1px solid #F0F4F6; }
          .slice-tbl tr.slice-sub:hover > td { background: #F3F6F8 !important; }
        `}</style>
        <div style={{ background: '#fff', border: '1px solid #ECEFF2', borderRadius: 14, boxShadow: '0 1px 2px rgba(16,24,40,.04)', overflow: 'hidden' }}>
          <Table className="tech-table slice-tbl" dataSource={flatData} columns={reqColumns} loading={reqLoading} size="middle"
            rowKey={(rec: any) => rec.__sub ? rec.__key : rec.id}
            rowClassName={(rec: any) => rec.__sub ? 'slice-sub' : ''}
            pagination={{ defaultPageSize: 10, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: () => `共 ${sortedReqQuality.length} 条` }} locale={{ emptyText: '暂无需求数据' }} tableLayout="fixed" />
        </div>

        {/* bottom panels */}
        <div style={{ display: 'flex', gap: 14, marginTop: 18 }}>
          <div style={{ flex: 1, ...PANEL_CARD_STYLE, background: '#fff', padding: '20px 22px' }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: '#0F172A', marginBottom: 18 }}>缺陷分布 <span style={{ fontSize: 12, fontWeight: 400, color: '#94A3B8', marginLeft: 8 }}>按严重程度</span></div>
            {defectByLevel.every((d) => d.total === 0) ? <Empty description="暂无缺陷数据" style={{ padding: '12px 0' }} /> : defectByLevel.map((d) => (
              <div key={d.key} style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 15 }}>
                <div style={{ width: 96, display: 'flex', alignItems: 'center', gap: 7, fontSize: 12.5, color: '#475569' }}><span style={{ width: 8, height: 8, borderRadius: '50%', background: d.color, flex: 'none' }} />{d.label}</div>
                <div style={{ flex: 1, height: 8, background: '#EEF2F5', borderRadius: 999, overflow: 'hidden' }}><div style={{ height: '100%', borderRadius: 999, background: d.color, width: `${(d.total / maxDefectTotal) * 100}%` }} /></div>
                <div style={{ width: 90, textAlign: 'right', fontSize: 11.5, color: '#94A3B8', fontFamily: MONO_FONT }}>{d.total} · {d.open} 未关闭</div>
              </div>
            ))}
          </div>
          <div style={{ flex: 1, ...PANEL_CARD_STYLE, background: '#fff', padding: '20px 22px' }}>
            <div style={{ fontSize: 14, fontWeight: 700, color: '#0F172A', marginBottom: 18 }}>缺陷修复进度 <span style={{ fontSize: 12, fontWeight: 400, color: '#94A3B8', marginLeft: 8 }}>按需求</span></div>
            {defectFixByReqData.length === 0 ? <Empty description="暂无缺陷数据" style={{ padding: '12px 0' }} /> : defectFixByReqData.map((d) => (
              <div key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 14, marginBottom: 15 }}>
                <div style={{ width: 150, fontSize: 12.5, color: '#475569', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{d.title}</div>
                <div style={{ flex: 1, height: 8, background: '#EEF2F5', borderRadius: 999, overflow: 'hidden' }}><div style={{ height: '100%', borderRadius: 999, background: '#16A34A', width: `${d.pct}%` }} /></div>
                <div style={{ width: 36, textAlign: 'right', fontSize: 11.5, color: '#94A3B8', fontFamily: MONO_FONT }}>{d.fixed}/{d.total}</div>
              </div>
            ))}
          </div>
        </div>
      </Spin>
    </div>
  )
}
