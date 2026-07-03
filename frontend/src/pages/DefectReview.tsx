import { useEffect, useState } from 'react'
import { Typography, Select, Empty, Card, Space } from 'antd'
import { defectsApi, enumsApi, requirementsApi } from '../api'
import { useProjectStore } from '../store/projectStore'
import { PANEL_CARD_STYLE } from '../styles/theme'
import DefectReviewTable from '../components/DefectReviewTable'

const STATUS_OPTIONS = [
  { value: 'draft', label: '待复核' },
  { value: 'confirmed', label: '待处理' },
  { value: 'fixed', label: '已解决' },
  { value: 'resolved', label: '无需处理' },
]

export default function DefectReview() {
  const currentProject = useProjectStore((s) => s.currentProject)
  const [data, setData] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [severityOptions, setSeverityOptions] = useState<{ key: string; label: string }[]>([])
  const [reqOptions, setReqOptions] = useState<{ id: string; title: string }[]>([])

  const [filterStatus, setFilterStatus] = useState<string | undefined>('draft')
  const [filterSeverity, setFilterSeverity] = useState<string | undefined>()
  const [filterReq, setFilterReq] = useState<string | undefined>()

  // 枚举「缺陷等级」+ 当前项目需求(供筛选 & 严重程度下拉)
  useEffect(() => {
    enumsApi.list('severity').then((r) => setSeverityOptions(r.data))
  }, [])
  useEffect(() => {
    if (!currentProject) return
    requirementsApi.list({ project_id: currentProject.id }).then((r) =>
      setReqOptions(r.data.map((x: any) => ({ id: x.id, title: x.title }))))
  }, [currentProject?.id])

  const load = async () => {
    if (!currentProject) return
    setLoading(true)
    try {
      const r = await defectsApi.list({
        project_id: currentProject.id,
        status: filterStatus,
        severity: filterSeverity,
        requirement_id: filterReq,
      })
      setData(r.data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [currentProject?.id, filterStatus, filterSeverity, filterReq])

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16, flexWrap: 'wrap', gap: 8 }}>
        <div />
        <Space wrap>
          <Select value={filterStatus} onChange={setFilterStatus} allowClear placeholder="状态"
            style={{ width: 130 }} options={STATUS_OPTIONS} />
          <Select value={filterSeverity} onChange={setFilterSeverity} allowClear placeholder="缺陷等级"
            style={{ width: 140 }} options={severityOptions.map((o) => ({ value: o.key, label: o.label }))} />
          <Select value={filterReq} onChange={setFilterReq} allowClear showSearch placeholder="关联需求"
            style={{ width: 220 }} optionFilterProp="label"
            options={reqOptions.map((o) => ({ value: o.id, label: o.title }))} />
        </Space>
      </div>

      {!currentProject ? (
        <Empty description="请先在右上角选择项目" style={{ marginTop: 80 }} />
      ) : (
        <Card bordered={false} style={PANEL_CARD_STYLE}>
          <DefectReviewTable defects={data} loading={loading} severityOptions={severityOptions} onChanged={load} />
        </Card>
      )}
    </div>
  )
}
