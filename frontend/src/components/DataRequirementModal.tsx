import { useEffect, useState } from 'react'
import { Modal, Button, Input, Tag, Space, message, Tooltip, Empty, Segmented, Select } from 'antd'
import { dataRequirementsApi, dataRegistriesApi, type DataRequirement } from '../api'

/**
 * 用例「数据要求」维护（测试数据准备）。
 * 步骤里写 ${别名.字段}；每条要求可选两种准备方式：
 *  - 人工直填(MANUAL)：给别名的字段填实际值；
 *  - 自动造数(AUTO)：绑定一个已发布的数据场景，执行前置自动造数并按 outputs 映射注入。
 * 执行前置会把 ${别名.字段} 注入步骤/接口；缺值/造数失败判「数据未准备好」而不误报缺陷。
 */

interface FieldRow { field: string; value: string }
interface ReqDraft {
  id?: string
  alias: string
  strategy: 'MANUAL' | 'AUTO'
  rows: FieldRow[]              // MANUAL: 字段→实际值
  scenarioId?: string          // AUTO: 绑定场景
  scenarioVersion?: string
  outputKey?: string           // AUTO: 取场景哪个 output 映射到别名
  constraintRows: FieldRow[]   // AUTO: 造数约束(如 seed=88)
}

interface ScenarioOpt {
  scenario_id: string
  version: string
  name?: string
  data_type?: string
  status?: string
  outputs?: Record<string, any>
}

function toRows(mv: Record<string, any> | null | undefined): FieldRow[] {
  if (!mv || typeof mv !== 'object') return [{ field: '', value: '' }]
  const rows = Object.entries(mv).map(([field, value]) => ({ field, value: value == null ? '' : String(value) }))
  return rows.length ? rows : [{ field: '', value: '' }]
}
function constraintRows(c: Record<string, any> | null | undefined): FieldRow[] {
  if (!c || typeof c !== 'object') return []
  return Object.entries(c).map(([field, value]) => ({ field, value: value == null ? '' : String(value) }))
}
function rowsToObj(rows: FieldRow[]): Record<string, string> {
  const o: Record<string, string> = {}
  for (const r of rows) { const f = r.field.trim(); if (f) o[f] = r.value }
  return o
}

