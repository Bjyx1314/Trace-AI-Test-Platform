import { useEffect, useState } from 'react'
import { Card, Table, Tag, Button, Modal, Form, Input, Select, Switch, Space, message, Popconfirm } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { qualityRulesApi, type QualityRule } from '../api'

export default function QualityRules() {
  const [rows, setRows] = useState<QualityRule[]>([])
  const [loading, setLoading] = useState(false)
  const [editing, setEditing] = useState<QualityRule | null>(null)
  const [open, setOpen] = useState(false)
  const [form] = Form.useForm()

  const load = () => { setLoading(true); qualityRulesApi.list().then((r) => setRows(r.data)).finally(() => setLoading(false)) }
  useEffect(load, [])

  const openEdit = (r?: QualityRule) => {
    setEditing(r || null)
    form.setFieldsValue(r
      ? { ...r, required_covered_items: (r.required_covered_items || []).join('\n') }
      : { name: '', match_tags: [], min_priority: undefined, required_covered_items: '', active: true })
    setOpen(true)
  }
  const save = async () => {
    const v = await form.validateFields()
    const payload = { ...v, required_covered_items: (v.required_covered_items || '').split('\n').map((s: string) => s.trim()).filter(Boolean) }
    if (editing) await qualityRulesApi.update(editing.id, payload)
    else await qualityRulesApi.create(payload)
    message.success('已保存'); setOpen(false); load()
  }
  const toggle = async (r: QualityRule) => { await qualityRulesApi.update(r.id, { ...r, active: !r.active }); load() }

  const columns: ColumnsType<QualityRule> = [
    { title: 'ID', dataIndex: 'id', key: 'id', width: 90 },
    { title: '规则', dataIndex: 'name', key: 'name' },
    { title: '匹配标签', dataIndex: 'match_tags', key: 'match_tags', render: (t: string[]) => (t || []).map((x) => <Tag key={x}>{x}</Tag>) },
    { title: '优先级下限', dataIndex: 'min_priority', key: 'min_priority', width: 100, render: (p) => p ? <Tag color={p === 'P0' ? 'red' : 'orange'}>{p}</Tag> : '—' },
    { title: '必测项', dataIndex: 'required_covered_items', key: 'req', render: (t: string[]) => (t || []).map((x) => <Tag key={x} color="green">{x}</Tag>) },
    { title: '状态', dataIndex: 'active', key: 'active', width: 90, render: (a, r) => <Switch checked={a} size="small" onChange={() => toggle(r)} /> },
    { title: '操作', key: 'op', width: 130, render: (_, r) => (
      <Space>
        <Button size="small" onClick={() => openEdit(r)}>编辑</Button>
        <Popconfirm title="删除该规则？" onConfirm={async () => { await qualityRulesApi.remove(r.id); load() }}><Button size="small" danger>删除</Button></Popconfirm>
      </Space>
    ) },
  ]

  return (
    <div style={{ padding: 20 }}>
      <Card size="small" style={{ marginBottom: 12 }} extra={<Button type="primary" onClick={() => openEdit()}>新增规则</Button>}
        title="硬规则引擎（命中即抬优先级 + 强制必测项，命中留痕见覆盖矩阵）">
        <Table rowKey="id" loading={loading} columns={columns} dataSource={rows} size="small" pagination={false} />
      </Card>
      <Modal title={editing ? `编辑规则 ${editing.id}` : '新增规则'} open={open} onOk={save} onCancel={() => setOpen(false)} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="规则名称" rules={[{ required: true }]}><Input /></Form.Item>
          <Form.Item name="match_tags" label="匹配风险标签（命中任一即触发）"><Select mode="tags" placeholder="如 支付、金额、认证" /></Form.Item>
          <Form.Item name="min_priority" label="命中后优先级下限"><Select allowClear options={[{ value: 'P0' }, { value: 'P1' }]} /></Form.Item>
          <Form.Item name="required_covered_items" label="强制必测覆盖项（每行一条）"><Input.TextArea rows={4} /></Form.Item>
          <Form.Item name="active" label="启用" valuePropName="checked"><Switch /></Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
