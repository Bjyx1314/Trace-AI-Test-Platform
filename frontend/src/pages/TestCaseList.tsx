import { useEffect, useState } from 'react'
import {
  Table, Button, Tag, Space, Typography, Input, Select, Drawer, Descriptions, List,
  Popconfirm, Empty, Card, Modal, Form, message, Spin, Row, Col, Tabs, Dropdown, Upload,
} from 'antd'
import {
  ArrowLeftOutlined, HistoryOutlined, FileTextOutlined, DeleteOutlined,
  UndoOutlined, EditOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { testCasesApi, enumsApi, frameworksApi, executionsApi } from '../api'
import ExecConfigModal, { categorizeCaseByPlatform, isAutoExecutable } from '../components/ExecConfigModal'
import { useProjectStore } from '../store/projectStore'
import { confirmDialog } from '../components/ConfirmModal'
import { PANEL_CARD_STYLE, PRIMARY_COLOR } from '../styles/theme'
import dayjs from 'dayjs'

// 优先级发光胶囊样式：统一视觉，取代红橙蓝混搭
const PRIORITY_PILL: Record<string, { bg: string; color: string; border: string }> = {
  P0: { bg: '#FFF1F0', color: '#E5484D', border: '#FFCCC7' },
  P1: { bg: '#FFF7E6', color: '#D97706', border: '#FFE0A3' },
  P2: { bg: '#EAF1FF', color: '#2D6CFF', border: '#BBD2FF' },
}

function PriorityPill({ value }: { value: string }) {
  const s = PRIORITY_PILL[value] || PRIORITY_PILL.P2
  return (
    <span className="tech-pill" style={{ background: s.bg, color: s.color, borderColor: s.border }}>
      {value}
    </span>
  )
}

export default function TestCaseList() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const { currentProject, projects } = useProjectStore()

  const [selectedKeys, setSelectedKeys] = useState<string[]>([])

  const handleBatchDelete = async () => {
    if (!selectedKeys.length) return
    if (!(await confirmDialog({ title: '批量删除用例', desc: `确认删除选中的 ${selectedKeys.length} 条用例？删除后进入回收站。`, ok: '删除', danger: true }))) return
    await Promise.all(selectedKeys.map((id) => testCasesApi.delete(id)))
    message.success(`已删除 ${selectedKeys.length} 条用例`)
    setSelectedKeys([])
    load()
  }
  const handleBatchPurge = async () => {
    if (!selectedKeys.length) return
    if (!(await confirmDialog({ title: '永久删除', desc: `确认永久删除选中的 ${selectedKeys.length} 条用例？不可恢复。`, ok: '永久删除', danger: true }))) return
    await Promise.all(selectedKeys.map((id) => testCasesApi.purge(id)))
    message.success(`已永久删除 ${selectedKeys.length} 条`)
    setSelectedKeys([])
    load()
  }

  // Read initial filters from URL params
  const [search, setSearch] = useState('')
  const [priority, setPriority] = useState<string | undefined>(
    searchParams.get('priority') || undefined,
  )
  const [moduleFilter, setModuleFilter] = useState<string | undefined>(
    searchParams.get('module') || undefined,
  )
  const [platformFilter, setPlatformFilter] = useState<string | undefined>(
    searchParams.get('platform') || undefined,
  )
  const [caseTypeFilter, setCaseTypeFilter] = useState<string | undefined>(
    searchParams.get('case_type') || undefined,
  )
  // 是否自动测试筛选：'auto'=自动测试 / 'manual'=手动测试 / undefined=全部。按执行方式(last_status)判定，不看结果
  const [autoFilter, setAutoFilter] = useState<string | undefined>(
    searchParams.get('auto') || undefined,
  )
  const [projectFilter, setProjectFilter] = useState<string | undefined>(
    searchParams.get('project_id') || undefined,
  )

  const [moduleOptions, setModuleOptions] = useState<any[]>([])
  const [platformOptions, setPlatformOptions] = useState<any[]>([])
  const [categoryOptions, setCategoryOptions] = useState<any[]>([])
  const [data, setData] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [activeTab, setActiveTab] = useState<'all' | 'trash'>('all')

  // Detail + edit
  const [selected, setSelected] = useState<any>(null)
  const [editMode, setEditMode] = useState(false)
  const [saving, setSaving] = useState(false)
  const [form] = Form.useForm()

  // 框架产物 review/提交
  const [autoBusy, setAutoBusy] = useState<string | null>(null)

  // 执行测试：与需求详情一致 —— 打开按端分流的 ExecConfigModal，再 executionsApi.create
  const [execModalOpen, setExecModalOpen] = useState(false)
  const [pendingCaseIds, setPendingCaseIds] = useState<string[]>([])
  const [execApiBaseUrl, setExecApiBaseUrl] = useState('')

  const openExecModal = (caseIds: string[]) => {
    setPendingCaseIds(caseIds)
    setExecModalOpen(true)
  }

  const runExecution = async (caseIds: string[], runMode: string = 'fresh', accountOverrides?: Record<string, any>, targetDevice?: string | null, env?: string, packageOverrides?: Record<string, string>) => {
    if (!selected) return
    try {
      await executionsApi.create({
        project_id: selected.project_id,
        name: `${selected.title} - 执行测试`,
        case_ids: caseIds,
        run_mode: runMode,
        account_overrides: accountOverrides && Object.keys(accountOverrides).length ? accountOverrides : undefined,
        target_device: targetDevice ?? undefined,
        env: env || undefined,
        package_overrides: packageOverrides,
      })
      message.success('已创建执行测试，正在后台运行')
      navigate('/executions')
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '执行测试启动失败，请稍后重试')
    }
  }

  const handleExecuteConfirm = async (runMode: string, accountOverrides?: Record<string, any>, targetDevice?: string | null, env?: string, packageOverrides?: Record<string, string>) => {
    const targetCases = data.filter((c: any) => pendingCaseIds.includes(c.id))
    let executableCases = targetCases.filter((c: any) => isAutoExecutable(categorizeCaseByPlatform(c)))
    // 已连真机时，App 用例也纳入(AI 直连真机执行)
    const mobileCases = targetCases.filter((c: any) => categorizeCaseByPlatform(c) === 'mobile')
    if (mobileCases.length) {
      try {
        const dev = await executionsApi.devices()
        if (dev.data.devices?.length || dev.data.sonic_devices?.length) executableCases = [...executableCases, ...mobileCases]
      } catch { /* 探测失败按不可执行处理 */ }
    }
    if (executableCases.length === 0) {
      message.warning('所选用例中没有可自动执行的用例，App 需先连真机、小程序需接入小程序自动化环境')
      return
    }
    setExecModalOpen(false)
    await runExecution(executableCases.map((c: any) => c.id), runMode, accountOverrides, targetDevice, env, packageOverrides)
  }

  const runReview = async () => {
    if (!selected) return
    setAutoBusy('review')
    try {
      const r = await frameworksApi.reviewCase(selected.id)
      if (r.data.ok) {
        Modal.success({ title: 'Review 通过', content: r.data.warnings.length ? `提醒：${r.data.warnings.join('；')}` : '无问题，可提交' })
      } else {
        Modal.error({ title: 'Review 未通过', content: r.data.issues.join('；') })
      }
    } catch (e: any) {
      message.error(e?.response?.data?.detail || 'review 失败')
    } finally {
      setAutoBusy(null)
    }
  }

  const runCommit = async (push: boolean) => {
    if (!selected) return
    setAutoBusy('commit')
    try {
      const r = await frameworksApi.commitCase(selected.id, push)
      Modal.success({ title: '已提交到框架仓库', content: `分支 ${r.data.branch} @ ${r.data.commit.slice(0, 8)}，${r.data.files.length} 个文件${r.data.pushed ? '，已 push' : ''}` })
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '提交失败')
    } finally {
      setAutoBusy(null)
    }
  }

  // Operation logs
  const [logCase, setLogCase] = useState<any>(null)
  const [logs, setLogs] = useState<any[]>([])
  const [logsLoading, setLogsLoading] = useState(false)

  useEffect(() => {
    enumsApi.list('module').then((r) => setModuleOptions(r.data))
    enumsApi.list('platform').then((r) => setPlatformOptions(r.data))
    enumsApi.list('category').then((r) => setCategoryOptions(r.data))
  }, [])

  const load = () => {
    const pid = projectFilter ?? currentProject?.id
    setLoading(true)
    const req = activeTab === 'trash'
      ? testCasesApi.trash({ project_id: pid })
      : testCasesApi.list({ project_id: pid, priority, library_only: true })
    req
      .then((r) => setData(r.data))
      .finally(() => setLoading(false))
  }

  useEffect(load, [priority, currentProject?.id, projectFilter, activeTab])

  const handleRestore = (row: any) => {
    testCasesApi.restore(row.id).then(() => {
      message.success('已恢复到用例列表')
      load()
    })
  }

  const handlePurge = (row: any) => {
    testCasesApi.purge(row.id)
      .then(() => {
        message.success('已永久删除')
        load()
      })
      .catch((err) => {
        message.error(err?.response?.data?.detail || '永久删除失败')
      })
  }

  // 执行方式判定(只看方式不看结果)：手动=manual_*，自动=其它已执行(非 not_run/空)
  const isManualCase = (d: any) => d.last_status === 'manual_passed' || d.last_status === 'manual_failed'
  const isAutoCase = (d: any) => !!d.last_status && d.last_status !== 'not_run' && !isManualCase(d)

  const filtered = data.filter((d) => {
    if (search && !d.title.toLowerCase().includes(search.toLowerCase())) return false
    if (moduleFilter && !(d.modules || []).includes(moduleFilter)) return false
    if (platformFilter && !(d.platforms || []).includes(platformFilter)) return false
    if (caseTypeFilter && d.case_type !== caseTypeFilter) return false
    if (autoFilter === 'auto' && !isAutoCase(d)) return false
    if (autoFilter === 'manual' && !isManualCase(d)) return false
    return true
  })

  // 注意：不再把筛选写回 URL。用例列表的筛选只在进入时从深链参数(用例库统计图标点入)读一次，
  // 之后纯组件内 state。这样与需求详情等其它页面各自独立、互不影响，也不会"粘"在地址栏里被下次带入。

  // Project name display for the filter
  const projectFilterName = projectFilter
    ? projects.find((p) => p.id === projectFilter)?.name
    : undefined

  const openDetail = (row: any) => {
    setSelected(row)
    setEditMode(false)
  }

  const startEdit = () => {
    form.setFieldsValue({
      title: selected.title,
      priority: selected.priority,
      case_type: selected.case_type,
      modules: selected.modules || [],
      platforms: selected.platforms || [],
      expected_result: selected.expected_result || '',
      preconditions: selected.preconditions || [],
      steps: (selected.steps || []).map((s: any) => ({
        seq: s.seq,
        action: s.action,
        expected: s.expected,
      })),
    })
    setEditMode(true)
  }

  const handleSave = async () => {
    try {
      const values = await form.validateFields()
      setSaving(true)
      await testCasesApi.update(selected.id, {
        project_id: selected.project_id,
        requirement_id: selected.requirement_id,
        product_line: selected.product_line,
        source_req_id: selected.source_req_id,
        source_issue_point: selected.source_issue_point,
        tags: selected.tags,
        ...values,
      })
      message.success('保存成功')
      const updated = { ...selected, ...values }
      setSelected(updated)
      setEditMode(false)
      load()
    } catch (err: any) {
      if (err?.errorFields) return
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const openLogs = async (row: any) => {
    setLogCase(row)
    setLogs([])
    setLogsLoading(true)
    testCasesApi
      .logs(row.id)
      .then((r) => setLogs(r.data))
      .finally(() => setLogsLoading(false))
  }

  // Active filter tags summary
  const activeFilters: string[] = []
  if (projectFilterName) activeFilters.push(`项目: ${projectFilterName}`)

  const exportPid = projectFilter ?? currentProject?.id
  const handleImport = async (file: File) => {
    if (!exportPid) { message.warning('请先选择项目后再导入'); return false }
    const hide = message.loading('正在导入解析…', 0)
    try {
      const r = await testCasesApi.importCases(file, exportPid)
      message.success(`已导入 ${r.data.created} 条用例`)
      load()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '导入失败，请检查文件格式')
    } finally {
      hide()
    }
    return false // 阻止 antd 默认上传
  }
  if (moduleFilter) activeFilters.push(`模块: ${moduleFilter}`)
  if (platformFilter) activeFilters.push(`端: ${platformFilter}`)
  if (caseTypeFilter) activeFilters.push(`场景类型: ${caseTypeFilter}`)
  if (priority) activeFilters.push(`优先级: ${priority}`)
  if (autoFilter) activeFilters.push(`执行方式: ${autoFilter === 'auto' ? '自动测试' : '手动测试'}`)

  const columns = [
    { title: '用例ID', dataIndex: 'case_id', key: 'case_id', width: 110 },
    {
      title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
      render: (v: string, row: any) => (
        <a
          className="row-title"
          onClick={() => openDetail(row)}
          style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}
        >
          <FileTextOutlined style={{ color: '#94A3B8' }} />
          {v}
        </a>
      ),
    },
    {
      title: '模块',
      dataIndex: 'modules',
      key: 'modules',
      width: 150,
      render: (v: string[]) => (v || []).map((m) => (
        <Tag key={m} bordered style={{ background: '#fff', color: '#4E5969', borderColor: '#E5E9F0', borderRadius: 6 }}>{m}</Tag>
      )),
    },
    {
      title: '端',
      dataIndex: 'platforms',
      key: 'platforms',
      width: 150,
      render: (v: string[]) => (v || []).map((p) => (
        <Tag key={p} style={{ background: '#EAF4FB', color: '#1577C2', border: '1px solid #C6E1F4', borderRadius: 6 }}>{p}</Tag>
      )),
    },
    {
      title: '场景类型',
      dataIndex: 'case_type',
      key: 'case_type',
      width: 80,
      render: (v: string) => v
        ? <Tag bordered style={{ background: '#fff', color: '#4E5969', borderColor: '#E5E9F0', borderRadius: 6 }}>{v}</Tag>
        : '-',
    },
    {
      title: '优先级',
      dataIndex: 'priority',
      key: 'priority',
      width: 80,
      render: (v: string) => <PriorityPill value={v} />,
    },
    {
      title: '自动化',
      dataIndex: 'is_automated',
      key: 'is_automated',
      width: 100,
      render: (v: boolean) => (
        <span
          className="tech-pill"
          style={v
            ? { background: '#E9F8EF', color: '#128A43', borderColor: '#B5E0C8', whiteSpace: 'nowrap' }
            : { background: '#F4F5F8', color: '#9CA3AF', borderColor: '#E5E9F0', whiteSpace: 'nowrap' }}
        >
          {v ? <ThunderboltOutlined /> : null}{v ? '已生成' : '未生成'}
        </span>
      ),
    },
    activeTab === 'trash'
      ? {
          title: '删除时间',
          dataIndex: 'deleted_at',
          key: 'deleted_at',
          width: 130,
          render: (v: string) => (v ? dayjs(v).format('MM-DD HH:mm') : '—'),
        }
      : {
          title: '创建时间',
          dataIndex: 'created_at',
          key: 'created_at',
          width: 130,
          render: (v: string) => dayjs(v).format('MM-DD HH:mm'),
        },
    {
      title: '操作',
      key: 'action',
      width: 240,
      render: (_: any, row: any) => (
        <Space size={2}>
          <Button type="text" size="small" icon={<HistoryOutlined />} onClick={() => openLogs(row)}>记录</Button>
          {activeTab === 'trash' ? (
            <>
              <Button type="text" size="small" icon={<UndoOutlined />} style={{ color: PRIMARY_COLOR }} onClick={() => handleRestore(row)}>恢复</Button>
              <Button type="text" size="small" danger icon={<DeleteOutlined />}
                onClick={async () => { if (await confirmDialog({ title: '永久删除', desc: '永久删除后不可恢复，确认？', ok: '永久删除', danger: true })) handlePurge(row) }}>永久删除</Button>
            </>
          ) : (
            <Button type="text" size="small" danger icon={<DeleteOutlined />}
              onClick={async () => { if (await confirmDialog({ title: '删除用例', desc: `确认删除「${row.title || row.case_id}」？删除后进入回收站。`, ok: '删除', danger: true })) testCasesApi.delete(row.id).then(load) }}>删除</Button>
          )}
        </Space>
      ),
    },
  ]

  return (
    <div style={{ padding: 24 }}>
      <Space align="center" style={{ marginBottom: 16 }}>
        <Button
          icon={<ArrowLeftOutlined />}
          onClick={() => navigate('/testcases')}
        >
          返回用例库
        </Button>
        <Typography.Title level={4} style={{ margin: 0, display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <FileTextOutlined style={{ color: PRIMARY_COLOR }} />用例列表
        </Typography.Title>
        {activeFilters.length > 0 && (
          <Space size={4}>
            {activeFilters.map((f) => (
              <Tag key={f} color="blue">{f}</Tag>
            ))}
          </Space>
        )}
      </Space>

      <Tabs
        activeKey={activeTab}
        onChange={(k) => { setActiveTab(k as 'all' | 'trash'); setSelectedKeys([]) }}
        items={[
          { key: 'all', label: '全部用例' },
          { key: 'trash', label: '回收站' },
        ]}
      />

      <Card
        bordered={false}
        style={PANEL_CARD_STYLE}
        title={
          <Space>
            <span>共 {filtered.length} 条</span>
          </Space>
        }
        extra={
          <Space>
            {selectedKeys.length > 0 && (
              <>
                <Dropdown menu={{ items: [
                  { key: 'xlsx', label: '导出为表格(Excel)', onClick: () => window.open(testCasesApi.exportUrl('xlsx', { ids: selectedKeys })) },
                  { key: 'md', label: '导出为 Markdown', onClick: () => window.open(testCasesApi.exportUrl('md', { ids: selectedKeys })) },
                ] }}>
                  <Button size="small">导出选中（{selectedKeys.length}）▾</Button>
                </Dropdown>
                {activeTab === 'trash'
                  ? <Button size="small" danger onClick={handleBatchPurge}>永久删除（{selectedKeys.length}）</Button>
                  : <Button size="small" danger onClick={handleBatchDelete}>批量删除（{selectedKeys.length}）</Button>}
              </>
            )}
            <Dropdown menu={{ items: [
              { key: 'xlsx', label: '导出为表格(Excel)', onClick: () => window.open(testCasesApi.exportUrl('xlsx', { projectId: exportPid })) },
              { key: 'md', label: '导出为 Markdown', onClick: () => window.open(testCasesApi.exportUrl('md', { projectId: exportPid })) },
            ] }}>
              <Button size="small">导出 ▾</Button>
            </Dropdown>
            <Upload showUploadList={false} accept=".xmind,.xlsx,.xls,.md,.markdown,.docx,.doc" beforeUpload={handleImport}>
              <Button size="small" type="primary">导入用例</Button>
            </Upload>
            {activeFilters.length > 0 && (
              <Button
                size="small"
                onClick={() => {
                  setPriority(undefined)
                  setModuleFilter(undefined)
                  setPlatformFilter(undefined)
                  setCaseTypeFilter(undefined)
                  setAutoFilter(undefined)
                  setProjectFilter(undefined)
                }}
              >
                清除筛选
              </Button>
            )}
          </Space>
        }
      >
        <Space style={{ marginBottom: 12 }} wrap>
          <Input.Search
            placeholder="搜索用例标题"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            style={{ width: 260 }}
          />
          <Select
            placeholder="优先级"
            allowClear
            style={{ width: 110 }}
            value={priority}
            onChange={setPriority}
            options={[
              { value: 'P0', label: 'P0' },
              { value: 'P1', label: 'P1' },
              { value: 'P2', label: 'P2' },
            ]}
          />
          <Select
            placeholder="模块"
            allowClear
            style={{ width: 140 }}
            value={moduleFilter}
            onChange={setModuleFilter}
            options={moduleOptions.map((o) => ({ value: o.key, label: o.label }))}
          />
          <Select
            placeholder="端"
            allowClear
            style={{ width: 140 }}
            value={platformFilter}
            onChange={setPlatformFilter}
            options={platformOptions.map((o) => ({ value: o.key, label: o.label }))}
          />
          <Select
            placeholder="场景类型"
            allowClear
            style={{ width: 110 }}
            value={caseTypeFilter}
            onChange={setCaseTypeFilter}
            options={categoryOptions.map((o) => ({ value: o.key, label: o.label }))}
          />
          <Select
            placeholder="是否自动测试"
            allowClear
            style={{ width: 130 }}
            value={autoFilter}
            onChange={setAutoFilter}
            options={[
              { value: 'auto', label: '自动测试' },
              { value: 'manual', label: '手动测试' },
            ]}
          />
          <Select
            placeholder="项目筛选"
            allowClear
            style={{ width: 160 }}
            value={projectFilter}
            onChange={setProjectFilter}
            options={projects.map((p) => ({ value: p.id, label: p.name }))}
          />
        </Space>

        <Table
          className="tech-table"
          rowKey="id"
          dataSource={filtered}
          columns={columns}
          loading={loading}
          rowSelection={{ selectedRowKeys: selectedKeys, onChange: (k) => setSelectedKeys(k as string[]) }}
          scroll={{ x: 1300 }}
          pagination={{ defaultPageSize: 20, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: (t) => `共 ${t} 条` }}
          locale={{
            emptyText: (
              <Empty
                image={Empty.PRESENTED_IMAGE_SIMPLE}
                description={activeTab === 'trash' ? '回收站为空' : '暂无用例'}
                style={{ padding: '32px 0' }}
              />
            ),
          }}
        />
      </Card>

      {/* Detail / Edit Drawer */}
      <Drawer
        title={
          <Space>
            <span
              style={{
                maxWidth: 380,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
                display: 'inline-block',
              }}
            >
              {selected?.title}
            </span>
            {!editMode && (
              <Button size="small" icon={<EditOutlined />} onClick={startEdit}>编辑</Button>
            )}
          </Space>
        }
        open={!!selected}
        onClose={() => { setSelected(null); setEditMode(false) }}
        width={720}
        extra={
          editMode ? (
            <Space>
              <Button onClick={() => setEditMode(false)}>取消</Button>
              <Button type="primary" loading={saving} onClick={handleSave}>保存</Button>
            </Space>
          ) : (
            <Space>
              <Button type="primary" icon={<ThunderboltOutlined />} onClick={() => selected && openExecModal([selected.id])}>执行测试</Button>
              <Button loading={autoBusy === 'review'} disabled={!selected?.generated_artifacts} onClick={runReview}>Review</Button>
              <Popconfirm title="提交到框架仓库" description="先静态 review，通过后在独立分支提交" okText="提交并push" cancelText="仅本地commit"
                onConfirm={() => runCommit(true)} onCancel={() => runCommit(false)}>
                <Button loading={autoBusy === 'commit'} disabled={!selected?.generated_artifacts}>提交</Button>
              </Popconfirm>
            </Space>
          )
        }
      >
        {selected && !editMode && (
          <>
            <Descriptions column={1} bordered size="small">
              <Descriptions.Item label="用例ID">{selected.case_id || '-'}</Descriptions.Item>
              <Descriptions.Item label="优先级">
                <PriorityPill value={selected.priority} />
              </Descriptions.Item>
              <Descriptions.Item label="场景类型">
                {selected.case_type ? <Tag>{selected.case_type}</Tag> : '-'}
              </Descriptions.Item>
              <Descriptions.Item label="预期结果">{selected.expected_result || '-'}</Descriptions.Item>
              {selected.source_issue_point && (
                <Descriptions.Item label="来源问题点">{selected.source_issue_point}</Descriptions.Item>
              )}
              <Descriptions.Item label="模块">
                {(selected.modules || []).map((m: string) => <Tag key={m}>{m}</Tag>)}
              </Descriptions.Item>
              <Descriptions.Item label="适用端">
                {(selected.platforms || []).map((p: string) => <Tag key={p} color="blue">{p}</Tag>)}
              </Descriptions.Item>
            </Descriptions>

            <List
              size="small"
              header={<strong>前置条件</strong>}
              dataSource={selected.preconditions || []}
              style={{ marginTop: 16 }}
              renderItem={(item: string) => <List.Item>{item}</List.Item>}
              locale={{ emptyText: '无' }}
            />

            <div style={{ marginTop: 16, marginBottom: 8, fontWeight: 600 }}>测试步骤</div>
            <Table
              size="small"
              rowKey="seq"
              pagination={false}
              dataSource={selected.steps || []}
              columns={[
                { title: '#', dataIndex: 'seq', key: 'seq', width: 50 },
                { title: '操作', dataIndex: 'action', key: 'action' },
                { title: '预期', dataIndex: 'expected', key: 'expected' },
              ]}
            />

            {selected.script && (
              <div style={{ marginTop: 16 }}>
                <div style={{ marginBottom: 8, fontWeight: 600 }}>测试脚本</div>
                <pre style={{
                  fontSize: 12, maxHeight: 400, overflow: 'auto', padding: 12,
                  background: '#0E1726', color: '#C7D2E5', borderRadius: 8,
                  fontFamily: "'SF Mono', 'JetBrains Mono', ui-monospace, Menlo, Consolas, monospace",
                  lineHeight: 1.6,
                }}>
                  {selected.script}
                </pre>
              </div>
            )}
          </>
        )}

        {selected && editMode && (
          <Form form={form} layout="vertical">
            <Form.Item name="title" label="标题" rules={[{ required: true, message: '请输入标题' }]}>
              <Input />
            </Form.Item>
            <Row gutter={16}>
              <Col span={12}>
                <Form.Item name="priority" label="优先级" rules={[{ required: true }]}>
                  <Select
                    options={[
                      { value: 'P0', label: 'P0' },
                      { value: 'P1', label: 'P1' },
                      { value: 'P2', label: 'P2' },
                    ]}
                  />
                </Form.Item>
              </Col>
              <Col span={12}>
                <Form.Item name="case_type" label="场景类型" rules={[{ required: true }]}>
                  <Select
                    options={categoryOptions.map((o) => ({ value: o.key, label: o.label }))}
                  />
                </Form.Item>
              </Col>
            </Row>
            <Form.Item name="modules" label="模块">
              <Select
                mode="multiple"
                options={moduleOptions.map((o) => ({ value: o.key, label: o.label }))}
              />
            </Form.Item>
            <Form.Item name="platforms" label="适用端">
              <Select
                mode="multiple"
                options={platformOptions.map((o) => ({ value: o.key, label: o.label }))}
              />
            </Form.Item>
            <Form.Item name="expected_result" label="预期结果">
              <Input.TextArea rows={3} />
            </Form.Item>
            <Form.Item label="前置条件">
              <Form.List name="preconditions">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map((field) => (
                      <Space
                        key={field.key}
                        style={{ display: 'flex', marginBottom: 4 }}
                        align="center"
                      >
                        <Form.Item name={field.name} style={{ marginBottom: 0 }}>
                          <Input style={{ width: 480 }} />
                        </Form.Item>
                        <Button size="small" danger onClick={() => remove(field.name)}>删除</Button>
                      </Space>
                    ))}
                    <Button size="small" onClick={() => add('')}>+ 添加前置条件</Button>
                  </>
                )}
              </Form.List>
            </Form.Item>
            <Form.Item label="测试步骤">
              <Form.List name="steps">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map((field) => (
                      <Card
                        key={field.key}
                        size="small"
                        style={{ marginBottom: 8 }}
                        extra={
                          <Button size="small" danger onClick={() => remove(field.name)}>删除</Button>
                        }
                      >
                        <Space wrap>
                          <Form.Item
                            name={[field.name, 'seq']}
                            label="序号"
                            style={{ marginBottom: 0, width: 80 }}
                          >
                            <Input type="number" />
                          </Form.Item>
                          <Form.Item
                            name={[field.name, 'action']}
                            label="操作"
                            style={{ marginBottom: 0, width: 220 }}
                          >
                            <Input />
                          </Form.Item>
                          <Form.Item
                            name={[field.name, 'expected']}
                            label="预期"
                            style={{ marginBottom: 0, width: 220 }}
                          >
                            <Input />
                          </Form.Item>
                        </Space>
                      </Card>
                    ))}
                    <Button
                      size="small"
                      onClick={() => add({ seq: fields.length + 1, action: '', expected: '' })}
                    >
                      + 添加步骤
                    </Button>
                  </>
                )}
              </Form.List>
            </Form.Item>
          </Form>
        )}
      </Drawer>

      {/* Operation Logs Modal */}
      <Modal
        title={`操作记录 — ${logCase?.title || ''}`}
        open={!!logCase}
        onCancel={() => setLogCase(null)}
        footer={null}
        width={860}
      >
        {logsLoading ? (
          <Spin style={{ display: 'block', margin: '24px auto' }} />
        ) : logs.length === 0 ? (
          <Empty description="暂无操作记录" />
        ) : (
          <Table
            size="small"
            rowKey="id"
            pagination={false}
            dataSource={logs}
            columns={[
              {
                title: '时间',
                dataIndex: 'created_at',
                key: 'created_at',
                width: 150,
                render: (v: string) => (v ? dayjs(v).format('YYYY-MM-DD HH:mm') : '-'),
              },
              {
                title: '操作类型',
                dataIndex: 'operation',
                key: 'operation',
                width: 90,
                render: (v: string) => {
                  const map: Record<string, { label: string; color: string }> = {
                    create: { label: '新建', color: 'success' },
                    update: { label: '修改', color: 'processing' },
                    delete: { label: '删除', color: 'error' },
                    reuse: { label: '复用纳入', color: 'cyan' },
                    manual_pass: { label: '手动通过', color: 'gold' },
                    manual_fail: { label: '手动失败', color: 'error' },
                    restore: { label: '恢复', color: 'blue' },
                    purge: { label: '彻底删除', color: 'error' },
                  }
                  const item = map[v] || { label: v, color: 'default' }
                  return <Tag color={item.color}>{item.label}</Tag>
                },
              },
              {
                title: '操作人',
                dataIndex: 'operator',
                key: 'operator',
                width: 90,
              },
              {
                title: '备注',
                key: 'note',
                ellipsis: true,
                render: (_: any, row: any) => row.snapshot?.note || '-',
              },
            ]}
          />
        )}
      </Modal>

      {/* 执行测试配置弹窗（按端分流，与需求详情一致） */}
      <ExecConfigModal
        open={execModalOpen}
        cases={data.filter((c: any) => pendingCaseIds.includes(c.id))}
        categorizeCase={categorizeCaseByPlatform}
        execApiBaseUrl={execApiBaseUrl}
        setExecApiBaseUrl={setExecApiBaseUrl}
        onCancel={() => setExecModalOpen(false)}
        onConfirm={handleExecuteConfirm}
      />
    </div>
  )
}