export default function DataRequirementModal({ open, caseId, caseTitle, onClose }: {
  open: boolean; caseId: string; caseTitle?: string; onClose: (changed?: boolean) => void
}) {
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [placeholders, setPlaceholders] = useState<string[]>([])
  const [drafts, setDrafts] = useState<ReqDraft[]>([])
  const [scenarios, setScenarios] = useState<ScenarioOpt[]>([])
  const [dirty, setDirty] = useState(false)

  useEffect(() => {
    if (!open || !caseId) return
    setLoading(true); setDirty(false)
    Promise.all([
      dataRequirementsApi.list(caseId),
      dataRegistriesApi.listScenarios().catch(() => ({ data: [] as ScenarioOpt[] })),
    ])
      .then(([r, s]) => {
        setPlaceholders(r.data.referenced_placeholders || [])
        setDrafts((r.data.requirements || []).map((x) => ({
          id: x.id,
          alias: x.alias,
          strategy: (x.strategy === 'AUTO' ? 'AUTO' : 'MANUAL'),
          rows: toRows(x.manual_values),
          scenarioId: x.scenario_id || undefined,
          scenarioVersion: x.scenario_version || undefined,
          outputKey: x.output_key || undefined,
          constraintRows: constraintRows((x as any).constraints),
        })))
        setScenarios((s.data || []).filter((x: ScenarioOpt) => (x.status || '').toUpperCase() === 'ACTIVE'))
      })
      .catch(() => message.error('加载数据要求失败'))
      .finally(() => setLoading(false))
  }, [open, caseId])

  // 已配的 别名.字段 集合，用于给引用的占位符标"已配/待配"
  const configured = new Set<string>()
  for (const d of drafts) {
    if (d.strategy === 'MANUAL') {
      for (const r of d.rows) if (r.field.trim()) configured.add(`${d.alias}.${r.field.trim()}`)
    } else if (d.scenarioId && d.outputKey) {
      // AUTO：场景 output 的字段视为已配（能覆盖到的占位符）
      const sc = scenarios.find((s) => s.scenario_id === d.scenarioId)
      const outMap = sc?.outputs?.[d.outputKey]
      if (outMap && typeof outMap === 'object') {
        for (const f of Object.keys(outMap)) configured.add(`${d.alias}.${f}`)
      } else {
        // 未知 output 结构，别名整体视为已绑定
        for (const p of placeholders) if (p.split('.')[0] === d.alias) configured.add(p)
      }
    }
  }

  // 从步骤引用的占位符自动补齐 别名/字段（空值），省得手敲
  const autofill = () => {
    const byAlias = new Map<string, Set<string>>()
    for (const p of placeholders) {
      const [alias, ...rest] = p.split('.')
      const field = rest.join('.') || 'value'
      if (!byAlias.has(alias)) byAlias.set(alias, new Set())
      byAlias.get(alias)!.add(field)
    }
    setDrafts((prev) => {
      const next = prev.map((d) => ({ ...d, rows: [...d.rows] }))
      for (const [alias, fields] of byAlias) {
        let d = next.find((x) => x.alias === alias)
        if (!d) { d = { alias, strategy: 'MANUAL', rows: [], constraintRows: [] }; next.push(d) }
        if (d.strategy !== 'MANUAL') continue
        for (const f of fields) {
          if (!d.rows.some((r) => r.field.trim() === f)) d.rows.push({ field: f, value: '' })
        }
        d.rows = d.rows.filter((r) => r.field.trim())
        if (!d.rows.length) d.rows.push({ field: '', value: '' })
      }
      return next
    })
    setDirty(true)
  }

  const patch = (fn: (d: ReqDraft[]) => ReqDraft[]) => { setDrafts(fn); setDirty(true) }
  const patchOne = (i: number, fn: (d: ReqDraft) => ReqDraft) =>
    patch((prev) => prev.map((x, j) => (j === i ? fn(x) : x)))

  const save = async () => {
    for (const d of drafts) {
      if (!d.alias.trim()) { message.warning('每条数据要求都要填别名'); return }
      if (d.strategy === 'AUTO' && !d.scenarioId) { message.warning(`别名「${d.alias}」选了自动造数，请先选场景`); return }
    }
    setSaving(true)
    try {
      for (const d of drafts) {
        if (d.strategy === 'AUTO') {
          await dataRequirementsApi.upsert({
            case_id: caseId, alias: d.alias.trim(), strategy: 'AUTO',
            scenario_id: d.scenarioId, scenario_version: d.scenarioVersion || null,
            output_key: d.outputKey || null,
            constraints: rowsToObj(d.constraintRows),
            manual_values: null,
          })
        } else {
          await dataRequirementsApi.upsert({
            case_id: caseId, alias: d.alias.trim(), strategy: 'MANUAL',
            manual_values: rowsToObj(d.rows),
            scenario_id: null, output_key: null,
          })
        }
      }
      message.success('已保存数据要求')
      onClose(true)
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败')
    } finally { setSaving(false) }
  }

  const removeReq = async (idx: number) => {
    const d = drafts[idx]
    if (d.id) { try { await dataRequirementsApi.remove(d.id) } catch { /* ignore */ } }
    patch((prev) => prev.filter((_, i) => i !== idx))
  }

  const addReq = () => patch((p) => [...p, { alias: '', strategy: 'MANUAL', rows: [{ field: '', value: '' }], constraintRows: [] }])

  const renderManual = (d: ReqDraft, i: number) => (
    <>
      {d.rows.map((r, k) => (
        <div key={k} style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
          <Input size="small" style={{ width: 200 }} placeholder="字段，如 orderId" value={r.field}
            onChange={(e) => patchOne(i, (x) => ({ ...x, rows: x.rows.map((y, m) => m === k ? { ...y, field: e.target.value } : y) }))} />
          <Input size="small" style={{ flex: 1 }} placeholder="实际值" value={r.value}
            onChange={(e) => patchOne(i, (x) => ({ ...x, rows: x.rows.map((y, m) => m === k ? { ...y, value: e.target.value } : y) }))} />
          <Tooltip title="删除该字段"><Button size="small" type="text"
            onClick={() => patchOne(i, (x) => ({ ...x, rows: x.rows.filter((_, m) => m !== k) }))}>×</Button></Tooltip>
        </div>
      ))}
      <Button size="small" type="dashed"
        onClick={() => patchOne(i, (x) => ({ ...x, rows: [...x.rows, { field: '', value: '' }] }))}>+ 字段</Button>
    </>
  )

  const renderAuto = (d: ReqDraft, i: number) => {
    const sc = scenarios.find((s) => s.scenario_id === d.scenarioId)
    const outputKeys = sc?.outputs && typeof sc.outputs === 'object' ? Object.keys(sc.outputs) : []
    const outMap = sc?.outputs?.[d.outputKey || ''] as Record<string, any> | undefined
    return (
      <div>
        <div style={{ display: 'flex', gap: 8, marginBottom: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <span style={{ fontSize: 12.5, color: '#64748B' }}>场景</span>
          <Select size="small" style={{ minWidth: 260 }} placeholder="选择已发布的数据场景" value={d.scenarioId}
            options={scenarios.map((s) => ({
              value: s.scenario_id,
              label: `${s.name || s.scenario_id}${s.data_type ? ` · ${s.data_type}` : ''} (v${s.version})`,
            }))}
            onChange={(v) => {
              const s = scenarios.find((x) => x.scenario_id === v)
              const keys = s?.outputs && typeof s.outputs === 'object' ? Object.keys(s.outputs) : []
              patchOne(i, (x) => ({ ...x, scenarioId: v, scenarioVersion: s?.version, outputKey: keys.length === 1 ? keys[0] : x.outputKey }))
            }} />
          {outputKeys.length > 0 && (
            <>
              <span style={{ fontSize: 12.5, color: '#64748B' }}>取输出</span>
              <Select size="small" style={{ minWidth: 160 }} placeholder="output" value={d.outputKey}
                options={outputKeys.map((k) => ({ value: k, label: k }))}
                onChange={(v) => patchOne(i, (x) => ({ ...x, outputKey: v }))} />
            </>
          )}
        </div>
        {outMap && typeof outMap === 'object' && (
          <div style={{ fontSize: 12, color: '#64748B', margin: '0 0 8px', lineHeight: 1.7 }}>
            该场景 <code style={{ background: '#F1F5F9', padding: '1px 5px', borderRadius: 4 }}>{d.outputKey}</code> 映射到别名字段：
            {Object.keys(outMap).map((f) => (
              <Tag key={f} color={placeholders.includes(`${d.alias}.${f}`) ? 'green' : 'default'} style={{ marginLeft: 4 }}>
                {`${d.alias}.${f}`}
              </Tag>
            ))}
          </div>
        )}
        <div style={{ fontSize: 12.5, color: '#64748B', marginBottom: 4 }}>造数约束（可选，如 seed / 金额；会喂给场景工作流）</div>
        {d.constraintRows.map((r, k) => (
          <div key={k} style={{ display: 'flex', gap: 8, marginBottom: 6 }}>
            <Input size="small" style={{ width: 200 }} placeholder="约束名，如 seed" value={r.field}
              onChange={(e) => patchOne(i, (x) => ({ ...x, constraintRows: x.constraintRows.map((y, m) => m === k ? { ...y, field: e.target.value } : y) }))} />
            <Input size="small" style={{ flex: 1 }} placeholder="值" value={r.value}
              onChange={(e) => patchOne(i, (x) => ({ ...x, constraintRows: x.constraintRows.map((y, m) => m === k ? { ...y, value: e.target.value } : y) }))} />
            <Tooltip title="删除该约束"><Button size="small" type="text"
              onClick={() => patchOne(i, (x) => ({ ...x, constraintRows: x.constraintRows.filter((_, m) => m !== k) }))}>×</Button></Tooltip>
          </div>
        ))}
        <Button size="small" type="dashed"
          onClick={() => patchOne(i, (x) => ({ ...x, constraintRows: [...x.constraintRows, { field: '', value: '' }] }))}>+ 约束</Button>
        {scenarios.length === 0 && (
          <div style={{ fontSize: 12, color: '#E8833A', marginTop: 8 }}>
            还没有已发布(ACTIVE)的数据场景。请先到「数据编排」页注册并发布场景。
          </div>
        )}
      </div>
    )
  }

  return (
    <Modal open={open} title={`数据要求 · ${caseTitle || caseId}`} width={700} onCancel={() => onClose(dirty)}
      footer={[
        <Button key="c" onClick={() => onClose(dirty)}>关闭</Button>,
        <Button key="s" type="primary" loading={saving} onClick={save}>保存</Button>,
      ]}>
      <div style={{ fontSize: 12.5, color: '#64748B', marginBottom: 10, lineHeight: 1.7 }}>
        步骤里用 <code style={{ background: '#F1F5F9', padding: '1px 5px', borderRadius: 4 }}>{'${别名.字段}'}</code> 占位。
        每条要求可选<b>人工直填</b>（自己填实际值）或<b>自动造数</b>（绑定数据场景，执行前自动造并注入）。
        缺值/造数失败会判「数据未准备好」而不误报缺陷。
      </div>

      {placeholders.length > 0 && (
        <div style={{ marginBottom: 12 }}>
          <span style={{ fontSize: 12, color: '#64748B', marginRight: 6 }}>步骤引用：</span>
          {placeholders.map((p) => (
            <Tag key={p} color={configured.has(p) ? 'green' : 'orange'} style={{ marginBottom: 4 }}>
              {'${' + p + '}'}{configured.has(p) ? ' ✓' : ' 待配'}
            </Tag>
          ))}
          <Button size="small" type="link" onClick={autofill}>从步骤自动补齐</Button>
        </div>
      )}

      {loading ? <div style={{ color: '#94A3B8', padding: 12 }}>加载中…</div> : (
        drafts.length === 0 ? (
          <Empty description="暂无数据要求" image={Empty.PRESENTED_IMAGE_SIMPLE}>
            <Button onClick={addReq}>+ 新增数据要求</Button>
            {placeholders.length > 0 && <Button type="link" onClick={autofill}>从步骤自动补齐</Button>}
          </Empty>
        ) : (
          <Space direction="vertical" style={{ width: '100%' }} size={12}>
            {drafts.map((d, i) => (
              <div key={i} style={{ border: '1px solid #E7ECF0', borderRadius: 10, padding: 12 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ fontSize: 12.5, color: '#64748B' }}>别名</span>
                  <Input size="small" style={{ width: 180 }} placeholder="如 targetOrder" value={d.alias}
                    onChange={(e) => patchOne(i, (x) => ({ ...x, alias: e.target.value }))} />
                  <Segmented size="small" value={d.strategy}
                    options={[{ label: '人工直填', value: 'MANUAL' }, { label: '自动造数', value: 'AUTO' }]}
                    onChange={(v) => patchOne(i, (x) => ({ ...x, strategy: v as 'MANUAL' | 'AUTO' }))} />
                  <div style={{ flex: 1 }} />
                  <Button size="small" danger type="text" onClick={() => removeReq(i)}>删除</Button>
                </div>
                {d.strategy === 'MANUAL' ? renderManual(d, i) : renderAuto(d, i)}
              </div>
            ))}
            <Button type="dashed" block onClick={addReq}>+ 新增数据要求</Button>
          </Space>
        )
      )}
    </Modal>
  )
}
