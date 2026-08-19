import { useEffect, useState } from 'react'
import {
  Button, Tag, Space, Modal, Form, Input, Switch, message,
  Typography, Card, Table, Tooltip, Alert,
} from 'antd'
import { PlusOutlined, ReloadOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import { appLoginRecipesApi, type AppLoginRecipe, type AppLoginRecipeInput } from '../api'
import { confirmDialog } from '../components/ConfirmModal'
import { PANEL_CARD_STYLE } from '../styles/theme'

const { Text, Paragraph } = Typography

export default function AppLoginRecipes() {
  const [data, setData] = useState<AppLoginRecipe[]>([])
  const [loading, setLoading] = useState(false)
  const [open, setOpen] = useState(false)
  const [editing, setEditing] = useState<AppLoginRecipe | null>(null)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  const load = () => {
    setLoading(true)
    appLoginRecipesApi.list().then((r) => setData(r.data)).finally(() => setLoading(false))
  }
  useEffect(load, [])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ restart_after_env: false, needs_tenant: false, enabled: true })
    setOpen(true)
  }
  const openEdit = (r: AppLoginRecipe) => {
    setEditing(r)
    form.setFieldsValue({
      name: r.name, match_keywords: r.match_keywords, env_steps: r.env_steps || '',
      restart_after_env: r.restart_after_env, needs_tenant: r.needs_tenant, enabled: r.enabled,
    })
    setOpen(true)
  }

  const submit = async (v: any) => {
    const payload: AppLoginRecipeInput = {
      name: (v.name || '').trim(),
      match_keywords: (v.match_keywords || '').trim(),
      env_steps: (v.env_steps || '').trim() || null,
      restart_after_env: !!v.restart_after_env,
      needs_tenant: !!v.needs_tenant,
      enabled: v.enabled !== false,
    }
    if (!payload.name || !payload.match_keywords) { message.warning('名称与匹配关键词必填'); return }
    setSaving(true)
    try {
      if (editing) await appLoginRecipesApi.update(editing.id, payload)
      else await appLoginRecipesApi.create(payload)
      message.success(editing ? '已更新' : '已创建')
      setOpen(false); load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败，请重试')
    } finally {
      setSaving(false)
    }
  }

  const remove = async (r: AppLoginRecipe) => {
    if (!(await confirmDialog({ title: '删除登录配方', desc: `确认删除「${r.name}」？`, ok: '删除', danger: true }))) return
    await appLoginRecipesApi.remove(r.id)
    message.success('已删除'); load()
  }

  const columns = [
    {
      title: '名称', dataIndex: 'name', width: 150,
      render: (v: string, r: AppLoginRecipe) => (
        <Space size={6}>
          <a onClick={() => openEdit(r)} style={{ fontWeight: 600 }}>{v}</a>
          {!r.enabled && <Tag color="default">停用</Tag>}
        </Space>
      ),
    },
    {
      title: '匹配关键词', dataIndex: 'match_keywords', width: 160,
      render: (v: string) => (
        <Space size={4} wrap>
          {(v || '').split(',').map((k) => k.trim()).filter(Boolean).map((k) => (
            <Tag key={k} color="purple" style={{ marginRight: 0 }}>{k}</Tag>
          ))}
        </Space>
      ),
    },
    {
      title: '选环境步骤', dataIndex: 'env_steps',
      render: (v: string | null) => {
        const lines = (v || '').split('\n').map((s) => s.trim()).filter(Boolean)
        if (!lines.length) return <Text type="secondary">无需选环境</Text>
        return (
          <Tooltip title={<div style={{ whiteSpace: 'pre-wrap' }}>{lines.map((l, i) => `${i + 1}. ${l}`).join('\n')}</div>}>
            <Text style={{ cursor: 'help' }}>{lines.length} 步 · {lines[0].slice(0, 22)}…</Text>
          </Tooltip>
        )
      },
    },
    {
      title: '选完重启', dataIndex: 'restart_after_env', width: 90, align: 'center' as const,
      render: (v: boolean) => v ? <Tag color="orange">重启</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '需选租户', dataIndex: 'needs_tenant', width: 90, align: 'center' as const,
      render: (v: boolean) => v ? <Tag color="geekblue">要</Tag> : <Text type="secondary">—</Text>,
    },
    {
      title: '操作', width: 120, align: 'right' as const,
      render: (_: any, r: AppLoginRecipe) => (
        <Space>
          <Button size="small" type="text" icon={<EditOutlined />} onClick={() => openEdit(r)} />
          <Button size="small" type="text" danger icon={<DeleteOutlined />} onClick={() => remove(r)} />
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24, maxWidth: 1200, margin: '0 auto' }}>
      <div style={{ display: 'flex', alignItems: 'flex-end', marginBottom: 16 }}>
        <div style={{ flex: 1 }}>
          <Typography.Title level={4} style={{ margin: 0 }}>App 登录配方</Typography.Title>
          <Text type="secondary">配置各 App 执行前自动登录的差异点，无需改代码即可把新 App 的登录接进平台</Text>
        </div>
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>新增配方</Button>
        </Space>
      </div>

      <Alert
        type="info" showIcon style={{ marginBottom: 16 }}
        message="登录方式全端固定：手机号 + 固定验证码（账号执行时手输，环境从枚举选）"
        description={
          <div style={{ fontSize: 13 }}>
            配方只描述每个 App 的<b>差异点</b>：怎么选环境、选完是否重启、要不要选租户。
            装完包的<b>启动页 / 引导页 / 权限弹窗</b>由 AI 视觉执行器自动趟过，<b>不用</b>在这里写。
            登录表单（填手机号、点获取验证码等倒计时、填码、勾协议、点登录）也是通用的，无需配置。
          </div>
        }
      />

      <Card style={PANEL_CARD_STYLE} styles={{ body: { padding: 0 } }}>
        <Table
          rowKey="id" size="middle" columns={columns} dataSource={data} loading={loading}
          pagination={false} locale={{ emptyText: '暂无配方，点右上「新增配方」添加' }}
        />
      </Card>

      <Modal
        title={editing ? `编辑配方 — ${editing.name}` : '新增登录配方'}
        open={open} onCancel={() => setOpen(false)} onOk={() => form.submit()}
        okText="保存" confirmLoading={saving} destroyOnClose width={620}
      >
        <Form form={form} layout="vertical" onFinish={submit} style={{ marginTop: 8 }}>
          <Form.Item name="name" label="配方名称" rules={[{ required: true, message: '请填写名称' }]}>
            <Input placeholder="如 Android App / Android App / Android App" />
          </Form.Item>
          <Form.Item
            name="match_keywords" label="匹配关键词"
            tooltip="逗号分隔；对执行端的「端key + 端名称」小写做『全部命中』匹配。如 商,app 表示端信息里同时含『商』和『app』才用这份配方"
            rules={[{ required: true, message: '请填写至少一个关键词' }]}
          >
            <Input placeholder="逗号分隔，如：商,app" />
          </Form.Item>
          <Form.Item
            name="env_steps" label="进环境入口的步骤（自然语言，一行一步；可留空表示无需选环境）"
            tooltip="只需描述『怎么打开环境设置/环境列表页』这个隐藏入口。进到列表后选中哪个环境、点确定，由引擎自动完成，不用写。可用 {env} 占位"
          >
            <Input.TextArea
              rows={4} style={{ fontSize: 13 }}
              placeholder={'如（Android App）只写入口这一句：\n点击登录页左下角的扇形图标，进入环境设置页'}
            />
          </Form.Item>
          <Paragraph type="secondary" style={{ fontSize: 12.5, marginTop: -4 }}>
            只写「怎么进到环境设置/环境列表页」这个隐藏入口即可 —— 进去后<b>选中「{'{env}'}」并点确定由引擎自动完成</b>；
            启动页/引导页/权限弹窗也会被自动跳过，都不用写。
          </Paragraph>
          <Space size={40}>
            <Form.Item
              name="restart_after_env" label="选完环境后重启 App" valuePropName="checked"
              tooltip="部分 App（如Android App）切换环境后需杀进程重启才生效"
            >
              <Switch />
            </Form.Item>
            <Form.Item
              name="needs_tenant" label="需要选择租户" valuePropName="checked"
              tooltip="登录后校验左上角租户名，不符则进选择租户页切换。仅Android App需要"
            >
              <Switch />
            </Form.Item>
            <Form.Item name="enabled" label="启用" valuePropName="checked">
              <Switch />
            </Form.Item>
          </Space>
        </Form>
      </Modal>
    </div>
  )
}
