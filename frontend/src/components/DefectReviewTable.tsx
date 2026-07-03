import { useState } from 'react'
import { Table, Tag, Space, Button, Select, Drawer, Typography, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import dayjs from 'dayjs'
import { defectsApi } from '../api'

// 状态：draft=待复核 → confirmed=待处理 / (ignored|duplicate)=无需处理 / fixed=已解决(再次通过自动复核)
export const DEFECT_STATUS_LABEL: Record<string, string> = {
  draft: '待复核', confirmed: '待处理', ignored: '无需处理', duplicate: '无需处理', ticket_created: '已建单', fixed: '已解决',
}
export const DEFECT_STATUS_COLOR: Record<string, string> = {
  draft: 'gold', confirmed: 'blue', ignored: 'default', duplicate: 'default', ticket_created: 'processing', fixed: 'success',
}

function sevColor(v: string): string {
  if (/P0|1级|致命/.test(v)) return 'red'
  if (/P1|2级|严重/.test(v)) return 'orange'
  if (/P2|3级|一般/.test(v)) return 'gold'
  return 'default'
}

type SevOption = { key: string; label: string }

/**
 * 缺陷复核列表 —— 缺陷复核目录(/defects) 与 需求详情 共用，保证字段一致。
 * 字段：标题(点击跳详情) / 关联需求 / 严重程度(可改,取枚举缺陷等级) / 状态 / 创建时间 / 操作。
 * 操作：仅「待复核(draft)」时显示 确认/忽略/标记重复，三选一，点完状态变更后按钮自动消失。
 */
export default function DefectReviewTable({
  defects, loading, severityOptions, onChanged, pageSize = 10,
}: {
  defects: any[]
  loading?: boolean
  severityOptions: SevOption[]
  onChanged: () => void
  pageSize?: number
}) {
  const navigate = useNavigate()
  const [selected, setSelected] = useState<any>(null)
  const [busy, setBusy] = useState<string | null>(null)
  const [editing, setEditing] = useState(false)
  const [form, setForm] = useState<any>({})

  const startEdit = () => {
    const d = selected.draft_ticket || {}
    setForm({
      title: selected.title || '',
      severity: selected.severity || '',
      summary: d.summary || d.title || '',
      expected: d.expected || '',
      actual: d.actual || '',
      steps: (d.reproduction_steps || (d.steps ? [d.steps] : [])).join('\n'),
    })
    setEditing(true)
  }

  const saveEdit = async () => {
    setBusy(selected.id)
    try {
      const draft_ticket = {
        ...(selected.draft_ticket || {}),
        summary: form.summary,
        expected: form.expected,
        actual: form.actual,
        reproduction_steps: String(form.steps || '').split('\n').map((s: string) => s.trim()).filter(Boolean),
      }
      await defectsApi.update(selected.id, { title: form.title, severity: form.severity, draft_ticket })
      message.success('缺陷复核已保存')
      setSelected({ ...selected, title: form.title, severity: form.severity, draft_ticket })
      setEditing(false)
      onChanged()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    } finally {
      setBusy(null)
    }
  }

  const closeDrawer = () => { setSelected(null); setEditing(false) }

  const act = async (id: string, status: string) => {
    setBusy(id)
    try {
      await defectsApi.update(id, { status })
      message.success('缺陷状态已更新')
      onChanged()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '更新失败')
    } finally {
      setBusy(null)
    }
  }

  const sevOpts = severityOptions.map((o) => ({ value: o.key, label: o.label }))

  // 缺陷抽屉设计令牌
  const BRAND = '#D97757', BRAND_SOFT = '#FBEEE6', BRAND_BORDER = '#EFD6C8'
  const BRAND_GRAD = 'linear-gradient(135deg,#E8916B 0%,#D97757 100%)'
  const MONO = "'JetBrains Mono','SFMono-Regular',Consolas,monospace"
  const inpStyle = { width: '100%', border: '1.5px solid #E7ECF0', borderRadius: 10, padding: '12px 13px', fontSize: 13, color: '#334155', resize: 'none' as const, outline: 'none', lineHeight: 1.7 }
  const metaRow = (label: string, content: any) => (
    <div style={{ display: 'flex', borderBottom: '1px solid #F1F4F6' }}>
      <div style={{ width: 100, flex: 'none', padding: '11px 16px', fontSize: 12.5, color: '#94A3B8', fontWeight: 500, background: '#FAFBFC' }}>{label}</div>
      <div style={{ flex: 1, minWidth: 0, padding: '11px 16px' }}>{content}</div>
    </div>
  )

  const columns = [
    {
      title: '标题', dataIndex: 'title', key: 'title', ellipsis: true,
      render: (v: string, row: any) => (
        <a className="row-title" onClick={() => setSelected(row)}>{v}</a>
      ),
    },
    {
      title: '关联需求', dataIndex: 'requirement_title', key: 'requirement_title', width: 200, ellipsis: true,
      render: (v: string, row: any) => v
        ? <a onClick={() => navigate(`/requirements/${row.requirement_id}`)}>{v}</a>
        : <Typography.Text type="secondary">—</Typography.Text>,
    },
    {
      title: '严重程度', dataIndex: 'severity', key: 'severity', width: 120,
      render: (v: string) => <Tag color={sevColor(v)}>{severityOptions.find((o) => o.key === v)?.label || v}</Tag>,
    },
    {
      title: '状态', dataIndex: 'status', key: 'status', width: 130,
      render: (v: string, row: any) => (
        <Space size={6}>
          <Tag color={DEFECT_STATUS_COLOR[v]}>{DEFECT_STATUS_LABEL[v] || v}</Tag>
          {row.external_ticket_url && (
            <a href={row.external_ticket_url} target="_blank" rel="noreferrer" style={{ fontSize: 12 }}>单据</a>
          )}
        </Space>
      ),
    },
    {
      title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 150,
      render: (v: string) => dayjs(v).format('MM-DD HH:mm'),
    },
    {
      title: '操作', key: 'action', width: 220,
      render: (_: any, row: any) => (
        row.status === 'draft' ? (
          <Space>
            <Button type="link" size="small" danger loading={busy === row.id}
              onClick={() => act(row.id, 'confirmed')}>确认</Button>
            <Button type="link" size="small" loading={busy === row.id}
              onClick={() => act(row.id, 'ignored')}>忽略</Button>
            <Button type="link" size="small" loading={busy === row.id}
              onClick={() => act(row.id, 'duplicate')}>标记重复</Button>
          </Space>
        ) : <Typography.Text type="secondary">已处理</Typography.Text>
      ),
    },
  ]

  return (
    <>
      <Table rowKey="id" dataSource={defects} columns={columns as any} loading={loading}
             pagination={{ defaultPageSize: pageSize, showSizeChanger: true, pageSizeOptions: [10, 20, 50, 100], showTotal: (t) => `共 ${t} 条` }} />

      <Drawer
        open={!!selected}
        onClose={closeDrawer}
        width={520}
        closable={false}
        title={null}
        styles={{ body: { padding: 0, display: 'flex', flexDirection: 'column' }, content: { boxShadow: '-12px 0 40px -12px rgba(15,23,42,.22)' } }}
      >
        <style>{`
          .def-input:focus{border-color:${BRAND}!important;box-shadow:0 0 0 3px rgba(217,119,87,.12)}
          .def-act:hover{background:#F3F6F8;color:#0F172A!important}
          .def-close:hover{color:#475569!important}
        `}</style>
        {selected && (() => {
          const d = selected.draft_ticket || {}
          const steps: string[] = d.reproduction_steps || (d.steps ? [d.steps] : [])
          const bugId = selected.external_ticket_id || `#${String(selected.id || '').slice(0, 8)}`
          if (editing) {
            // ── 编辑视图 ──
            const field = (label: string, node: any) => (
              <div>
                <div style={{ fontSize: 12.5, fontWeight: 600, color: '#64748B', marginBottom: 8 }}>{label}</div>
                {node}
              </div>
            )
            return (
              <>
                <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '18px 22px 14px', borderBottom: '1px solid #ECEFF2' }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
                    <span className="ms def-close" onClick={closeDrawer} style={{ fontSize: 22, color: '#B0BAC4', cursor: 'pointer' }}>close</span>
                    <span style={{ fontSize: 14.5, fontWeight: 700, color: '#0F172A' }}>编辑缺陷复核</span>
                  </div>
                  <Space>
                    <button onClick={() => setEditing(false)} style={{ height: 32, padding: '0 14px', background: '#fff', border: '1px solid #E7ECF0', borderRadius: 9, fontSize: 12.5, color: '#64748B', cursor: 'pointer' }}>取消</button>
                    <button onClick={saveEdit} disabled={busy === selected.id} style={{ height: 32, padding: '0 16px', background: BRAND_GRAD, border: 'none', borderRadius: 9, fontSize: 12.5, fontWeight: 600, color: '#fff', cursor: 'pointer', boxShadow: '0 4px 12px -5px rgba(217,119,87,.45)' }}>保存</button>
                  </Space>
                </div>
                <div style={{ padding: '22px 24px', display: 'flex', flexDirection: 'column', gap: 20, overflowY: 'auto' }}>
                  {field('标题', <input className="def-input" style={{ ...inpStyle, height: 42 }} value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} />)}
                  {field('严重程度', (
                    <Select
                      value={form.severity || undefined}
                      style={{ width: '100%' }}
                      options={sevOpts.length ? sevOpts : (form.severity ? [{ value: form.severity, label: form.severity }] : [])}
                      onChange={(val) => setForm({ ...form, severity: val })}
                      optionRender={(o) => <Tag color={sevColor(String(o.value))}>{o.label}</Tag>}
                    />
                  ))}
                  {field('摘要', <textarea className="def-input" style={{ ...inpStyle, height: 120 }} value={form.summary} onChange={(e) => setForm({ ...form, summary: e.target.value })} />)}
                  {field('预期', <textarea className="def-input" style={{ ...inpStyle, height: 72 }} value={form.expected} onChange={(e) => setForm({ ...form, expected: e.target.value })} />)}
                  {field('实际', <textarea className="def-input" style={{ ...inpStyle, height: 72 }} value={form.actual} onChange={(e) => setForm({ ...form, actual: e.target.value })} />)}
                  {field('复现步骤（每行一步）', <textarea className="def-input" style={{ ...inpStyle, height: 96 }} value={form.steps} onChange={(e) => setForm({ ...form, steps: e.target.value })} />)}
                </div>
              </>
            )
          }
          // ── 详情视图 ──
          const txtAct = (label: string, status: string) => (
            <span className="def-act" onClick={() => act(selected.id, status).then(closeDrawer)}
              style={{ fontSize: 13, color: '#64748B', padding: '4px 6px', borderRadius: 7, cursor: 'pointer' }}>{label}</span>
          )
          return (
            <>
              <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12, padding: '18px 22px 14px', borderBottom: '1px solid #ECEFF2' }}>
                <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12, minWidth: 0 }}>
                  <span className="ms def-close" onClick={closeDrawer} style={{ fontSize: 22, color: '#B0BAC4', cursor: 'pointer', marginTop: 1 }}>close</span>
                  <div style={{ minWidth: 0 }}>
                    <div style={{ fontFamily: MONO, fontSize: 11, color: '#94A3B8' }}>{bugId}</div>
                    <div style={{ fontSize: 14.5, fontWeight: 700, color: '#0F172A', lineHeight: 1.5 }}>{selected.title}</div>
                  </div>
                </div>
                {selected.status === 'draft' && (
                  <button onClick={startEdit} style={{ flex: 'none', height: 32, padding: '0 13px', background: '#fff', border: '1px solid #E7ECF0', borderRadius: 9, fontSize: 12.5, color: '#64748B', cursor: 'pointer', display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                    <span className="ms" style={{ fontSize: 16 }}>edit</span>编辑
                  </button>
                )}
              </div>

              <div style={{ flex: 1, overflowY: 'auto', padding: '20px 22px' }}>
                {/* Meta */}
                <div style={{ border: '1px solid #ECEFF2', borderRadius: 12, overflow: 'hidden', marginBottom: 24 }}>
                  {metaRow('关联需求', selected.requirement_title
                    ? <span onClick={() => navigate(`/requirements/${selected.requirement_id}`)} style={{ fontSize: 13, fontWeight: 500, color: BRAND, cursor: 'pointer' }}>{selected.requirement_title}</span>
                    : <span style={{ color: '#CBD5E1' }}>—</span>)}
                  {metaRow('严重程度', <Tag color={sevColor(selected.severity)}>{severityOptions.find((o) => o.key === selected.severity)?.label || selected.severity}</Tag>)}
                  {metaRow('状态', <Tag color={DEFECT_STATUS_COLOR[selected.status]}>{DEFECT_STATUS_LABEL[selected.status] || selected.status}</Tag>)}
                  {metaRow('关联用例ID', <span style={{ fontFamily: MONO, fontSize: 12.5, color: '#475569' }}>{selected.test_case_id}</span>)}
                  {metaRow('关联执行ID', <span style={{ fontFamily: MONO, fontSize: 12.5, color: '#475569' }}>{selected.execution_id}</span>)}
                </div>

                {/* 草稿单 */}
                <div style={{ fontSize: 14, fontWeight: 700, color: '#0F172A', marginBottom: 14 }}>缺陷草稿单</div>
                <div style={{ border: '1px solid #ECEFF2', borderRadius: 12, overflow: 'hidden', marginBottom: 24 }}>
                  {[['摘要', d.summary || d.title], ['预期', d.expected], ['实际', d.actual]].map(([lab, val], i) => (
                    <div key={lab as string} style={{ display: 'flex', alignItems: 'flex-start', borderBottom: i < 2 ? '1px solid #F1F4F6' : 'none' }}>
                      <div style={{ width: 56, flex: 'none', padding: '14px 16px', fontSize: 12.5, color: '#94A3B8', background: '#FAFBFC' }}>{lab}</div>
                      <div style={{ flex: 1, minWidth: 0, padding: '14px 16px', fontSize: 13, lineHeight: 1.7, color: val ? '#334155' : '#CBD5E1' }}>{val || '—'}</div>
                    </div>
                  ))}
                </div>

                {/* 复现步骤 */}
                <div style={{ fontSize: 14, fontWeight: 700, color: '#0F172A', marginBottom: 14 }}>复现步骤</div>
                {steps.length ? (
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {steps.map((s, i) => (
                      <div key={i} style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
                        <span style={{ width: 22, height: 22, flex: 'none', borderRadius: '50%', background: BRAND_SOFT, color: BRAND, fontSize: 11, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{i + 1}</span>
                        <span style={{ fontSize: 13, color: '#334155', lineHeight: 1.65, paddingTop: 2 }}>{s}</span>
                      </div>
                    ))}
                  </div>
                ) : <span style={{ fontSize: 13, color: '#CBD5E1' }}>—</span>}
              </div>

              {/* Footer */}
              {selected.status === 'draft' && (
                <div style={{ display: 'flex', alignItems: 'center', gap: 4, padding: '14px 22px 18px', borderTop: '1px solid #ECEFF2' }}>
                  <button onClick={() => act(selected.id, 'confirmed').then(closeDrawer)} disabled={busy === selected.id}
                    style={{ height: 36, padding: '0 18px', background: BRAND_GRAD, border: 'none', borderRadius: 9, fontSize: 13, fontWeight: 600, color: '#fff', cursor: 'pointer', boxShadow: '0 4px 12px -5px rgba(217,119,87,.45)' }}>确认</button>
                  <div style={{ width: 1, height: 16, background: '#E7ECF0', margin: '0 8px' }} />
                  {txtAct('忽略', 'ignored')}
                  {txtAct('标记重复', 'duplicate')}
                  {txtAct('标记已建单', 'ticket_created')}
                </div>
              )}
            </>
          )
        })()}
      </Drawer>
    </>
  )
}
