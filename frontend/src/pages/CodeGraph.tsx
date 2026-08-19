import { useEffect, useState } from 'react'
import { Card, Table, Tag, Input, Select, Space, Button, Statistic, Row, Col, Drawer, message } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { graphApi } from '../api'
import type { GraphNode, GraphExpandResponse } from '../types/api'

const TYPE_COLOR: Record<string, string> = { page: 'blue', api: 'purple', service: 'geekblue', component: 'cyan', file: 'default', db: 'gold', mq: 'volcano' }

export default function CodeGraph() {
  const [nodes, setNodes] = useState<GraphNode[]>([])
  const [loading, setLoading] = useState(false)
  const [nodeType, setNodeType] = useState<string | undefined>()
  const [q, setQ] = useState('')
  const [stats, setStats] = useState<{ nodes: number; edges: number; by_type: Record<string, number> } | null>(null)
  const [expand, setExpand] = useState<GraphExpandResponse | null>(null)

  const load = () => {
    setLoading(true)
    graphApi.nodes({ node_type: nodeType, q: q || undefined, limit: 300 }).then((r) => setNodes(r.data)).finally(() => setLoading(false))
  }
  useEffect(() => { load() }, [nodeType])
  useEffect(() => { graphApi.stats().then((r) => setStats(r.data)) }, [])

  const doExpand = async (node: string) => {
    const r = await graphApi.expand(node, 2)
    setExpand(r.data)
  }
  const seedPages = async () => {
    const r = await graphApi.seedPages()
    message.success(`页面种子导入 ${r.data.seeded} 个`); load(); graphApi.stats().then((s) => setStats(s.data))
  }

  const columns: ColumnsType<GraphNode> = [
    { title: '类型', dataIndex: 'node_type', key: 'node_type', width: 100, render: (t) => <Tag color={TYPE_COLOR[t] || 'default'}>{t}</Tag> },
    { title: '名称', dataIndex: 'name', key: 'name' },
    { title: '节点ID', dataIndex: 'node_id', key: 'node_id', render: (v) => <span style={{ fontFamily: 'monospace', fontSize: 12, color: '#888' }}>{v}</span> },
    { title: '仓库', dataIndex: 'repo', key: 'repo', width: 120 },
    { title: '操作', key: 'op', width: 100, render: (_, r) => <Button size="small" onClick={() => doExpand(r.node_id)}>扩散影响</Button> },
  ]

  return (
    <div style={{ padding: 20 }}>
      {stats && (
        <Row gutter={12} style={{ marginBottom: 16 }}>
          <Col span={6}><Card size="small"><Statistic title="节点总数" value={stats.nodes} /></Card></Col>
          <Col span={6}><Card size="small"><Statistic title="边总数" value={stats.edges} /></Card></Col>
          <Col span={12}><Card size="small"><Space wrap>{Object.entries(stats.by_type).map(([t, c]) => <Tag key={t} color={TYPE_COLOR[t] || 'default'}>{t}: {c}</Tag>)}</Space></Card></Col>
        </Row>
      )}
      <Card size="small" style={{ marginBottom: 12 }}>
        <Space wrap>
          <Select allowClear placeholder="节点类型" style={{ width: 140 }} value={nodeType} onChange={setNodeType}
            options={['page', 'api', 'service', 'component', 'file', 'db', 'mq'].map((v) => ({ label: v, value: v }))} />
          <Input.Search placeholder="按名称搜索" style={{ width: 240 }} value={q} onChange={(e) => setQ(e.target.value)} onSearch={load} />
          <Button onClick={seedPages}>从页面缓存导入 Page 节点</Button>
        </Space>
      </Card>
      <Table rowKey="node_id" loading={loading} columns={columns} dataSource={nodes} size="small" pagination={{ pageSize: 20 }} />

      <Drawer title="影响面扩散（证据链）" open={!!expand} onClose={() => setExpand(null)} width={560}>
        {expand && (
          <Space direction="vertical" style={{ width: '100%' }}>
            <div style={{ color: '#888' }}>种子：{expand.seeds.join('、')}</div>
            {expand.nodes.map((n, i) => (
              <div key={i} style={{ borderBottom: '1px solid #f0f0f0', paddingBottom: 6 }}>
                <Tag color={TYPE_COLOR[n.node_type || ''] || 'default'}>{n.node_type}</Tag>{n.name}
                <span style={{ color: '#aaa', marginLeft: 6 }}>置信 {n.path_conf}</span>
                {(n.evidence_chain || []).map((c, j) => <div key={j} style={{ fontSize: 12, color: '#999', marginLeft: 8 }}>{c}</div>)}
              </div>
            ))}
            {expand.truncated.length > 0 && <div style={{ color: '#faad14' }}>{expand.truncated.length} 个分支未追踪</div>}
          </Space>
        )}
      </Drawer>
    </div>
  )
}
