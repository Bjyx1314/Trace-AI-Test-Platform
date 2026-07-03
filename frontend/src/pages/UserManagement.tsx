import { useEffect, useState } from 'react'
import { Table, Tag, message, Card, Button, Modal, Form, Input, Select } from 'antd'
import { confirmDialog } from '../components/ConfirmModal'
import { usersApi } from '../api'
import { PANEL_CARD_STYLE } from '../styles/theme'

export default function UserManagement() {
  const [data, setData] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [createOpen, setCreateOpen] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm()
  const [keyModal, setKeyModal] = useState<{ open: boolean; user?: any }>({ open: false })
  const [keyValue, setKeyValue] = useState('')
  const [savingKey, setSavingKey] = useState(false)

  const load = () => {
    setLoading(true)
    usersApi.list().then((r) => setData(r.data)).finally(() => setLoading(false))
  }

  useEffect(load, [])

  const handleRoleChange = async (userId: string, role: string) => {
    try {
      await usersApi.updateRole(userId, role)
      message.success('角色已更新')
      load()
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '更新失败')
    }
  }

  const handleSetActive = async (userId: string, isActive: boolean) => {
    try {
      await usersApi.setActive(userId, isActive)
      message.success(isActive ? '账号已启用' : '账号已禁用')
      load()
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '操作失败')
    }
  }

  const openKeyModal = (user: any) => { setKeyValue(''); setKeyModal({ open: true, user }) }

  const handleSaveKey = async (clear = false) => {
    const u = keyModal.user
    if (!u) return
    setSavingKey(true)
    try {
      await usersApi.setAiKey(u.id, clear ? null : keyValue.trim())
      message.success(clear ? '已清除该用户的 AI key' : '已保存该用户的 AI key')
      setKeyModal({ open: false })
      load()
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '保存失败')
    } finally {
      setSavingKey(false)
    }
  }

  const handleCreate = async () => {
    const values = await form.validateFields()
    setCreating(true)
    try {
      await usersApi.create(values)
      message.success('账号已创建')
      setCreateOpen(false)
      form.resetFields()
      load()
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '创建失败')
    } finally {
      setCreating(false)
    }
  }

  const columns = [
    { title: '账号', dataIndex: 'username', key: 'username', width: 150, render: (v: string) => v || '—' },
    { title: '姓名', dataIndex: 'name', key: 'name', width: 130, render: (v: string) => v || '—' },
    {
      title: '来源', dataIndex: 'auth_source', key: 'auth_source', width: 120,
      render: (v: string) => <Tag color={v === 'local' ? 'blue' : 'default'}>{v === 'local' ? '本地账号' : '外部 SSO'}</Tag>,
    },
    {
      title: '状态', dataIndex: 'is_active', key: 'is_active', width: 90,
      render: (v: boolean) => <Tag color={v ? 'success' : 'error'}>{v ? '正常' : '已禁用'}</Tag>,
    },
    {
      title: 'AI key', key: 'ai_key', width: 200,
      render: (_: any, row: any) => (
        <div style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          {row.has_ai_key
            ? <Tag color="success" style={{ fontFamily: 'monospace' }}>{row.ai_key_masked}</Tag>
            : <Tag color="error">缺 key</Tag>}
          <Button type="link" size="small" onClick={() => openKeyModal(row)}>
            {row.has_ai_key ? '修改' : '配置'}
          </Button>
        </div>
      ),
    },
    {
      title: '修改角色', key: 'role_action', width: 200,
      render: (_: any, row: any) => (
        <div style={{ display: 'inline-flex', background: '#F1F4F6', borderRadius: 10, padding: 3, gap: 3 }}>
          {([['admin', 'shield_person', '管理员'], ['user', 'person', '普通用户']] as const).map(([val, icon, label]) => {
            const on = row.role === val
            // 选中态：管理员=橙色软胶囊，普通用户=中性白胶囊；未选中=透明灰
            const activeStyle = val === 'admin'
              ? { background: '#FEF3EE', color: '#B5600A', border: '1px solid #F5D6C8', boxShadow: 'none' }
              : { background: '#fff', color: '#475569', border: '1px solid #E7ECF0', boxShadow: '0 1px 3px rgba(16,24,40,.08)' }
            return (
              <div key={val} onClick={() => { if (!on) handleRoleChange(row.id, val) }}
                style={{
                  display: 'inline-flex', alignItems: 'center', gap: 5, padding: '5px 12px', borderRadius: 8,
                  fontSize: 12.5, fontWeight: 600, cursor: on ? 'default' : 'pointer', whiteSpace: 'nowrap',
                  transition: 'all .15s',
                  ...(on ? activeStyle : { background: 'transparent', color: '#94A3B8', border: '1px solid transparent', boxShadow: 'none' }),
                }}>
                <span className="ms" style={{ fontSize: 15 }}>{icon}</span>{label}
              </div>
            )
          })}
        </div>
      ),
    },
    {
      title: '操作', key: 'action', width: 100,
      render: (_: any, row: any) => (
        row.is_active ? (
          <Button type="link" danger size="small"
            onClick={async () => { if (await confirmDialog({ title: '禁用账号', desc: `确认禁用「${row.name || row.username || row.email}」？`, ok: '禁用', danger: true })) handleSetActive(row.id, false) }}>禁用</Button>
        ) : (
          <Button type="link" size="small" onClick={() => handleSetActive(row.id, true)}>启用</Button>
        )
      ),
    },
    {
      title: '加入时间', dataIndex: 'created_at', key: 'created_at',
      render: (v: string) => v ? new Date(v).toLocaleString('zh-CN') : '—',
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', marginBottom: 16 }}>
        <Button type="primary" onClick={() => setCreateOpen(true)}>新增账号</Button>
      </div>
      <Card bordered={false} style={PANEL_CARD_STYLE}>
        <Table rowKey="id" dataSource={data} columns={columns} loading={loading} pagination={false} />
      </Card>

      <Modal
        title={`配置 AI key — ${keyModal.user?.name || keyModal.user?.username || ''}`}
        open={keyModal.open} onCancel={() => setKeyModal({ open: false })}
        footer={[
          keyModal.user?.has_ai_key && <Button key="clear" danger onClick={() => handleSaveKey(true)} loading={savingKey}>清除</Button>,
          <Button key="cancel" onClick={() => setKeyModal({ open: false })}>取消</Button>,
          <Button key="ok" type="primary" loading={savingKey} disabled={!keyValue.trim()} onClick={() => handleSaveKey(false)}>保存</Button>,
        ]}
        destroyOnClose
      >
        <div style={{ fontSize: 12.5, color: '#64748B', marginBottom: 10 }}>
          该用户专属的中转 key（同一中转站，仅 key 不同）。配置后该用户的所有 AI 操作都走自己的 key。
        </div>
        <Input.Password value={keyValue} onChange={(e) => setKeyValue(e.target.value)}
          placeholder={keyModal.user?.has_ai_key ? `当前：${keyModal.user?.ai_key_masked}，输入新 key 覆盖` : '粘贴该用户的中转 key，如 sk-...'}
          autoComplete="new-password" />
      </Modal>

      <Modal title="新增账号" open={createOpen} onCancel={() => setCreateOpen(false)} onOk={handleCreate} confirmLoading={creating} okText="创建" cancelText="取消" destroyOnClose>
        <Form form={form} layout="vertical" initialValues={{ role: 'user' }}>
          <Form.Item name="username" label="账号" rules={[{ required: true, message: '请输入账号' }]}>
            <Input placeholder="登录账号，如 zhangsan" autoComplete="off" />
          </Form.Item>
          <Form.Item name="password" label="密码" rules={[{ required: true, message: '请输入密码' }, { min: 6, message: '密码至少 6 位' }]}>
            <Input.Password placeholder="至少 6 位" autoComplete="new-password" />
          </Form.Item>
          <Form.Item name="name" label="姓名">
            <Input placeholder="选填，显示名称" />
          </Form.Item>
          <Form.Item name="role" label="角色">
            <Select options={[{ value: 'user', label: '普通用户' }, { value: 'admin', label: '管理员' }]} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}
