import { useEffect, useState } from 'react'
import {
  Card, Form, Input, InputNumber, Switch, Button, message, List, Typography, Space, Tag, Empty,
} from 'antd'
import { PlusOutlined } from '@ant-design/icons'
import { projectsApi, executionsApi, qualityGateConfigApi } from '../api'
import { confirmDialog } from '../components/ConfirmModal'
import { useProjectStore } from '../store/projectStore'
import { PANEL_CARD_STYLE } from '../styles/theme'

export default function ProjectSettings() {
  const currentProject = useProjectStore((s) => s.currentProject)
  const [projects, setProjects] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [creating, setCreating] = useState(false)
  const [form] = Form.useForm()
  const [gateForm] = Form.useForm()
  const [gateLoading, setGateLoading] = useState(false)
  const [gateSaving, setGateSaving] = useState(false)

  const load = () => {
    setLoading(true)
    projectsApi.list().then((r) => setProjects(r.data)).finally(() => setLoading(false))
  }

  useEffect(load, [])

  useEffect(() => {
    if (!currentProject) return
    setGateLoading(true)
    qualityGateConfigApi.get(currentProject.id).then((r) => gateForm.setFieldsValue(r.data)).finally(() => setGateLoading(false))
  }, [currentProject?.id])

  const handleCreate = async (values: any) => {
    setCreating(true)
    try {
      await projectsApi.create(values)
      message.success('项目已创建')
      form.resetFields()
      load()
    } finally {
      setCreating(false)
    }
  }

  const handleGateSave = async (values: any) => {
    if (!currentProject) return
    setGateSaving(true)
    try {
      await qualityGateConfigApi.update(currentProject.id, values)
      message.success('门禁配置已保存')
    } finally {
      setGateSaving(false)
    }
  }

  const triggerExecution = async (projectId: string, projectName: string) => {
    try {
      await executionsApi.create({ project_id: projectId, name: `手动执行 - ${projectName}`, trigger: 'manual' })
      message.success('执行已触发，前往「执行历史」查看')
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '执行触发失败，请稍后重试')
    }
  }

  return (
    <div style={{ padding: 24 }}>
      <Typography.Title level={4} style={{ marginBottom: 16 }}>项目设置</Typography.Title>

      <Card title="新建项目" bordered={false} style={{ ...PANEL_CARD_STYLE, marginBottom: 24 }}>
        <Form form={form} layout="vertical" onFinish={handleCreate} style={{ maxWidth: 600 }}>
          <Form.Item name="name" label="项目名称" rules={[{ required: true }]}>
            <Input />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="feishu_webhook" label="飞书 Webhook URL">
            <Input placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..." />
          </Form.Item>
          <Form.Item name="pass_rate_threshold" label="质量门禁通过率阈值 (%)" initialValue={80}>
            <InputNumber min={0} max={100} style={{ width: 200 }} />
          </Form.Item>
          <Form.Item name="ci_gate_enabled" label="启用 CI 门禁" valuePropName="checked" initialValue={false}>
            <Switch />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={creating} icon={<PlusOutlined />}>
            创建项目
          </Button>
        </Form>
      </Card>

      <Card title={`质量门禁配置${currentProject ? ` - ${currentProject.name}` : ''}`} bordered={false} style={{ ...PANEL_CARD_STYLE, marginBottom: 24 }} loading={gateLoading}>
        {!currentProject ? (
          <Empty description="请先在右上角选择项目" />
        ) : (
          <Form form={gateForm} layout="vertical" onFinish={handleGateSave} style={{ maxWidth: 600 }}>
            <Form.Item name="overall_pass_rate_threshold" label="总体通过率阈值 (%)">
              <InputNumber min={0} max={100} style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="enable_overall_pass_rate_gate" label="启用总体通过率门禁" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="p1_failure_threshold" label="P1 失败数阈值">
              <InputNumber min={0} style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="enable_p1_failure_gate" label="启用 P1 失败门禁" valuePropName="checked">
              <Switch />
            </Form.Item>
            <Form.Item name="pass_rate_wow_drop_threshold" label="通过率环比下降阈值 (pp)">
              <InputNumber min={0} max={100} style={{ width: 200 }} />
            </Form.Item>
            <Form.Item name="coverage_threshold" label="覆盖率阈值 (%)">
              <InputNumber min={0} max={100} style={{ width: 200 }} />
            </Form.Item>
            <Button type="primary" htmlType="submit" loading={gateSaving}>
              保存配置
            </Button>
          </Form>
        )}
      </Card>

      <Card title="现有项目" bordered={false} style={PANEL_CARD_STYLE} loading={loading}>
        <List
          dataSource={projects}
          renderItem={(proj: any) => (
            <List.Item
              actions={[
                <Button type="link" size="small" onClick={() => triggerExecution(proj.id, proj.name)}>
                  触发执行
                </Button>,
                <Button key="del" type="link" size="small" danger
                  onClick={async () => { if (await confirmDialog({ title: '删除项目', desc: `确认删除项目「${proj.name}」？此操作不可恢复。`, ok: '删除', danger: true })) projectsApi.delete(proj.id).then(load) }}>删除</Button>,
              ]}
            >
              <List.Item.Meta
                title={
                  <Space>
                    {proj.name}
                    {proj.ci_gate_enabled && <Tag color="blue">CI门禁</Tag>}
                  </Space>
                }
                description={
                  <Space>
                    <span>通过率阈值: {proj.pass_rate_threshold}%</span>
                    {proj.feishu_webhook && <Tag color="green">飞书通知</Tag>}
                  </Space>
                }
              />
            </List.Item>
          )}
        />
      </Card>
    </div>
  )
}
