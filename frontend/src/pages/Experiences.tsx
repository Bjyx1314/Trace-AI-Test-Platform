import { useEffect, useState } from 'react'
import { Card, Table, Tag, Select, Space, Button, message, Progress, Popconfirm } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useProjectStore } from '../store/projectStore'
import { experiencesApi } from '../api'
import type { Experience } from '../types/api'

const STATUS_COLOR: Record<string, string> = { candidate: 'default', active: 'success', dormant: 'warning', stale: 'error' }
const STATUS_LABEL: Record<string, string> = { candidate: '候选', active: '生效', dormant: '休眠', stale: '失效' }
const SOURCE_LABEL: Record<string, string> = { tester_feedback: '测试反馈', found_bug: '缺陷', production_issue: '线上问题', high_value_case: '高价值用例' }

export default function Experiences() {
  const { currentProject } = useProjectStore()
  const [rows, setRows] = useState<Experience[]>([])
  const [loading, setLoading] = useState(false)
  const [status, setStatus] = useState<string | undefined>()

  const load = () => {
    setLoading(true)
    experiencesApi.list({ project_id: currentProject?.id, status, limit: 200 })
      .then((r) => setRows(r.data)).finally(() => setLoading(false))
  }
  useEffect(load, [currentProject, status])

  const setExpStatus = async (id: string, s: string) => {
    await experiencesApi.update(id, { status: s }); message.success('已更新'); load()
  }
  const runMerge = async () => {
    const r = await experiencesApi.runMerge(currentProject?.id)
    message.success(`合并 ${r.data.merged} 条近重经验`); load()
  }

  const columns: ColumnsType<Experience> = [
    { title: '经验', dataIndex: 'title', key: 'title', render: (t, r) => (
      <div><div style={{ fontWeight: 600 }}>{t}</div>{r.reason && <div style={{ fontSize: 12, color: '#999' }}>{r.reason}</div>}</div>
    ) },
    { title: '来源', dataIndex: 'source', key: 'source', width: 100, render: (s) => SOURCE_LABEL[s] || s },
    { title: '触发标签', key: 'tags', render: (_, r) => (r.trigger_context?.risk_tags || []).map((t) => <Tag key={t}>{t}</Tag>) },
    { title: '建议覆盖项', key: 'items', render: (_, r) => (r.suggested_covered_items || []).map((s) => <Tag key={s} color="green">{s}</Tag>) },
    { title: '置信度', dataIndex: 'confidence', key: 'confidence', width: 130, render: (c) => <Progress percent={Math.round(c * 100)} size="small" /> },
    { title: '采纳/忽略', key: 'stats', width: 100, render: (_, r) => `${r.stats?.adopt_count ?? 0} / ${r.stats?.reject_count ?? 0}` },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90, render: (s) => <Tag color={STATUS_COLOR[s]}>{STATUS_LABEL[s] || s}</Tag> },
    { title: '操作', key: 'op', width: 160, render: (_, r) => (
      <Space>
        {r.status !== 'active' && <Button size="small" onClick={() => setExpStatus(r.experience_id, 'active')}>启用</Button>}
        {r.status !== 'dormant' && <Popconfirm title="休眠此经验？" onConfirm={() => setExpStatus(r.experience_id, 'dormant')}><Button size="small">休眠</Button></Popconfirm>}
      </Space>
    ) },
  ]

  return (
    <div style={{ padding: 20 }}>
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space>
          <span>状态：</span>
          <Select allowClear placeholder="全部" style={{ width: 140 }} value={status} onChange={setStatus}
            options={Object.entries(STATUS_LABEL).map(([v, l]) => ({ label: l, value: v }))} />
          <Button onClick={runMerge}>合并近重经验</Button>
        </Space>
      </Card>
      <Table rowKey="experience_id" loading={loading} columns={columns} dataSource={rows} size="small" pagination={{ pageSize: 20 }} />
    </div>
  )
}
