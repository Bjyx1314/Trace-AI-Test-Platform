import { useEffect, useState } from 'react'
import {
  Button, Tag, Space, Modal, Form, Input, Select, message,
  Typography, Card, Descriptions, Empty, Collapse,
} from 'antd'
import {
  PlusOutlined, ReloadOutlined, DeleteOutlined, EditOutlined, SyncOutlined,
} from '@ant-design/icons'
import { frameworksApi, type FrameworkRepo } from '../api'
import { useProjectStore } from '../store/projectStore'
import { useAuthStore } from '../store/authStore'
import { confirmDialog } from '../components/ConfirmModal'
import { PANEL_CARD_STYLE } from '../styles/theme'

const REPO_TYPE_LABEL: Record<string, string> = { interface: '接口', web: 'PC Web', app: 'App' }
const STATUS_COLOR: Record<string, string> = { ready: 'green', indexing: 'blue', pending: 'default', failed: 'red' }

// 单个积木块(类/页面/流程/组件)：名称 + 模块路径 + 方法/关键字 chips
function BlockRow({ title, doc, module, chips, chipColor }: { title: string; doc?: string | null; module?: string; chips?: string[]; chipColor?: string }) {
  const [all, setAll] = useState(false)
  const list = chips || []
  const shown = all ? list : list.slice(0, 16)
  return (
    <div style={{ padding: '10px 12px', borderBottom: '1px solid #F1F4F6' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: '#0F172A' }}>{title}</span>
        {module && <span style={{ fontSize: 11.5, fontFamily: 'monospace', color: '#94A3B8' }}>{module}</span>}
      </div>
      {doc && <div style={{ fontSize: 12, color: '#64748B', marginTop: 3, lineHeight: 1.5 }}>{String(doc).split('\n')[0]}</div>}
      {list.length > 0 && (
        <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 4 }}>
          {shown.map((c, i) => <Tag key={i} color={chipColor} style={{ marginRight: 0 }}>{c}</Tag>)}
          {list.length > 16 && <a style={{ fontSize: 12 }} onClick={() => setAll(!all)}>{all ? '收起' : `+${list.length - 16} 更多`}</a>}
        </div>
      )}
    </div>
  )
}

// 索引清单：按类别分组展示积木
function IndexDetail({ repo }: { repo: FrameworkRepo }) {
  const idx: any = repo.index_json || {}
  if (repo.repo_type === 'interface') {
    const classes: any[] = idx.aw_classes || []
    return (
      <Collapse defaultActiveKey={['aw']} items={[{
        key: 'aw',
        label: `AW 关键字类（${idx.class_count ?? classes.length}）· 共 ${idx.keyword_count ?? 0} 个关键字`,
        children: (
          <div>{classes.map((c, i) => (
            <BlockRow key={i} title={c.class} doc={c.doc} module={c.module} chipColor="blue"
              chips={(c.keywords && c.keywords.length ? c.keywords : c.methods) || []} />
          ))}</div>
        ),
      }]} />
    )
  }
  const cats: [string, string, any[], string][] = [
    ['flows', '业务流程 Flows', idx.flows || [], 'geekblue'],
    ['pages', '页面对象 Pages', idx.pages || [], 'cyan'],
    ['components', '组件 Components', idx.components || [], 'purple'],
    ['fixtures', '夹具 Fixtures', idx.fixtures || [], 'gold'],
  ]
  const active = cats.find(([, , arr]) => arr.length)?.[0]
  return (
    <Collapse defaultActiveKey={active ? [active] : []} items={cats.filter(([, , arr]) => arr.length).map(([key, label, arr, color]) => ({
      key, label: `${label}（${arr.length}）`,
      children: (
        <div>{arr.map((c: any, i: number) => key === 'fixtures'
          ? <BlockRow key={i} title={c.name} doc={c.doc} module={c.module} chips={c.scope ? [`scope: ${c.scope}`] : []} chipColor="default" />
          : <BlockRow key={i} title={c.class} doc={c.doc} module={c.module} chipColor={color}
              chips={(c.methods || []).map((m: any) => (typeof m === 'string' ? m : m.name)).filter(Boolean)} />
        )}</div>
      ),
    }))} />
  )
}

