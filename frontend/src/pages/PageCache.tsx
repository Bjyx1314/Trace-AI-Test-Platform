import { useEffect, useState } from 'react'
import {
  Card, Table, Tag, Button, Space, Drawer, Input, Select,
  Modal, message, Typography, Descriptions, List, Empty,
  Alert,
} from 'antd'
import {
  PlusOutlined, DeleteOutlined, ReloadOutlined,
  GlobalOutlined, SearchOutlined, VideoCameraOutlined,
} from '@ant-design/icons'
import { pageCacheApi, enumsApi } from '../api'
import { confirmDialog } from '../components/ConfirmModal'
import { useProjectStore } from '../store/projectStore'
import { PANEL_CARD_STYLE, MONO_FONT } from '../styles/theme'
import type { PageStructureCache } from '../types/api'

const STATUS_COLOR: Record<string, string> = {
  active: 'success',
  stale: 'warning',
  needs_update: 'error',
}

const STATUS_LABEL: Record<string, string> = {
  active: '有效',
  stale: '已过期',
  needs_update: '待更新',
}

type ExploreResult = {
  explored_count: number
  created_count: number
  updated_count: number
  existing_paths: { path: string; url_pattern: string; page_name: string }[]
}

export default function PageCache() {
  const currentProject = useProjectStore((s) => s.currentProject)
  const [data, setData] = useState<PageStructureCache[]>([])
  const [loading, setLoading] = useState(false)
  const [detailDrawer, setDetailDrawer] = useState<PageStructureCache | null>(null)
  const [keyword, setKeyword] = useState('')

  // 人工录入（Playwright 录制）state
  const [recordModal, setRecordModal] = useState(false)
  const [recordBaseUrl, setRecordBaseUrl] = useState<string | undefined>()
  const [recordStartPath, setRecordStartPath] = useState('')
  const [recording, setRecording] = useState(false)
  const [recorderAvailable, setRecorderAvailable] = useState<boolean | null>(null)

  // AI exploration state
  const [baseUrl, setBaseUrl] = useState<string | undefined>()
  const [baseUrlOptions, setBaseUrlOptions] = useState<Array<{ value: string; label: string }>>([])
  // base_url 端名映射：端名/URL（含去尾斜杠）→ 端名，用于「PC 端」列显示端名
  const [baseUrlNameMap, setBaseUrlNameMap] = useState<Record<string, string>>({})
  // 多条待探索路径：path + 可选描述
  const [paths, setPaths] = useState<Array<{ path: string; description: string }>>([{ path: '', description: '' }])
  const [exploring, setExploring] = useState(false)
  const [exploreResult, setExploreResult] = useState<ExploreResult | null>(null)

  const load = () => {
    if (!currentProject) return
    setLoading(true)
    pageCacheApi.list(currentProject.id)
      .then((r) => setData(r.data))
      .finally(() => setLoading(false))
  }

  useEffect(() => { load() }, [currentProject?.id])

  useEffect(() => {
    enumsApi.list('base_url').then((r) => {
      // value 用真实 URL(label)，端 key 仅作展示；探索/录制要拿 URL 去浏览
      setBaseUrlOptions(r.data.map((e) => ({ value: e.label || e.key, label: e.label ? `${e.label}（${e.key}）` : e.key })))
      const map: Record<string, string> = {}
      r.data.forEach((e: any) => {
        map[e.key] = e.key                       // 端名 → 端名
        if (e.label) {                           // URL → 端名（含去尾斜杠）
          map[e.label] = e.key
          map[e.label.replace(/\/$/, '')] = e.key
        }
      })
      setBaseUrlNameMap(map)
    })
    // 探测本机是否具备 Playwright 录制能力
    pageCacheApi.recorderStatus()
      .then((r) => setRecorderAvailable(r.data.available))
      .catch(() => setRecorderAvailable(false))
  }, [])

  // 人工录入：启动 Playwright 录制（请求会阻塞到用户关闭录制浏览器）
  const handleRecord = async () => {
    if (!currentProject || !recordBaseUrl) {
      message.warning('请先选择 PC 端地址')
      return
    }
    setRecording(true)
    try {
      const r = await pageCacheApi.record({
        project_id: currentProject.id,
        base_url: recordBaseUrl,
        start_path: recordStartPath.trim() || undefined,
        overwrite: false,
      })
      const d = r.data
      const finish = (created: number, updated: number, skipped: number) => {
        message.success(`录制完成：新建 ${created} 个，更新 ${updated} 个${skipped ? `，跳过已存在 ${skipped} 个` : ''}`)
        setRecordModal(false)
        load()
      }
      if (d.existing_paths.length > 0) {
        const okd = await confirmDialog({
          title: '部分页面已存在缓存',
          desc: `以下录制到的页面已缓存，是否重新缓存（覆盖）？\n${d.existing_paths.map((e) => e.page_name || e.url_pattern).join('、')}`,
          ok: '重新缓存', cancel: '跳过这些', danger: true,
        })
        if (okd) {
          const r2 = await pageCacheApi.record({
            project_id: currentProject.id,
            base_url: recordBaseUrl,
            start_path: recordStartPath.trim() || undefined,
            overwrite: true,
          })
          finish(r2.data.created_count, r2.data.updated_count, 0)
        } else {
          finish(d.created_count, d.updated_count, d.existing_paths.length)
        }
      } else {
        finish(d.created_count, d.updated_count, 0)
      }
    } catch (e) {
      const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      message.error(detail || '录制失败，请重试')
    } finally {
      setRecording(false)
    }
  }

  const runExplore = async (
    items: Array<{ path: string; description: string }>,
    overwrite: boolean,
  ): Promise<ExploreResult | null> => {
    const r = await pageCacheApi.explore({
      project_id: currentProject!.id,
      base_url: baseUrl!,
      paths: items
        .filter((p) => p.path.trim())
        .map((p) => ({ path: p.path.trim(), description: p.description.trim() || undefined })),
      overwrite,
    })
    return r.data
  }

  const handleExplore = async () => {
    if (!currentProject || !baseUrl) {
      message.warning('请先选择 PC 端地址')
      return
    }
    const validPaths = paths.filter((p) => p.path.trim())
    if (validPaths.length === 0) {
      message.warning('请至少补充一条需要探索的路径')
      return
    }
    setExploring(true)
    setExploreResult(null)
    try {
      const data = await runExplore(validPaths, false)
      if (!data) return

      // 有已存在路径 → 弹框确认是否重新缓存
      if (data.existing_paths.length > 0) {
        const existSet = new Set(data.existing_paths.map((e) => e.path))
        const okd = await confirmDialog({
          title: '部分页面已存在缓存',
          desc: `以下页面已缓存，是否重新缓存（覆盖）？\n${data.existing_paths.map((e) => e.page_name || e.path).join('、')}`,
          ok: '重新缓存', cancel: '跳过这些', danger: true,
        })
        if (okd) {
          const reExplore = validPaths.filter((p) => existSet.has(p.path.trim()))
          const data2 = await runExplore(reExplore, true)
          const created = data.created_count + (data2?.created_count ?? 0)
          const updated = data.updated_count + (data2?.updated_count ?? 0)
          setExploreResult({ ...data, created_count: created, updated_count: updated, explored_count: created + updated, existing_paths: [] })
          message.success(`探索完成：新建 ${created} 个，重新缓存 ${updated} 个`)
          load()
        } else {
          setExploreResult(data)
          message.success(`探索完成：新建 ${data.created_count} 个，跳过已存在 ${data.existing_paths.length} 个`)
          load()
        }
      } else {
        setExploreResult(data)
        message.success(`探索完成：新建 ${data.created_count} 个，更新 ${data.updated_count} 个`)
        load()
      }
    } catch {
      message.error('探索失败，请检查配置后重试')
    } finally {
      setExploring(false)
    }
  }

  const columns = [
    {
      // 页面结构缓存不区分环境：去重/更新只看页面结构(页面名 / 去掉 host 的路径 pattern)，与具体地址无关。
      // 这一列只表示"最近一次从哪个地址抓到的"，仅作来源信息展示，不是缓存的身份。
      title: '来源地址',
      dataIndex: 'base_url',
      key: 'base_url',
      width: 150,
      render: (v: string | null) => {
        if (!v) return <Typography.Text type="secondary">—</Typography.Text>
        const name = baseUrlNameMap[v] || baseUrlNameMap[v.replace(/\/$/, '')] || v
        return <span style={{ display: 'inline-flex', alignItems: 'center', gap: 5, fontSize: 12, color: '#1577C2', background: '#EAF4FB', border: '1px solid #C6E1F4', borderRadius: 999, padding: '2px 10px', whiteSpace: 'nowrap' }}><span className="ms" style={{ fontSize: 13 }}>language</span>{name}</span>
      },
    },
    {
      title: '页面名称',
      dataIndex: 'page_name',
      key: 'page_name',
      ellipsis: true,
      render: (v: string, row: PageStructureCache) => (
        <a className="row-title" style={{ fontWeight: 600 }} onClick={() => setDetailDrawer(row)}>{v || row.url_pattern}</a>
      ),
    },
    {
      title: '页面路径',
      dataIndex: 'url_pattern',
      key: 'url_pattern',
      ellipsis: true,
      width: 220,
      render: (v: string, row: PageStructureCache) => (
        <a onClick={() => setDetailDrawer(row)}><Typography.Text code style={{ fontSize: 12 }}>{v}</Typography.Text></a>
      ),
    },
    {
      title: '描述',
      dataIndex: 'description',
      key: 'description',
      ellipsis: true,
      render: (v: string | null) =>
        v ? <span style={{ color: '#64748B' }}>{v}</span> : <Typography.Text type="secondary">—</Typography.Text>,
    },
  ]

  const filteredCache = keyword.trim()
    ? data.filter((d) => {
        const kw = keyword.trim().toLowerCase()
        return (d.page_name || '').toLowerCase().includes(kw) ||
          d.url_pattern.toLowerCase().includes(kw) ||
          (d.base_url || '').toLowerCase().includes(kw) ||
          (d.description || '').toLowerCase().includes(kw)
      })
    : data

  return (
    <div style={{ padding: 24 }}>
      {/* AI Exploration Panel */}
      <Card
        title={
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <div style={{ width: 38, height: 38, borderRadius: 11, background: '#FEF3EE', display: 'flex', alignItems: 'center', justifyContent: 'center', flex: 'none' }}>
              <span className="ms" style={{ fontSize: 22, color: '#D97757' }}>travel_explore</span>
            </div>
            <div style={{ lineHeight: 1.3 }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: '#0F172A' }}>AI 自动探索</div>
              <div style={{ fontSize: 12, fontWeight: 400, color: '#94A3B8' }}>选择端点与路径，AI 自动记录页面结构到共享缓存</div>
            </div>
          </div>
        }
        bordered={false}
        style={{ ...PANEL_CARD_STYLE, marginBottom: 20 }}
      >
        <div style={{ display: 'flex', gap: 10, padding: '11px 14px', marginBottom: 12, background: '#FEF3EE', border: '1px solid #F5D6C8', borderRadius: 10 }}>
          <span className="ms" style={{ fontSize: 17, color: '#D97757', flex: 'none', marginTop: 1 }}>info</span>
          <div style={{ minWidth: 0 }}>
            <div style={{ fontSize: 12.5, fontWeight: 600, color: '#B5600A', marginBottom: 3 }}>建议优先选择自动探索</div>
            <div style={{ fontSize: 11.5, color: '#9A6B4E', lineHeight: 1.65 }}>选择已配置的 PC 端地址，补充需要探索的路径（描述可选），AI 将自动记录这些页面的结构到共享缓存。若自动探索失败，可改用下方「人工录入（录制）」。PC 端地址在「枚举管理 → base_url」中配置。</div>
          </div>
        </div>
        {!currentProject ? (
          <Empty description="请先在右上角选择项目" />
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 18 }}>
            {/* Step 1：选择 PC 端地址 */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 11 }}>
                <span style={{ width: 18, height: 18, borderRadius: '50%', background: '#D97757', color: '#fff', fontSize: 11, fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>1</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#64748B' }}>选择 PC 端地址</span>
              </div>
              {baseUrlOptions.length === 0 ? (
                <Typography.Text type="secondary" style={{ fontSize: 12 }}>请先在「枚举管理 → base_url」配置 PC 端地址</Typography.Text>
              ) : (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 9 }}>
                  {baseUrlOptions.map((o) => {
                    const sel = baseUrl === o.value
                    return (
                      <div key={o.value} onClick={() => setBaseUrl(sel ? undefined : o.value)}
                        style={{ display: 'flex', alignItems: 'center', gap: 9, padding: '11px 14px', borderRadius: 11, cursor: 'pointer', transition: 'all .15s',
                          border: sel ? '1.5px solid #F5D6C8' : '1px solid #E7ECF0', background: sel ? '#FEF3EE' : '#FAFBFC' }}>
                        <span className="ms" style={{ fontSize: 18, color: sel ? '#D97757' : '#94A3B8', flex: 'none' }}>language</span>
                        <div style={{ minWidth: 0, flex: 1 }}>
                          <div style={{ fontSize: 12.5, fontWeight: 600, color: sel ? '#B5600A' : '#475569', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.label}</div>
                          <div style={{ fontSize: 10.5, fontFamily: MONO_FONT, color: '#94A3B8', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{o.value}</div>
                        </div>
                        {sel && <span className="ms" style={{ fontSize: 18, color: '#D97757', marginLeft: 'auto', flex: 'none' }}>check_circle</span>}
                      </div>
                    )
                  })}
                </div>
              )}
            </div>

            {/* Step 2：路径录入 */}
            <div>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 11 }}>
                <span style={{ width: 18, height: 18, borderRadius: '50%', background: '#D97757', color: '#fff', fontSize: 11, fontWeight: 700, display: 'inline-flex', alignItems: 'center', justifyContent: 'center' }}>2</span>
                <span style={{ fontSize: 12, fontWeight: 600, color: '#64748B' }}>需要探索的页面（页面名 + 如何到达，AI 自动导航后记录结构）</span>
              </div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 9 }}>
                {paths.map((p, idx) => (
                  <div key={idx} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <Input placeholder="页面名称(中文，如 订单列表)" value={p.path}
                      onChange={(e) => setPaths((prev) => prev.map((it, i) => i === idx ? { ...it, path: e.target.value } : it))}
                      style={{ flex: 1, maxWidth: 220, height: 36 }} />
                    <Input placeholder="如何到达/具体操作(如 点订单中心→订单列表)" value={p.description}
                      onChange={(e) => setPaths((prev) => prev.map((it, i) => i === idx ? { ...it, description: e.target.value } : it))}
                      style={{ flex: 1, maxWidth: 300, height: 36 }} />
                    <span className="ms" onClick={() => paths.length > 1 && setPaths((prev) => prev.filter((_, i) => i !== idx))}
                      style={{ fontSize: 18, color: paths.length === 1 ? '#E2E8F0' : '#D69A9A', cursor: paths.length === 1 ? 'default' : 'pointer' }}>delete</span>
                  </div>
                ))}
                <span onClick={() => setPaths((prev) => [...prev, { path: '', description: '' }])}
                  style={{ display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, color: '#B5600A', cursor: 'pointer', width: 'fit-content' }}>
                  <span className="ms" style={{ fontSize: 16 }}>add</span>添加路径
                </span>
              </div>
            </div>

            {/* 开始探索 */}
            <div>
              <button onClick={handleExplore} disabled={!baseUrl || exploring}
                style={{ display: 'inline-flex', alignItems: 'center', gap: 6, height: 38, padding: '0 16px', border: 'none', borderRadius: 9, fontSize: 13, fontWeight: 500,
                  cursor: baseUrl && !exploring ? 'pointer' : 'not-allowed', color: '#fff',
                  background: baseUrl ? 'linear-gradient(140deg,#E8930C,#D97757)' : '#CBD5E1',
                  boxShadow: baseUrl ? '0 4px 12px -5px rgba(217,119,87,.5)' : 'none' }}>
                <span className="ms" style={{ fontSize: 18 }}>{exploring ? 'hourglass_top' : 'search'}</span>{exploring ? '探索中…' : '开始探索'}
              </button>
            </div>

            {exploreResult && (
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', background: '#E9F8EF', border: '1px solid #B5E0C8', borderRadius: 10, fontSize: 12.5, color: '#128A43' }}>
                <span className="ms" style={{ fontSize: 16 }}>check_circle</span>
                探索完成：共处理 {exploreResult.explored_count} 个页面，新建 {exploreResult.created_count} 个，更新 {exploreResult.updated_count} 个
              </div>
            )}
          </div>
        )}
      </Card>

      <Card
        title={`已缓存页面结构（${data.length}）`}
        bordered={false}
        style={PANEL_CARD_STYLE}
        extra={
          <Space>
            <Input
              allowClear
              prefix={<SearchOutlined />}
              placeholder="搜索路径 / 描述 / PC端"
              value={keyword}
              onChange={(e) => setKeyword(e.target.value)}
              style={{ width: 220 }}
            />
            <Button icon={<ReloadOutlined />} onClick={load}>刷新</Button>
            <Button
              type="primary"
              icon={<VideoCameraOutlined />}
              onClick={() => { setRecordBaseUrl(undefined); setRecordStartPath(''); setRecordModal(true) }}
              disabled={!currentProject}
            >
              人工录入（录制）
            </Button>
          </Space>
        }
      >
        {!currentProject ? (
          <Empty description="请先在右上角选择项目" />
        ) : (
          <Table
            dataSource={filteredCache}
            columns={columns}
            rowKey="id"
            loading={loading}
            size="small"
            pagination={{ defaultPageSize: 15, showSizeChanger: true, pageSizeOptions: [15, 30, 50, 100], showTotal: (t) => `共 ${t} 条` }}
            locale={{ emptyText: keyword ? '没有匹配的缓存' : '暂无缓存数据，点击「人工录入（录制）」或上方自动探索添加' }}
            scroll={{ x: 800 }}
          />
        )}
      </Card>

      {/* Detail Drawer */}
      <Drawer
        title={detailDrawer ? `${detailDrawer.page_name} — 结构详情` : ''}
        open={!!detailDrawer}
        onClose={() => setDetailDrawer(null)}
        width={600}
        placement="right"
      >
        {detailDrawer && (
          <Space direction="vertical" style={{ width: '100%' }} size="large">
            <Descriptions column={2} size="small" bordered>
              <Descriptions.Item label="页面名称" span={2}>
                <Typography.Text strong>{detailDrawer.page_name || '—'}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="PC 端" span={2}>
                {detailDrawer.base_url || <Typography.Text type="secondary">—</Typography.Text>}
              </Descriptions.Item>
              <Descriptions.Item label="页面路径" span={2}>
                <Typography.Text code>{detailDrawer.url_pattern}</Typography.Text>
              </Descriptions.Item>
              <Descriptions.Item label="描述" span={2}>
                {detailDrawer.description || <Typography.Text type="secondary">—</Typography.Text>}
              </Descriptions.Item>
              <Descriptions.Item label="状态">
                <Tag color={STATUS_COLOR[detailDrawer.status]}>{STATUS_LABEL[detailDrawer.status]}</Tag>
              </Descriptions.Item>
              <Descriptions.Item label="区块数">{detailDrawer.region_count}</Descriptions.Item>
              <Descriptions.Item label="元素数">{detailDrawer.element_count}</Descriptions.Item>
              <Descriptions.Item label="命中次数">{detailDrawer.hit_count}</Descriptions.Item>
              <Descriptions.Item label="最后命中">
                {detailDrawer.last_hit_at
                  ? new Date(detailDrawer.last_hit_at).toLocaleString('zh-CN')
                  : '从未'}
              </Descriptions.Item>
              <Descriptions.Item label="捕获时间">
                {detailDrawer.captured_at
                  ? new Date(detailDrawer.captured_at).toLocaleString('zh-CN')
                  : '—'}
              </Descriptions.Item>
              <Descriptions.Item label="最后更新">
                {detailDrawer.updated_at
                  ? new Date(detailDrawer.updated_at).toLocaleString('zh-CN')
                  : '—'}
              </Descriptions.Item>
            </Descriptions>

            {detailDrawer.dom_hash && Object.keys(detailDrawer.dom_hash).length > 0 && (
              <div>
                <Typography.Text strong>DOM 区块哈希</Typography.Text>
                <div style={{ marginTop: 8 }}>
                  {Object.entries(detailDrawer.dom_hash).map(([region, hash]) => (
                    <div key={region} style={{ display: 'flex', gap: 8, marginBottom: 4 }}>
                      <Tag>{region}</Tag>
                      <Typography.Text code style={{ fontSize: 11, color: '#999' }}>
                        {hash}
                      </Typography.Text>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {detailDrawer.regions && detailDrawer.regions.length > 0 ? (
              <div>
                <Typography.Text strong>页面区块结构</Typography.Text>
                {detailDrawer.regions.map((region, idx) => (
                  <Card
                    key={idx}
                    size="small"
                    title={
                      <Space>
                        <Tag color="blue">{region.name}</Tag>
                        <Typography.Text code style={{ fontSize: 11 }}>{region.selector}</Typography.Text>
                      </Space>
                    }
                    style={{ marginTop: 8 }}
                  >
                    {region.elements && region.elements.length > 0 ? (
                      <List
                        size="small"
                        dataSource={region.elements}
                        renderItem={(el) => (
                          <List.Item>
                            <Space>
                              <Tag color="default">{el.type || 'element'}</Tag>
                              <span>{el.name}</span>
                              <Typography.Text code style={{ fontSize: 11, color: '#999' }}>
                                {el.selector}
                              </Typography.Text>
                            </Space>
                          </List.Item>
                        )}
                      />
                    ) : (
                      <Typography.Text type="secondary">暂无元素定义</Typography.Text>
                    )}
                  </Card>
                ))}
              </div>
            ) : (
              <Empty description="暂无区块结构数据" />
            )}
          </Space>
        )}
      </Drawer>

      {/* 人工录入 — Playwright 录制 Modal */}
      <Modal
        title="人工录入页面结构缓存（Playwright 录制）"
        open={recordModal}
        onOk={handleRecord}
        onCancel={() => { if (!recording) setRecordModal(false) }}
        confirmLoading={recording}
        okText={recording ? '录制中…' : '开始录制'}
        okButtonProps={{ icon: <VideoCameraOutlined />, disabled: !recordBaseUrl || recorderAvailable === false }}
        cancelButtonProps={{ disabled: recording }}
        maskClosable={!recording}
        width={560}
      >
        <Alert
          type="warning"
          showIcon
          style={{ marginBottom: 16 }}
          message="自动探索失败可人工录入，建议优先选择自动探索"
        />
        {recorderAvailable === false && (
          <Alert
            type="error"
            showIcon
            style={{ marginBottom: 16 }}
            message="本机未检测到 Playwright CLI，无法录制"
            description="请在后端所在机器执行：npm i -g playwright && playwright install chromium"
          />
        )}
        <Space direction="vertical" style={{ width: '100%' }} size={12}>
          <div>
            <div style={{ fontSize: 13, color: '#555', marginBottom: 6 }}>① 选择 PC 端地址：</div>
            <Select
              placeholder="选择 PC 端地址"
              value={recordBaseUrl}
              onChange={setRecordBaseUrl}
              style={{ width: '100%' }}
              options={baseUrlOptions}
              allowClear
              disabled={recording}
              notFoundContent="请先在「枚举管理 → base_url」配置 PC 端地址"
            />
          </div>
          <div>
            <div style={{ fontSize: 13, color: '#555', marginBottom: 6 }}>② 起始路径（可选，浏览器打开后直接落在该页）：</div>
            <Input
              placeholder="例：/admin/users（留空则打开 PC 端首页）"
              value={recordStartPath}
              onChange={(e) => setRecordStartPath(e.target.value)}
              disabled={recording}
            />
          </div>
          <Alert
            type="info"
            showIcon
            message="录制说明"
            description={
              <div style={{ fontSize: 13 }}>
                点击「开始录制」后会以 <b>PC 桌面视口</b> 弹出真实浏览器，请在其中
                <b>自主操作</b>需要缓存的页面（点击、跳转、填写等）。
                操作完成后<b>关闭该浏览器窗口即结束录制</b>，系统会自动把访问过的页面结构写入缓存。
              </div>
            }
          />
          {recording && (
            <Alert
              type="success"
              showIcon
              message="录制进行中…请在弹出的浏览器中操作，完成后关闭浏览器窗口以结束录制"
            />
          )}
        </Space>
      </Modal>
    </div>
  )
}
