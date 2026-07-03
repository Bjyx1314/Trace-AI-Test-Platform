import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Table, Button, Modal, Upload, Tag, Space, Typography, message, Card, Input, Select,
} from 'antd'
import { UploadOutlined, CloudSyncOutlined, InboxOutlined } from '@ant-design/icons'
import type { UploadProps } from 'antd'
import { requirementsApi, pipelineApi, slicesApi } from '../api'
import { confirmDialog } from '../components/ConfirmModal'
import { useProjectStore } from '../store/projectStore'
import type { Project } from '../types/api'
import { PANEL_CARD_STYLE } from '../styles/theme'
import dayjs from 'dayjs'

const STATUS_COLOR: Record<string, string> = {
  pending_analysis: 'default',
  analyzing: 'processing',
  pending_case_generation: 'cyan',
  generating_cases: 'processing',
  pending_test: 'blue',
  testing: 'warning',
  done: 'success',
}

const STATUS_LABEL: Record<string, string> = {
  pending_analysis: '待需求分析',
  analyzing: '分析中',
  pending_case_generation: '待生成用例',
  generating_cases: '生成用例中',
  pending_test: '待测试',
  testing: '测试中',
  done: '已完成',
}

export default function Requirements() {
  const navigate = useNavigate()
  const { currentProject, projects } = useProjectStore()
  const [data, setData] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [modalOpen, setModalOpen] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [feishuModalOpen, setFeishuModalOpen] = useState(false)
  const [feishuLink, setFeishuLink] = useState('')
  const [linkSyncing, setLinkSyncing] = useState(false)
  const [searchText, setSearchText] = useState('')
  const [appliedSearch, setAppliedSearch] = useState('')
  const [batchSyncing, setBatchSyncing] = useState(false)
  const [externalProjects, setExternalProjects] = useState<{ id: string; name: string }[]>([])
  const [externalProjectId, setExternalProjectId] = useState<string | undefined>(undefined)
  const [projectFilter, setProjectFilter] = useState<string | undefined>(undefined)
  const [iterationFilter, setIterationFilter] = useState<string | undefined>(undefined)
  const [sliceMap, setSliceMap] = useState<Record<string, any[]>>({})  // 展开时懒加载各需求的切片(负责范围)
  const [expandedId, setExpandedId] = useState<string | undefined>()    // 单行展开(手风琴)
  const BRAND = '#D97757'  // brand-solid 橙

  const toggleExpand = (record: any) => {
    if (expandedId === record.id) { setExpandedId(undefined); return }
    setExpandedId(record.id)
    if (!sliceMap[record.id]) {
      slicesApi.list(record.id).then((r) => setSliceMap((m) => ({ ...m, [record.id]: r.data })))
    }
  }

  const effectiveProjectId = projectFilter !== undefined ? projectFilter : currentProject?.id

  const load = () => {
    setLoading(true)
    requirementsApi.list({
      project_id: effectiveProjectId || undefined,
      iteration: iterationFilter || undefined,
    }).then((r) => setData(r.data)).finally(() => setLoading(false))
  }

  useEffect(load, [currentProject?.id, projectFilter, iterationFilter])

  const handleUpload: UploadProps['customRequest'] = async (options) => {
    const { file, onSuccess, onError } = options
    if (!currentProject) return
    setUploading(true)
    try {
      const r = await requirementsApi.upload(currentProject.id, file as File)
      message.success(`需求已创建: ${r.data.title}`)
      onSuccess?.(r.data)
      setModalOpen(false)
      load()
    } catch (err: any) {
      onError?.(err as Error)
      message.error(err?.response?.data?.detail || '上传解析失败，请检查文件格式')
    } finally {
      setUploading(false)
    }
  }

  const handleAnalyze = async (row: any) => {
    if (row.analysis_result) {
      navigate(`/requirements/${row.id}#analysis`)
      return
    }
    try {
      await pipelineApi.analyze(row.id)
      message.info('需求分析已启动')
      navigate(`/requirements/${row.id}#analysis`)
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '需求分析启动失败，请稍后重试')
    }
  }

  const handleGenerateCases = async (row: any) => {
    if (['pending_test', 'testing', 'done'].includes(row.status)) {
      navigate(`/requirements/${row.id}#testcases`)
      return
    }
    if (!row.analysis_result) {
      message.warning('请先完成需求分析')
      navigate(`/requirements/${row.id}#analysis`)
      return
    }
    const allConfirmed = (row.analysis_result.issue_points || []).every((ip: any) =>
      (ip.confirmation_points || []).every((cp: any) => cp.status === 'confirmed')
    )
    if (!allConfirmed) {
      message.warning('请确认所有待确认点后再生成用例')
      navigate(`/requirements/${row.id}#analysis`)
      return
    }
    try {
      await pipelineApi.generateCases(row.id)
      message.info('用例生成已启动')
      navigate(`/requirements/${row.id}#testcases`)
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '用例生成启动失败，请稍后重试')
    }
  }

  const handleExecute = (row: any) => {
    navigate(`/requirements/${row.id}#testcases`, { state: { openExecModal: true } })
  }

  const handleSyncFeishuLink = async () => {
    if (!currentProject || !feishuLink.trim()) return
    setLinkSyncing(true)
    try {
      const r = await requirementsApi.syncFeishuLink(currentProject.id, feishuLink.trim())
      message.success(`已同步需求: ${r.data.title}`)
      setFeishuLink('')
      setFeishuModalOpen(false)
      load()
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '同步失败，请检查链接')
    } finally {
      setLinkSyncing(false)
    }
  }

  const handleBatchSync = async () => {
    if (!currentProject) return
    setBatchSyncing(true)
    try {
      const r = await requirementsApi.syncExternal(currentProject.id, externalProjectId || undefined)
      message.success(`已批量同步 ${r.data.length} 条需求`)
      setFeishuModalOpen(false)
      load()
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '批量同步需求失败')
    } finally {
      setBatchSyncing(false)
    }
  }

  const loadExternalProjects = async () => {
    try {
      const r = await requirementsApi.externalProjects()
      setExternalProjects(r.data || [])
    } catch {
      setExternalProjects([])
    }
  }

  const handleSearch = (value: string) => {
    setAppliedSearch(value)
    if (!value.trim()) load()
  }

  const displayData = appliedSearch.trim()
    ? data.filter((d) => d.title?.toLowerCase().includes(appliedSearch.trim().toLowerCase()))
    : data

  const inProgress = (status: string) => ['analyzing', 'generating_cases'].includes(status)

  const iterationOptions = Array.from(new Set(data.map((d) => d.iteration).filter(Boolean))).map((v) => ({
    value: v as string,
    label: v as string,
  }))

  const columns = [
    {
      title: '标题', dataIndex: 'title', key: 'title',
      render: (v: string, row: any) => {
        if (row.__loading) return <span style={{ color: '#94A3B8', fontSize: 12, paddingLeft: 24 }}>加载中…</span>
        if (row.__sub) {
          return (
            <span style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', whiteSpace: 'normal' }}>
              <span style={{ width: 3, height: 18, borderRadius: 2, background: BRAND, flex: 'none' }} />
              <span style={{ fontSize: 13, fontWeight: 500, color: '#334155', paddingLeft: 4 }}>{row.scope_label}</span>
              {row.has_pending && <span style={{ fontSize: 11, fontWeight: 600, padding: '2px 8px', borderRadius: 5, background: '#FEF6E7', color: '#B5710A', border: '1px solid #FBE2B0', whiteSpace: 'nowrap' }}>有新增待分析</span>}
              {row.owner_name && <><span style={{ color: '#D1D9E0', fontSize: 12 }}>·</span><span style={{ fontSize: 12, color: '#94A3B8' }}>{row.owner_name}</span></>}
            </span>
          )
        }
        const expanded = expandedId === row.id
        const hasSub = (row.slice_count || 0) > 0
        return (
          <span style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
            {hasSub ? (
              <span className="ms" onClick={(e) => { e.stopPropagation(); toggleExpand(row) }}
                style={{ fontSize: 16, color: expanded ? BRAND : '#B0BAC4', transform: expanded ? 'rotate(90deg)' : 'none', transition: 'transform .2s, color .15s', cursor: 'pointer', flex: 'none' }}>chevron_right</span>
            ) : <span style={{ width: 16, flex: 'none' }} />}
            <a className="row-title" onClick={() => navigate(`/requirements/${row.id}`)}>{v}</a>
          </span>
        )
      },
    },
    {
      title: '迭代', dataIndex: 'iteration', key: 'iteration', width: 110,
      render: (v: string, row: any) => (row.__sub || row.__loading) ? <span style={{ color: '#bbb' }}>—</span> : (v ? <Tag>{v}</Tag> : <span style={{ color: '#bbb' }}>—</span>),
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 110,
      render: (v: string, row: any) => row.__loading ? null : <Tag color={STATUS_COLOR[v] || 'default'}>{STATUS_LABEL[v] || v}</Tag>,
    },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 180,
      render: (v: string, row: any) => (row.__sub || row.__loading) ? <span style={{ color: '#bbb' }}>—</span> : dayjs(v).format('YYYY-MM-DD HH:mm'),
    },
    {
      title: '操作', key: 'action', width: 280,
      render: (_: any, row: any) => {
        if (row.__loading) return null
        if (row.__sub) {
          // 子范围行：按钮只针对该范围 → 跳详情并激活该范围去操作
          const reqId = row.requirement_id
          const go = (hash: string, extra?: any) => navigate(`/requirements/${reqId}${hash}`, { state: { activeSliceId: row.id, ...extra } })
          return (
            <Space>
              <Button type="link" size="small" onClick={() => go('#analysis')} disabled={inProgress(row.status)}>需求分析</Button>
              <Button type="link" size="small" onClick={() => go('#testcases')} disabled={inProgress(row.status)}>生成用例</Button>
              <Button type="link" size="small" onClick={() => go('#testcases', { openExecModal: true })}>执行测试</Button>
            </Space>
          )
        }
        return (
          <Space>
            <Button type="link" size="small" onClick={() => handleAnalyze(row)} disabled={inProgress(row.status)}>需求分析</Button>
            <Button type="link" size="small" onClick={() => handleGenerateCases(row)} disabled={inProgress(row.status)}>生成用例</Button>
            <Button type="link" size="small" onClick={() => handleExecute(row)}>执行测试</Button>
            <Button type="link" size="small" danger
              onClick={async () => { if (await confirmDialog({ title: '删除需求', desc: `确认删除「${row.title}」？`, ok: '删除', danger: true })) requirementsApi.delete(row.id).then(load) }}>删除</Button>
          </Space>
        )
      },
    },
  ]

  // 扁平化：父需求行 + (展开时)其非全文子范围作为真实子 <tr>
  const flatRows: any[] = []
  displayData.forEach((r: any) => {
    flatRows.push(r)
    if (expandedId === r.id) {
      const sub = sliceMap[r.id]
      if (!sub) flatRows.push({ __sub: true, __loading: true, __key: `${r.id}:loading` })
      else sub.filter((s) => !s.is_default).forEach((s) => flatRows.push({ __sub: true, __key: `${r.id}:${s.id}`, ...s }))
    }
  })

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 16 }}>
        <div />
        <Space>
          <Button icon={<CloudSyncOutlined />} onClick={() => setFeishuModalOpen(true)} disabled={!currentProject}>
            同步需求
          </Button>
          <Button type="primary" icon={<UploadOutlined />} onClick={() => setModalOpen(true)} disabled={!currentProject}>
            上传需求
          </Button>
        </Space>
      </div>

      <div style={{ marginBottom: 16, display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <Select
          placeholder="全部项目"
          value={projectFilter}
          onChange={(v) => setProjectFilter(v)}
          allowClear
          onClear={() => setProjectFilter(undefined)}
          style={{ width: 180 }}
          options={projects.map((p: any) => ({ value: p.id, label: p.name }))}
        />
        <Select
          placeholder="全部迭代"
          value={iterationFilter}
          onChange={(v) => setIterationFilter(v)}
          allowClear
          onClear={() => setIterationFilter(undefined)}
          style={{ width: 160 }}
          options={iterationOptions}
        />
        <Input.Search
          placeholder="搜索需求标题"
          value={searchText}
          onChange={(e) => setSearchText(e.target.value)}
          onSearch={handleSearch}
          style={{ width: 300 }}
          allowClear
          onClear={() => { setSearchText(''); setAppliedSearch(''); load() }}
        />
      </div>

      <Card bordered={false} style={PANEL_CARD_STYLE}>
        <style>{`
          .slice-tbl .ant-table-tbody > tr > td, .slice-tbl .ant-table-thead > tr > th { white-space: nowrap; }
          .slice-tbl .ant-table-tbody > tr > td:first-child, .slice-tbl .ant-table-thead > tr > th:first-child { white-space: normal; }
          .slice-tbl tr.slice-sub > td { background: #FAFBFC; border-top: 1px solid #F0F4F6; }
          .slice-tbl tr.slice-sub:hover > td { background: #F3F6F8 !important; }
        `}</style>
        <Table
          className="slice-tbl"
          rowKey={(rec: any) => rec.__sub ? rec.__key : rec.id}
          rowClassName={(rec: any) => rec.__sub ? 'slice-sub' : ''}
          dataSource={flatRows}
          columns={columns}
          loading={loading}
        />
      </Card>

      <Modal
        title="上传需求"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        footer={null}
        destroyOnClose
      >
        <Upload.Dragger
          accept=".txt,.md,.docx,.pdf,.png,.jpg,.jpeg,.webp,.gif"
          maxCount={1}
          showUploadList={false}
          disabled={uploading}
          customRequest={handleUpload}
        >
          <p className="ant-upload-drag-icon"><InboxOutlined /></p>
          <p className="ant-upload-text">点击或拖拽文件到此处上传</p>
          <p className="ant-upload-hint">
            支持需求文档（.txt / .md / .docx / .pdf）或需求图片（.png / .jpg / .webp 等）
          </p>
        </Upload.Dragger>
        {uploading && <div style={{ textAlign: 'center', marginTop: 12 }}>上传中，请稍候...</div>}
      </Modal>

      <Modal title="同步需求" open={feishuModalOpen} onCancel={() => setFeishuModalOpen(false)} footer={null}
        afterOpenChange={(open) => { if (open) loadExternalProjects() }}>
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <div>
            <Typography.Text strong>按飞书文档链接同步单条需求</Typography.Text>
            <Space.Compact style={{ marginTop: 8, width: '100%' }}>
              <Input
                placeholder="粘贴飞书文档链接"
                value={feishuLink}
                onChange={(e) => setFeishuLink(e.target.value)}
              />
              <Button type="primary" onClick={handleSyncFeishuLink} loading={linkSyncing}>同步</Button>
            </Space.Compact>
          </div>
          <div>
            <Typography.Text strong>批量同步需求</Typography.Text>
            <Space.Compact style={{ marginTop: 8, width: '100%' }}>
              <Select
                style={{ flex: 1 }}
                allowClear
                placeholder="选择来源项目（不选则拉取全部可见需求）"
                value={externalProjectId}
                onChange={(v) => setExternalProjectId(v)}
                options={externalProjects.map((p) => ({ value: p.id, label: p.name }))}
              />
              <Button type="primary" icon={<CloudSyncOutlined />} onClick={handleBatchSync} loading={batchSyncing}>
                同步
              </Button>
            </Space.Compact>
            <Typography.Text type="secondary" style={{ fontSize: 12 }}>
              根据项目批量拉取需求到当前平台项目，按id去重
            </Typography.Text>
          </div>
        </Space>
      </Modal>
    </div>
  )
}