export default function FrameworkRepos() {
  const { currentProject } = useProjectStore()
  const { isAdmin } = useAuthStore()
  const [rows, setRows] = useState<FrameworkRepo[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [editing, setEditing] = useState<FrameworkRepo | null>(null)
  const [reindexing, setReindexing] = useState<string | null>(null)
  const [detail, setDetail] = useState<FrameworkRepo | null>(null)
  const [form] = Form.useForm()

  const load = async () => {
    setLoading(true)
    try {
      const r = await frameworksApi.list()
      setRows(r.data)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [])

  const openCreate = () => {
    setEditing(null)
    form.resetFields()
    form.setFieldsValue({ repo_type: 'interface', branch: 'main', project_id: currentProject?.id })
    setModalOpen(true)
  }

  const openEdit = (r: FrameworkRepo) => {
    setEditing(r)
    form.setFieldsValue({
      ...r,
      env_json: r.env_json ? JSON.stringify(r.env_json, null, 2) : undefined,
    })
    setModalOpen(true)
  }

  const submit = async () => {
    const v = await form.validateFields()
    if (v.env_json && typeof v.env_json === 'string') {
      try { v.env_json = JSON.parse(v.env_json) } catch { message.error('env_json 不是合法 JSON'); return }
    }
    try {
      if (editing) {
        await frameworksApi.update(editing.id, v)
        message.success('已更新')
      } else {
        await frameworksApi.create(v)
        message.success('已创建，记得点「索引」扫描积木')
      }
      setModalOpen(false)
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    }
  }

  const reindex = async (r: FrameworkRepo) => {
    setReindexing(r.id)
    try {
      // local_path 已配置则可不联网，直接扫本地；否则走 git clone/pull
      const res = await frameworksApi.reindex(r.id, !r.local_path)
      message.success(`索引完成：${summaryText(res.data)}`)
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '索引失败')
      load()
    } finally {
      setReindexing(null)
    }
  }

  const summaryText = (r: FrameworkRepo) => {
    const s = r.index_summary || {}
    if (r.repo_type === 'interface') return `${s.class_count ?? 0} AW类 / ${s.keyword_count ?? 0} 关键字`
    return `${s.page_count ?? 0} pages / ${s.flow_count ?? 0} flows / ${s.fixture_count ?? 0} fixtures`
  }

  // 类型 → 图标徽章配色
  const TYPE_ICON: Record<string, { icon: string; color: string; bg: string }> = {
    interface: { icon: 'api', color: '#1577C2', bg: '#EAF4FB' },
    web: { icon: 'desktop_windows', color: '#0B8276', bg: '#E6F6F4' },
    app: { icon: 'smartphone', color: '#6B4FD6', bg: '#F0EDFD' },
  }
  const STATUS_PILL: Record<string, { bg: string; color: string; label: string }> = {
    ready: { bg: '#E9F8EF', color: '#128A43', label: '已索引' },
    indexing: { bg: '#EAF4FB', color: '#1577C2', label: '索引中' },
    pending: { bg: '#F1F5F9', color: '#64748B', label: '待索引' },
    failed: { bg: '#FDECEC', color: '#C9332B', label: '索引失败' },
  }

  if (!isAdmin) {
    return <div style={{ padding: 24 }}><Empty description="该页面仅管理员可访问" style={{ padding: '60px 0' }} /></div>
  }

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div />
        <Space>
          <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>登记框架仓库</Button>
        </Space>
      </div>
      <Typography.Paragraph type="secondary" style={{ marginTop: -8 }}>
        把已有自动化框架（接口 AWFunc / PC Web / App POM）的 git 仓库绑定到平台。登记后点「索引」扫描积木，
        即可由平台按框架原生风格生成用例、提交回仓库、在仓库内执行。
      </Typography.Paragraph>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        {!loading && rows.length === 0 ? (
          <Empty description="暂无框架仓库，点击「登记框架仓库」绑定" style={{ padding: '40px 0' }} />
        ) : rows.map((r) => {
          const ti = TYPE_ICON[r.repo_type] || TYPE_ICON.interface
          const sp = STATUS_PILL[r.index_status] || STATUS_PILL.pending
          return (
            <div key={r.id} className="tech-card" style={{ ...PANEL_CARD_STYLE, padding: '16px 18px', display: 'flex', alignItems: 'center', gap: 14 }}>
              <div style={{ width: 42, height: 42, borderRadius: 12, flex: 'none', background: ti.bg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                <span className="ms" style={{ fontSize: 22, color: ti.color }}>{ti.icon}</span>
              </div>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                  <a className="row-title" style={{ fontSize: 15, fontWeight: 700, color: '#0F172A' }} onClick={() => frameworksApi.get(r.id, true).then((d) => setDetail(d.data))}>{r.name}</a>
                  <span style={{ fontSize: 11.5, fontWeight: 500, padding: '1px 8px', borderRadius: 6, background: ti.bg, color: ti.color }}>{REPO_TYPE_LABEL[r.repo_type] || r.repo_type}</span>
                  {!r.enabled && <span style={{ fontSize: 11.5, color: '#94A3B8', background: '#F1F5F9', borderRadius: 6, padding: '1px 8px' }}>已停用</span>}
                </div>
                <div style={{ fontSize: 12, fontFamily: 'monospace', color: '#94A3B8', marginTop: 4, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  <span className="ms" style={{ fontSize: 13, verticalAlign: -2, marginRight: 4 }}>commit</span>{r.git_url.split('/').pop()}@{r.branch}
                </div>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4, minWidth: 150 }}>
                <span style={{ fontSize: 11.5, fontWeight: 500, padding: '2px 9px', borderRadius: 7, background: sp.bg, color: sp.color }}>{sp.label}</span>
                {r.index_status === 'ready' && <span style={{ fontSize: 11.5, color: '#94A3B8' }}>{summaryText(r)}</span>}
              </div>
              <div style={{ display: 'flex', gap: 8, flex: 'none' }}>
                <Button size="small" icon={<SyncOutlined spin={reindexing === r.id} />} loading={reindexing === r.id} onClick={() => reindex(r)}>索引</Button>
                <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(r)} />
                <Button size="small" danger icon={<DeleteOutlined />}
                  onClick={async () => {
                    if (await confirmDialog({ title: '删除框架仓库', desc: `确认删除「${r.name}」的登记？`, ok: '删除', danger: true })) {
                      await frameworksApi.delete(r.id); message.success('已删除'); load()
                    }
                  }} />
              </div>
            </div>
          )
        })}
      </div>

      <Modal
        title={editing ? '编辑框架仓库' : '登记框架仓库'}
        open={modalOpen}
        onOk={submit}
        onCancel={() => setModalOpen(false)}
        width={680}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="名称" rules={[{ required: true }]}>
            <Input placeholder="如：接口自动化 / PC Web&App 自动化" />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="repo_type" label="类型" rules={[{ required: true }]} style={{ width: 160 }}>
              <Select disabled={!!editing} options={[
                { value: 'interface', label: '接口（AWFunc）' },
                { value: 'web', label: 'PC Web（Playwright）' },
                { value: 'app', label: 'App（Appium）' },
              ]} />
            </Form.Item>
            <Form.Item name="branch" label="分支" rules={[{ required: true }]} style={{ width: 160 }}>
              <Input placeholder="main / master" />
            </Form.Item>
            <Form.Item name="project_id" label="归属项目（空=全局）" style={{ flex: 1 }}>
              <Input placeholder="项目ID，留空为全局共享" />
            </Form.Item>
          </Space>
          <Form.Item name="git_url" label="Git URL" rules={[{ required: true }]}>
            <Input placeholder="https://github.com/your-org/automation-framework.git" />
          </Form.Item>
          <Form.Item name="local_path" label="本地路径（可选，已 clone 时直接扫描这里，免联网）">
            <Input placeholder="D:/automation/api 或留空由平台 clone 到工作区" />
          </Form.Item>
          <Space style={{ display: 'flex' }} align="start">
            <Form.Item name="tests_root" label="用例根" style={{ flex: 1 }}>
              <Input placeholder="cases / ui_web/tests / ui_app/tests" />
            </Form.Item>
            <Form.Item name="data_root" label="数据根（接口）" style={{ flex: 1 }}>
              <Input placeholder="data" />
            </Form.Item>
            <Form.Item name="keyword_root" label="关键字库（接口）" style={{ flex: 1 }}>
              <Input placeholder="data/aw/aw_class" />
            </Form.Item>
          </Space>
          <Form.Item name="run_command" label="执行命令模板（{target} 占位）">
            <Input placeholder="pytest {target} -m smoke --project demo" />
          </Form.Item>
          <Form.Item name="install_command" label="依赖安装命令">
            <Input placeholder="pip install -r requirements.txt" />
          </Form.Item>
          <Form.Item name="description" label="描述">
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item name="env_json" label="环境变量(JSON，可选)">
            <Input.TextArea rows={3} placeholder='{"ENV": "sit"}' />
          </Form.Item>
        </Form>
      </Modal>

      <Modal title="积木索引清单" open={!!detail} footer={null} onCancel={() => setDetail(null)} width={780}>
        {detail && (
          <>
            <Descriptions column={2} size="small" style={{ marginBottom: 12 }}>
              <Descriptions.Item label="名称">{detail.name}</Descriptions.Item>
              <Descriptions.Item label="类型">{REPO_TYPE_LABEL[detail.repo_type]}</Descriptions.Item>
              <Descriptions.Item label="索引状态"><Tag color={STATUS_COLOR[detail.index_status]}>{STATUS_PILL[detail.index_status]?.label || detail.index_status}</Tag></Descriptions.Item>
              <Descriptions.Item label="提交">{detail.index_commit?.slice(0, 8) || '-'}</Descriptions.Item>
              <Descriptions.Item label="积木统计" span={2}>{summaryText(detail)}</Descriptions.Item>
            </Descriptions>
            {detail.index_status !== 'ready' || !detail.index_json
              ? <Empty description="尚未索引，点列表里的「索引」先扫描积木" />
              : <div style={{ maxHeight: '60vh', overflow: 'auto' }}><IndexDetail repo={detail} /></div>}
            <Collapse style={{ marginTop: 12 }} items={[{
              key: 'raw', label: '查看原始索引 JSON',
              children: (
                <pre style={{ maxHeight: 320, overflow: 'auto', background: '#0f172a', color: '#cbd5e1', padding: 12, borderRadius: 8, fontSize: 12 }}>
                  {JSON.stringify(detail.index_json, null, 2)}
                </pre>
              ),
            }]} />
          </>
        )}
      </Modal>
    </div>
  )
}
