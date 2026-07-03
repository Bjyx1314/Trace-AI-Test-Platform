import { useEffect, useState } from 'react'
import { Table, Tag, Typography, Progress, Card, Input, Empty, Badge } from 'antd'
import { SearchOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import GateTag from '../components/GateTag'
import { MONO_FONT } from '../styles/theme'
import { executionsApi, type RequirementExecutionOverview } from '../api'
import { useProjectStore } from '../store/projectStore'
import { PANEL_CARD_STYLE } from '../styles/theme'
import dayjs from 'dayjs'

const REQ_STATUS_COLOR: Record<string, string> = {
  pending_analysis: 'default', analyzed: 'processing', testing: 'warning',
  completed: 'success', done: 'success',
}

export default function ExecutionHistory() {
  const currentProject = useProjectStore((s) => s.currentProject)
  const navigate = useNavigate()
  const [data, setData] = useState<RequirementExecutionOverview[]>([])
  const [loading, setLoading] = useState(false)
  const [keyword, setKeyword] = useState('')

  const load = () => {
    setLoading(true)
    executionsApi.requirementOverview(currentProject?.id)
      .then((r) => setData(r.data))
      .finally(() => setLoading(false))
  }

  useEffect(load, [currentProject?.id])

  const filtered = keyword.trim()
    ? data.filter((d) => d.title.toLowerCase().includes(keyword.trim().toLowerCase()))
    : data

  const columns = [
    {
      title: '需求',
      dataIndex: 'title',
      key: 'title',
      width: 360,
      ellipsis: true,
      render: (v: string, row: RequirementExecutionOverview) => (
        <a className="row-title" onClick={() => navigate(`/requirements/${row.requirement_id}`)}>{v}</a>
      ),
    },
    {
      title: '执行次数',
      dataIndex: 'execution_count',
      key: 'execution_count',
      width: 130,
      align: 'center' as const,
      render: (v: number) => <Badge count={v} showZero color={v > 0 ? '#52c41a' : '#d9d9d9'} />,
    },
    {
      title: '最近执行',
      key: 'last_time',
      width: 190,
      render: (_: unknown, row: RequirementExecutionOverview) =>
        row.last_execution?.created_at
          ? dayjs(row.last_execution.created_at).format('MM-DD HH:mm')
          : <Typography.Text type="secondary">未执行</Typography.Text>,
    },
    {
      title: '最近通过率',
      key: 'last_pass_rate',
      width: 280,
      render: (_: unknown, row: RequirementExecutionOverview) => {
        if (!row.last_execution) return <Typography.Text type="secondary">—</Typography.Text>
        const rate = row.last_execution.pass_rate ?? 0
        // 保留 2 位小数展示：避免 0.x% 被取整成 0%（去掉多余的尾随 0，如 100.00→100、33.30→33.3）
        const rateLabel = Number(rate.toFixed(2)).toString()
        const passBarColor = rate >= 100 ? '#16A34A' : rate >= 75 ? '#52A87A' : rate >= 50 ? '#E8930C' : '#EF4444'
        return (
          <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
            <div style={{ flex: 1, height: 6, background: '#EEF2F5', borderRadius: 999, overflow: 'hidden' }}>
              <div style={{ height: '100%', borderRadius: 999, background: passBarColor, width: `${Math.min(100, rate)}%` }} />
            </div>
            <span style={{ fontFamily: MONO_FONT, fontSize: 11.5, color: passBarColor, minWidth: 44, textAlign: 'right' }}>{rateLabel}%</span>
          </div>
        )
      },
    },
    {
      title: '门禁',
      key: 'gate',
      width: 140,
      render: (_: unknown, row: RequirementExecutionOverview) => {
        const g = row.last_execution?.ci_gate_result
        return <GateTag state={g ? (g.releasable ? 'pass' : 'block') : 'none'} />
      },
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <Card
        bordered={false}
        style={PANEL_CARD_STYLE}
        extra={
          <Input
            allowClear
            prefix={<SearchOutlined />}
            placeholder="搜索需求名称"
            value={keyword}
            onChange={(e) => setKeyword(e.target.value)}
            style={{ width: 240 }}
          />
        }
      >
        <div style={{ marginBottom: 12, color: '#666', fontSize: 13 }}>
          按需求汇总执行情况：每个需求的累计执行次数、最近一次执行的通过率与门禁结论。点击需求名进入详情查看完整执行记录。
        </div>
        {!currentProject ? (
          <Empty description="请先在右上角选择项目" />
        ) : (
          <Table
            rowKey="requirement_id"
            dataSource={filtered}
            columns={columns}
            loading={loading}
            size="small"
            pagination={{ defaultPageSize: 15, showSizeChanger: true, pageSizeOptions: [15, 30, 50, 100], showTotal: (t) => `共 ${t} 个需求` }}
            locale={{ emptyText: keyword ? '没有匹配的需求' : '暂无需求执行数据' }}
          />
        )}
      </Card>
    </div>
  )
}
