import { useState } from 'react'
import { Button, Input, Space, Popconfirm, message } from 'antd'
import { PlusOutlined, DeleteOutlined, EditOutlined } from '@ant-design/icons'
import type { CoveredItem } from '../types/api'
import { coveredItemsApi } from '../api'
import { COVERAGE_STATUS_LABEL } from '../constants/coveredItem'

interface Props {
  caseId: string
  items?: CoveredItem[] | null
  priority?: 'P0' | 'P1' | 'P2'
  editable?: boolean                       // 开启增删改（用例编辑态内联，写 ReviewFeedback 留痕）
  onChange?: (items: CoveredItem[]) => void
}

const PRIORITY_COLOR: Record<string, string> = { P0: '#EF4444', P1: '#E8930C', P2: '#94A3B8' }
const STATUS_STYLE: Record<string, { bg: string; fg: string }> = {
  covered: { bg: '#E9F6EE', fg: '#16A34A' },
  failed: { bg: '#FDECEC', fg: '#EF4444' },
  not_covered: { bg: '#F1F4F6', fg: '#94A3B8' },
}

/** 用例覆盖项块（精简版：对测试人员只呈现「名称 + 验证状态」，结构字段/风险标签在数据里保留但不展示）。 */
export default function CaseCoveredItemsBlock({ caseId, items, priority, editable, onChange }: Props) {
  const list = items || []
  const [adding, setAdding] = useState(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [editingItem, setEditingItem] = useState<CoveredItem | null>(null)  // 编辑时保留原结构字段，避免只改名把 object/action/expected 冲掉
  const [saving, setSaving] = useState(false)

  const p01Empty = editable && (priority === 'P0' || priority === 'P1') && list.length === 0

  const reset = () => { setName(''); setAdding(false); setEditingId(null); setEditingItem(null) }

  const submitAdd = async () => {
    if (!name.trim()) { message.warning('请填写覆盖项名称'); return }
    setSaving(true)
    try {
      const res = await coveredItemsApi.add(caseId, { name: name.trim(), source: 'tester_added' })
      onChange?.(res.data.covered_items); message.success('已新增覆盖项'); reset()
    } catch { message.error('新增失败') } finally { setSaving(false) }
  }

  const submitEdit = async (itemId: string) => {
    if (!name.trim()) { message.warning('请填写覆盖项名称'); return }
    setSaving(true)
    try {
      const o: Partial<CoveredItem> = editingItem || {}
      const res = await coveredItemsApi.update(caseId, itemId, {
        name: name.trim(),
        // 原结构字段透传保留，仅改名称
        object: o.object || undefined, action: o.action || undefined,
        expected: o.expected || undefined, scenario_type: o.scenario_type || undefined,
      })
      onChange?.(res.data.covered_items); message.success('已更新'); reset()
    } catch { message.error('更新失败') } finally { setSaving(false) }
  }

  const remove = async (itemId?: string | null) => {
    if (!itemId) return
    try {
      const res = await coveredItemsApi.remove(caseId, itemId)
      onChange?.(res.data.covered_items); message.success('已删除')
    } catch { message.error('删除失败') }
  }

  const startEdit = (ci: CoveredItem) => {
    setEditingId(ci.item_id || null); setEditingItem(ci); setAdding(false); setName(ci.name || '')
  }

  const nameEditor = (onOk: () => void) => (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, border: '1.5px solid #E7ECF0', borderRadius: 10, background: '#fff', padding: '5px 6px 5px 12px' }}>
      <Input variant="borderless" autoFocus placeholder="覆盖项名称（这条用例验证的质量点）" value={name}
        onChange={(e) => setName(e.target.value)} onPressEnter={onOk}
        style={{ fontSize: 13.5, color: '#0F172A', padding: 0, flex: 1 }} />
      <Button type="primary" size="small" style={{ fontSize: 12.5 }} loading={saving} onClick={onOk}>保存</Button>
      <Button size="small" style={{ fontSize: 12.5 }} onClick={reset}>取消</Button>
    </div>
  )

  const itemRow = (ci: CoveredItem) => {
    const st = STATUS_STYLE[ci.coverage_status || 'not_covered'] || STATUS_STYLE.not_covered
    return (
      <div key={ci.item_id} style={{ display: 'flex', alignItems: 'center', gap: 8, border: '1px solid #ECEFF2', borderRadius: 10, padding: '9px 12px', background: '#fff' }}>
        {ci.priority && <span style={{ width: 7, height: 7, flex: 'none', borderRadius: '50%', background: PRIORITY_COLOR[ci.priority] || '#94A3B8' }} />}
        <span style={{ flex: 1, minWidth: 0, fontSize: 13.5, color: '#0F172A', lineHeight: 1.5 }}>{ci.name}</span>
        <span style={{ fontSize: 11, lineHeight: '16px', padding: '1px 8px', borderRadius: 999, background: st.bg, color: st.fg, whiteSpace: 'nowrap', flex: 'none' }}>
          {COVERAGE_STATUS_LABEL[ci.coverage_status || 'not_covered']}
        </span>
        {editable && (
          <>
            <EditOutlined onClick={() => startEdit(ci)} style={{ color: '#94A3B8', cursor: 'pointer', fontSize: 13, flex: 'none' }} />
            <Popconfirm title="删除此覆盖项？" onConfirm={() => remove(ci.item_id)}>
              <DeleteOutlined style={{ color: '#CF6B5C', cursor: 'pointer', fontSize: 13, flex: 'none' }} />
            </Popconfirm>
          </>
        )}
      </div>
    )
  }

  return (
    <div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 10 }}>
        <span style={{ fontSize: 13, fontWeight: 600, color: '#0F172A' }}>
          覆盖项 <span style={{ fontSize: 12, color: '#94A3B8', fontWeight: 500 }}>{list.length}</span>
        </span>
        {editable && !adding && !editingId && (
          <Button size="small" icon={<PlusOutlined />} onClick={() => { setAdding(true); setEditingId(null); setName('') }}>新增覆盖项</Button>
        )}
      </div>
      {p01Empty && (
        <div style={{ fontSize: 12, color: '#B5600A', background: '#FEF3EE', border: '1px solid #F0D2C0', borderRadius: 8, padding: '7px 11px', marginBottom: 10 }}>
          P0/P1 用例建议至少 1 个覆盖项
        </div>
      )}
      <Space direction="vertical" style={{ width: '100%' }} size={8}>
        {list.length === 0 && !adding && <span style={{ fontSize: 12.5, color: '#B0BAC4' }}>暂无覆盖项</span>}
        {list.map((ci) => (editable && editingId === ci.item_id
          ? <div key={ci.item_id}>{nameEditor(() => submitEdit(ci.item_id!))}</div>
          : itemRow(ci)))}
        {adding && nameEditor(submitAdd)}
      </Space>
    </div>
  )
}
