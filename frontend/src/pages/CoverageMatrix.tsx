import { useEffect, useMemo, useState } from 'react'
import { useSearchParams, useNavigate } from 'react-router-dom'
import { Card, Select, Table, Tag, Space, Statistic, Row, Col, Empty, Drawer, Image, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useProjectStore } from '../store/projectStore'
import { requirementsApi, coverageMatrixApi } from '../api'
import type { Requirement, CoverageMatrixResponse, CoverageMatrixRow, CheckedPoint } from '../types/api'
import { sourceLabel, sourceColor, COVERAGE_STATUS_COLOR, COVERAGE_STATUS_LABEL, RISK_LEVEL_LABEL, RISK_LEVEL_COLOR } from '../constants/coveredItem'

const PRIORITY_COLOR: Record<string, string> = { P0: 'red', P1: 'orange', P2: 'default' }

// embedded=true 时收拢进「需求详情」的覆盖矩阵 tab：外部传入 requirementId，隐藏顶部需求下拉与外层留白
export default function CoverageMatrix({ requirementId, embedded }: { requirementId?: string; embedded?: boolean } = {}) {
  const { currentProject } = useProjectStore()
  const [searchParams, setSearchParams] = useSearchParams()
  const navigate = useNavigate()
  const [reqs, setReqs] = useState<Requirement[]>([])
  const [reqId, setReqId] = useState<string | undefined>(requirementId || searchParams.get('requirement_id') || undefined)
  const [data, setData] = useState<CoverageMatrixResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [evidence, setEvidence] = useState<CheckedPoint[] | null>(null)

  // 内嵌模式下随外部需求切换
  useEffect(() => { if (requirementId) setReqId(requirementId) }, [requirementId])

  useEffect(() => {
    if (embedded) return // 内嵌不需要需求下拉列表
    requirementsApi.list(currentProject ? { project_id: currentProject.id } : undefined)
      .then((r) => setReqs(r.data))
  }, [currentProject, embedded])

  useEffect(() => {
    if (!reqId) { setData(null); return }
    setLoading(true)
    coverageMatrixApi.byRequirement(reqId)
      .then((r) => setData(r.data))
      .finally(() => setLoading(false))
  }, [reqId])

  const onSelectReq = (v?: string) => {
    setReqId(v)
    setSearchParams(v ? { requirement_id: v } : {})
  }

  const columns: ColumnsType<CoverageMatrixRow> = useMemo(() => [
    {
      title: '覆盖项', dataIndex: 'name', key: 'name',
      render: (name: string, row) => (
        <div>
          <div style={{ fontWeight: 600 }}>{name}</div>
          {(row.risk_tags?.length ?? 0) > 0 && (
            <div style={{ marginTop: 2 }}>{row.risk_tags!.map((t) => <Tag key={t} style={{ transform: 'scale(0.85)' }}>{t}</Tag>)}</div>
          )}
        </div>
      ),
    },
    {
      title: '来源', dataIndex: 'sources', key: 'sources', width: 140,
      filters: [...new Set((data?.rows || []).flatMap((r) => r.sources))].map((s) => ({ text: sourceLabel(s), value: s })),
      onFilter: (val, row) => row.sources.includes(val as any),
      render: (srcs: string[]) => srcs.map((s) => <Tag key={s} color={sourceColor(s)}>{sourceLabel(s)}</Tag>),
    },
    {
      title: '优先级', dataIndex: 'priority', key: 'priority', width: 90,
      filters: ['P0', 'P1', 'P2'].map((p) => ({ text: p, value: p })),
      onFilter: (val, row) => row.priority === val,
      render: (p?: string) => p ? <Tag color={PRIORITY_COLOR[p]}>{p}</Tag> : '—',
    },
    {
      title: '命中规则', dataIndex: 'matched_rules', key: 'matched_rules', width: 110,
      render: (rules?: string[]) => (rules?.length ? rules.map((r) => <Tag key={r} color="red">{r}</Tag>) : '—'),
    },
    {
      title: '关联用例', dataIndex: 'related_cases', key: 'related_cases',
      render: (cases: CoverageMatrixRow['related_cases']) => (
        <Space direction="vertical" size={0}>
          {cases.map((c) => (
            <a key={c.id} onClick={() => navigate('/testcases/list')}>{c.case_id} {c.title.slice(0, 20)}</a>
          ))}
          {cases.length === 0 && <span style={{ color: '#bbb' }}>无</span>}
        </Space>
      ),
    },
    {
      title: '执行状态', dataIndex: 'coverage_status', key: 'coverage_status', width: 100,
      filters: [
        { text: '已验证', value: 'covered' }, { text: '失败', value: 'failed' }, { text: '未覆盖', value: 'not_covered' },
      ],
      onFilter: (val, row) => row.coverage_status === val,
      render: (st: CoverageMatrixRow['coverage_status']) => <Tag color={COVERAGE_STATUS_COLOR[st]}>{COVERAGE_STATUS_LABEL[st]}</Tag>,
    },
    {
      title: '证据', key: 'evidence', width: 80,
      render: (_: unknown, row) => (row.evidence?.length ? <a onClick={() => setEvidence(row.evidence)}>查看</a> : '—'),
    },
    {
      title: '风险', dataIndex: 'risk_level', key: 'risk_level', width: 80,
      render: (lv?: string) => lv ? <Tag color={RISK_LEVEL_COLOR[lv]}>{RISK_LEVEL_LABEL[lv]}</Tag> : '—',
    },
  ], [data, navigate])

  const s = data?.summary

  return (
    <div style={{ padding: embedded ? 0 : 20 }}>
      {!embedded && (
        <Card size="small" style={{ marginBottom: 16 }}>
          <Space>
            <span>需求：</span>
            <Select
              style={{ width: 380 }} allowClear showSearch optionFilterProp="label"
              placeholder="选择需求查看覆盖矩阵" value={reqId} onChange={onSelectReq}
              options={reqs.map((r) => ({ label: `${r.title}`, value: r.id }))}
            />
          </Space>
        </Card>
      )}

      {s && (
        <Row gutter={12} style={{ marginBottom: 16 }}>
          <Col span={5}><Card size="small"><Statistic title="覆盖项总数" value={s.total} /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="已验证" value={s.covered} valueStyle={{ color: '#52c41a' }} /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="失败" value={s.failed} valueStyle={{ color: '#f5222d' }} /></Card></Col>
          <Col span={5}><Card size="small"><Statistic title="未覆盖" value={s.not_covered} valueStyle={{ color: '#8c8c8c' }} /></Card></Col>
          <Col span={4}><Card size="small"><Statistic title="验证率" value={(s.verify_rate * 100).toFixed(1)} suffix="%" /></Card></Col>
        </Row>
      )}

      {!reqId ? (
        <Empty description="请选择需求查看覆盖矩阵" />
      ) : (
        <Table
          rowKey={(r) => r.item_id || r.name}
          loading={loading}
          columns={columns}
          dataSource={data?.rows || []}
          size="small"
          pagination={{ pageSize: 20 }}
          locale={{ emptyText: '该需求下暂无覆盖项（用例可能未回填覆盖项）' }}
        />
      )}

      {data?.entry_coverage_matrix && data.entry_coverage_matrix.length > 0 && (
        <Card size="small" title="入口覆盖（待确认区，来自代码影响分析）" style={{ marginTop: 16 }}>
          {data.entry_coverage_matrix.map((e, i) => (
            <div key={i}>
              <Tag color={e.wired === true ? 'success' : e.wired === 'unknown' ? 'warning' : 'default'}>
                {e.wired === true ? '已接线' : e.wired === 'unknown' ? '待确认' : '未接线'}
              </Tag>
              {e.entry} {e.evidence && <Typography.Text type="secondary">— {e.evidence}</Typography.Text>}
            </div>
          ))}
        </Card>
      )}

      <Drawer title="覆盖项验证证据" open={!!evidence} onClose={() => setEvidence(null)} width={480}>
        <Image.PreviewGroup>
          {(evidence || []).map((cp, i) => {
            const stColor = cp.status === 'passed' ? 'success' : cp.status === 'failed' ? 'error' : cp.status === 'blocked' ? 'warning' : 'default'
            const stLabel = cp.status === 'passed' ? '通过' : cp.status === 'failed' ? '失败' : cp.status === 'blocked' ? '阻塞' : '未验证'
            return (
              <div key={i} style={{ display: 'flex', gap: 10, marginBottom: 12, paddingBottom: 12, borderBottom: '1px solid #f0f0f0' }}>
                <div style={{ flex: 1 }}>
                  <Tag color={stColor}>{stLabel}</Tag>
                  {cp.evidence && <div style={{ fontSize: 12, color: '#666', marginTop: 4 }}>{cp.evidence}</div>}
                </div>
                {cp.screenshot_url && <Image src={cp.screenshot_url} width={60} />}
              </div>
            )
          })}
        </Image.PreviewGroup>
      </Drawer>
    </div>
  )
}
