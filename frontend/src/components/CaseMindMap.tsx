import { useState } from 'react'
import { Input, Button, message } from 'antd'
import { confirmDialog } from './ConfirmModal'

/**
 * 用例脑图（三层：需求 → 二级功能 → 用例）。
 * - 二级功能层来自需求分析 issue_points 的 feature（粗粒度功能块，同块问题点共用一个 feature）；用例通过 source_issue_point 归入对应功能块，未匹配的进「未归类功能」。
 * - 若无 issue_points，则退化为「按模块」聚合功能层。
 * - 用例叶子：标题行有唯一「编辑」入口(进编辑态可同时改标题/预期/步骤)与「删除」；点标题打开详情(onOpen)；
 *   标题下方直接展示预期结果与测试步骤（无需点开详情）。改标题走 onRename，改步骤/预期走 onUpdate。
 */
type Case = any
type IssuePoint = { issue_id?: string; description?: string; feature?: string }

const PRIORITY_COLOR: Record<string, { bg: string; fg: string }> = {
  P0: { bg: '#FDECEC', fg: '#DC2626' },
  P1: { bg: '#FEF3E2', fg: '#D97706' },
  P2: { bg: '#EEF2F6', fg: '#64748B' },
}

export default function CaseMindMap({
  reqTitle, cases, issuePoints, onRename, onDelete, onOpen, onUpdate,
}: {
  reqTitle: string
  cases: Case[]
  issuePoints: IssuePoint[]
  onRename: (id: string, title: string) => Promise<void>
  onDelete: (row: Case) => void
  onOpen: (row: Case) => void
  onUpdate?: (id: string, patch: { steps?: any[]; expected_result?: string }) => Promise<void>
}) {
  // 叶子默认折叠(只显标题+条数)，点开才展开步骤——避免"框太多太密"
  const [openIds, setOpenIds] = useState<Set<string>>(new Set())
  const toggleOpen = (id: string) => setOpenIds((prev) => {
    const n = new Set(prev); n.has(id) ? n.delete(id) : n.add(id); return n
  })
  // 统一编辑态：一个「编辑」入口同时改标题 + 预期 + 步骤
  const [editId, setEditId] = useState<string | null>(null)
  const [titleDraft, setTitleDraft] = useState('')
  const [stepDraft, setStepDraft] = useState<any[]>([])
  const [expDraft, setExpDraft] = useState('')
  const [saving, setSaving] = useState(false)

  // 组织二级功能分组
  // 优先用用例的 secondary_feature(从需求原文提取的页面级二级功能)；无则回退到 issue_points，再回退按模块。
  const useSecondary = cases.some((c) => c.secondary_feature)
  const useIssues = !useSecondary && issuePoints && issuePoints.length > 0
  type Group = { key: string; label: string; items: Case[] }
  const groups: Group[] = []
  const bucket: Record<string, Case[]> = {}

  if (useSecondary) {
    cases.forEach((c) => {
      const label = c.secondary_feature || '未归类功能'
      let g = groups.find((gg) => gg.label === label)
      if (!g) { g = { key: label, label, items: [] }; groups.push(g) }
      g.items.push(c)
    })
  } else if (useIssues) {
    // 二级功能按 feature(粗粒度)聚合：多个问题点共用同一 feature → 合并为一个分组。老数据无 feature 回退 description。
    const featureOf = (ip: IssuePoint) => ip.feature || ip.description || ip.issue_id || '功能点'
    const byId: Record<string, string> = {}    // issue_id → featureLabel
    const byDesc: Record<string, string> = {}  // description → featureLabel（source_issue_point 可能存描述）
    issuePoints.forEach((ip) => {
      const label = featureOf(ip)
      if (ip.issue_id) byId[ip.issue_id] = label
      if (ip.description) byDesc[ip.description] = label
      if (!groups.find((g) => g.label === label)) groups.push({ key: label, label, items: [] })
    })
    cases.forEach((c) => {
      const sip = c.source_issue_point
      const label = sip ? (byId[sip] || byDesc[sip]) : undefined
      const g = label ? groups.find((gg) => gg.label === label) : undefined
      if (g) g.items.push(c)
      else {
        const fallback = '未归类功能'  // 匹配不到别把原始 issue_id(ISSUE-x)当二级功能名
        let fg = groups.find((gg) => gg.label === fallback)
        if (!fg) {
          fg = { key: fallback, label: fallback, items: [] }
          groups.push(fg)
        }
        fg.items.push(c)
      }
    })
  } else {
    cases.forEach((c) => {
      const mods: string[] = (c.modules && c.modules.length) ? c.modules : ['未分类']
      mods.forEach((m) => { (bucket[m] ||= []).push(c) })
    })
    Object.keys(bucket).forEach((m) => groups.push({ key: m, label: m, items: bucket[m] }))
  }

  const visibleGroups = groups.filter((g) => g.items.length > 0)

  const del = async (c: Case) => {
    if (!(await confirmDialog({ title: '删除用例', desc: `确认删除「${c.title}」？删除后进入用例库回收站。`, ok: '删除', danger: true }))) return
    onDelete(c)
  }

  // ── 统一编辑：标题 + 预期 + 步骤 ──
  const startEdit = (c: Case) => {
    setEditId(c.id)
    setTitleDraft(c.title || '')
    setExpDraft(c.expected_result || '')
    setStepDraft((c.steps || []).map((s: any) => ({ ...s })))
  }
  const cancelEdit = () => { setEditId(null); setTitleDraft(''); setStepDraft([]); setExpDraft('') }
  const patchStep = (i: number, key: 'action' | 'expected', v: string) =>
    setStepDraft((prev) => prev.map((s, idx) => (idx === i ? { ...s, [key]: v } : s)))
  const addStep = () => setStepDraft((prev) => [...prev, { seq: prev.length + 1, action: '', expected: '' }])
  const removeStep = (i: number) =>
    setStepDraft((prev) => prev.filter((_, idx) => idx !== i).map((s, idx) => ({ ...s, seq: idx + 1 })))
  const commitEdit = async (c: Case) => {
    const title = titleDraft.trim()
    if (!title) { message.warning('标题不能为空'); return }
    // 保留每步原有 check_points（锚点）等字段，只改操作/预期；空步骤丢弃
    const steps = stepDraft
      .map((s, idx) => ({ ...s, seq: idx + 1, action: (s.action || '').trim(), expected: (s.expected || '').trim() }))
      .filter((s) => s.action || s.expected)
    setSaving(true)
    try {
      if (title !== c.title) { await onRename(c.id, title); c.title = title }
      if (onUpdate) {
        await onUpdate(c.id, { steps, expected_result: expDraft.trim() })
        c.steps = steps
        c.expected_result = expDraft.trim()
      }
      message.success('已保存修改')
      cancelEdit()
    } catch {
      message.error('保存失败')
    } finally {
      setSaving(false)
    }
  }

  const CONNECT = '#E2E8F0'

  return (
    <div style={{ overflowX: 'auto', padding: '8px 4px 16px' }}>
      <style>{`
        .mm-leaf:hover{border-color:#D5DDE4;box-shadow:0 6px 18px -12px rgba(16,24,40,.18)}
        .mm-op:hover{background:#EEF2F6}
        .mm-op-btn:hover{background:#EEF2F6 !important;border-color:#CBD5E1 !important;color:#334155 !important}
        .mm-del:hover{background:#FDECEC !important;color:#DC2626 !important;border-color:#F4C7C2 !important}
      `}</style>
      <div style={{ display: 'flex', alignItems: 'center', minWidth: 'min-content' }}>
        {/* 根节点：需求 */}
        <div style={{
          flex: 'none', maxWidth: 220, padding: '12px 16px', borderRadius: 12, color: '#fff',
          background: 'linear-gradient(135deg,#D97757,#C2410C)', fontWeight: 700, fontSize: 14,
          boxShadow: '0 6px 16px -8px rgba(217,119,87,.6)', lineHeight: 1.4,
        }}>
          <div style={{ fontSize: 10, opacity: .8, fontWeight: 500, letterSpacing: '.5px', marginBottom: 2 }}>需求</div>
          {reqTitle || '需求'}
        </div>

        {/* 根 → 二级功能 连接线 */}
        {visibleGroups.length > 0 && <div style={{ width: 28, height: 2, background: CONNECT, flex: 'none' }} />}

        {/* 二级功能层 */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, flex: 'none' }}>
          {visibleGroups.map((g) => (
            <div key={g.key} style={{ display: 'flex', alignItems: 'center' }}>
              <div style={{
                flex: 'none', maxWidth: 240, padding: '9px 13px', borderRadius: 10,
                background: '#FEF3EE', border: '1px solid #F0D2C0', color: '#B5600A', fontWeight: 600, fontSize: 12.5, lineHeight: 1.4,
              }}>
                <div style={{ fontSize: 9.5, opacity: .7, fontWeight: 500, letterSpacing: '.5px', marginBottom: 1 }}>二级功能</div>
                {g.label}
                <span style={{ marginLeft: 6, fontSize: 11, fontWeight: 500, color: '#C98A63' }}>{g.items.length}</span>
              </div>

              {/* 二级功能 → 用例 连接线 */}
              <div style={{ width: 24, height: 2, background: CONNECT, flex: 'none' }} />

              {/* 用例叶子层 */}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 10, flex: 'none' }}>
                {g.items.map((c) => {
                  const pri = PRIORITY_COLOR[c.priority as string] || PRIORITY_COLOR.P2
                  const steps: any[] = c.steps || []
                  return (
                    <div key={c.id} className="mm-leaf" style={{
                      width: editId === c.id ? 620 : 480, padding: editId === c.id ? '12px 14px' : '10px 12px', borderRadius: 11,
                      background: '#fff', border: editId === c.id ? '1px solid #D6E0EA' : '1px solid #ECEFF2', transition: 'all .15s',
                      boxShadow: editId === c.id ? '0 8px 24px -14px rgba(16,24,40,.22)' : 'none',
                    }}>
                      {/* 标题行 */}
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                        <span style={{
                          flex: 'none', fontSize: 10, fontWeight: 700, fontFamily: 'monospace', padding: '1px 6px',
                          borderRadius: 5, background: pri.bg, color: pri.fg,
                        }}>{c.priority || 'P2'}</span>
                        {editId === c.id ? (
                          <Input
                            size="small" autoFocus value={titleDraft}
                            onChange={(e) => setTitleDraft(e.target.value)}
                            placeholder="用例标题"
                            style={{ flex: 1 }}
                          />
                        ) : (
                          <span onClick={() => onOpen(c)} title="点击查看完整详情" style={{
                            flex: 1, minWidth: 0, fontSize: 13, fontWeight: 600, color: '#0F172A', cursor: 'pointer',
                            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                          }}>{c.title}</span>
                        )}
                        {editId !== c.id && (
                          <>
                            {(steps.length > 0 || c.expected_result) && (
                              <button className="mm-op-btn" title={openIds.has(c.id) ? '收起' : '展开步骤'} onClick={() => toggleOpen(c.id)}
                                style={{
                                  flex: 'none', display: 'inline-flex', alignItems: 'center', justifyContent: 'center', width: 24, height: 24,
                                  border: '1px solid #E2E8F0', borderRadius: 7, background: '#F8FAFC', color: '#64748B', cursor: 'pointer', transition: 'all .15s',
                                }}>
                                <span className="ms" style={{ fontSize: 16 }}>{openIds.has(c.id) ? 'expand_less' : 'expand_more'}</span>
                              </button>
                            )}
                            <button className="mm-op-btn" title="编辑用例" onClick={() => startEdit(c)}
                              style={{
                                flex: 'none', display: 'inline-flex', alignItems: 'center', gap: 3, height: 24, padding: '0 9px',
                                border: '1px solid #E2E8F0', borderRadius: 7, background: '#F8FAFC', color: '#475569',
                                fontSize: 11.5, fontWeight: 600, cursor: 'pointer', transition: 'all .15s',
                              }}>
                              <span className="ms" style={{ fontSize: 14 }}>edit</span>编辑
                            </button>
                            <button className="mm-del" title="删除用例" onClick={() => del(c)}
                              style={{
                                flex: 'none', display: 'inline-flex', alignItems: 'center', gap: 3, height: 24, padding: '0 8px',
                                border: '1px solid #F0DAD6', borderRadius: 7, background: '#FEF6F5', color: '#DC6A5C',
                                fontSize: 11.5, fontWeight: 600, cursor: 'pointer', transition: 'all .15s',
                              }}>
                              <span className="ms" style={{ fontSize: 14 }}>delete</span>删除
                            </button>
                          </>
                        )}
                      </div>

                      {/* 统一编辑态：标题(上方内联) + 预期 + 步骤 */}
                      {editId === c.id ? (
                        <div style={{ marginTop: 12, borderTop: '1px dashed #EEF2F6', paddingTop: 12, display: 'flex', flexDirection: 'column', gap: 14 }}>
                          <div>
                            <div style={{ fontSize: 11, color: '#94A3B8', fontWeight: 600, marginBottom: 5, letterSpacing: '.3px' }}>预期结果</div>
                            <Input.TextArea value={expDraft} onChange={(e) => setExpDraft(e.target.value)}
                              autoSize={{ minRows: 1, maxRows: 4 }} placeholder="用例整体预期结果" style={{ fontSize: 12.5 }} />
                          </div>
                          <div>
                            <div style={{ fontSize: 11, color: '#94A3B8', fontWeight: 600, marginBottom: 7, letterSpacing: '.3px' }}>
                              测试步骤 <span style={{ color: '#CBD5E1', fontWeight: 500 }}>· {stepDraft.length} 步</span>
                            </div>
                            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                              {stepDraft.map((s, i) => (
                                <div key={i} style={{
                                  display: 'flex', gap: 10, alignItems: 'flex-start', padding: '10px 10px 10px 8px',
                                  background: '#FAFBFC', border: '1px solid #EEF2F6', borderRadius: 9,
                                }}>
                                  <span style={{
                                    flex: 'none', width: 20, height: 20, marginTop: 2, borderRadius: '50%', background: '#E8EDF3',
                                    color: '#5B6B7C', fontSize: 11, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center',
                                  }}>{i + 1}</span>
                                  <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 7 }}>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                      <span style={{ flex: 'none', width: 30, fontSize: 11, color: '#94A3B8', fontWeight: 600 }}>操作</span>
                                      <Input size="small" placeholder="这一步做什么" value={s.action}
                                        onChange={(e) => patchStep(i, 'action', e.target.value)} style={{ fontSize: 12.5 }} />
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                                      <span style={{ flex: 'none', width: 30, fontSize: 11, color: '#94A3B8', fontWeight: 600 }}>预期</span>
                                      <Input size="small" placeholder="这一步的预期(可留空)" value={s.expected}
                                        onChange={(e) => patchStep(i, 'expected', e.target.value)} style={{ fontSize: 12.5 }} />
                                    </div>
                                  </div>
                                  <span className="ms mm-op" title="删除该步骤" onClick={() => removeStep(i)}
                                    style={{ flex: 'none', fontSize: 16, color: '#CBD5E1', cursor: 'pointer', padding: 3, borderRadius: 6 }}>close</span>
                                </div>
                              ))}
                            </div>
                            <Button size="small" onClick={addStep} disabled={saving} style={{ marginTop: 8 }}>+ 添加步骤</Button>
                          </div>
                          <div style={{ display: 'flex', gap: 8, alignItems: 'center', borderTop: '1px dashed #EEF2F6', paddingTop: 12 }}>
                            <div style={{ flex: 1 }} />
                            <Button size="small" onClick={cancelEdit} disabled={saving}>取消</Button>
                            <Button size="small" type="primary" loading={saving} onClick={() => commitEdit(c)}>保存</Button>
                          </div>
                        </div>
                      ) : !openIds.has(c.id) ? (
                        // 折叠态：一行摘要(步数 + 预期简述)，点开才展开——降低整体密度
                        (steps.length > 0 || c.expected_result) && (
                          <div onClick={() => toggleOpen(c.id)} title="展开步骤"
                            style={{
                              marginTop: 7, display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer',
                              fontSize: 11.5, color: '#94A3B8', overflow: 'hidden',
                            }}>
                            {steps.length > 0 && (
                              <span style={{ flex: 'none', padding: '1px 7px', borderRadius: 9, background: '#F1F5F9', color: '#64748B', fontWeight: 600 }}>
                                {steps.length} 步
                              </span>
                            )}
                            {c.expected_result && (
                              <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                                预期：{c.expected_result}
                              </span>
                            )}
                          </div>
                        )
                      ) : (
                        <div style={{ marginTop: 8, paddingLeft: 2, borderTop: '1px dashed #F1F4F6', paddingTop: 8 }}>
                          {c.expected_result && (
                            <div style={{ fontSize: 12, color: '#475569', lineHeight: 1.6, marginBottom: steps.length ? 6 : 0 }}>
                              <span style={{ color: '#94A3B8', fontWeight: 600 }}>预期结果：</span>{c.expected_result}
                            </div>
                          )}
                          {steps.map((s, i) => (
                            <div key={i} style={{ display: 'flex', gap: 8, alignItems: 'flex-start', margin: '4px 0' }}>
                              <span style={{
                                flex: 'none', width: 17, height: 17, marginTop: 1, borderRadius: '50%', background: '#F1F5F9',
                                color: '#64748B', fontSize: 10, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center',
                              }}>{i + 1}</span>
                              <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: 12, color: '#334155', lineHeight: 1.55 }}>{s.action}</div>
                                {s.expected && (
                                  <div style={{ fontSize: 11.5, color: '#94A3B8', lineHeight: 1.55 }}>
                                    <span style={{ color: '#16A34A' }}>预期 </span>{s.expected}
                                  </div>
                                )}
                              </div>
                            </div>
                          ))}
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
