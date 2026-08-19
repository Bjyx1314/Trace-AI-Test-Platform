import { useEffect, useState } from 'react'
import { Tabs, Table, Button, Modal, Input, Select, Switch, Tag, Space, message, InputNumber } from 'antd'
import { dataRegistriesApi } from '../api'

/**
 * 数据编排管理台（测试数据准备 MVP-1）：注册/维护 数据能力、数据场景、数据对象 Schema。
 * 复杂 JSON 字段（请求模板/mock 输出/输出抽取/workflow/postconditions/outputs）用 JSON 编辑框，保存时校验。
 */

const PROVIDER_TYPES = ['MOCK', 'HTTP', 'TEST_API', 'API_CASE', 'QUERY', 'WORKFLOW', 'SCRIPT', 'UI_CASE', 'POOL']
const CAP_STATUS_COLOR: Record<string, string> = { ACTIVE: 'green', DRAFT: 'default', VERIFYING: 'blue', DISABLED: 'red', DEGRADED: 'orange' }

function JsonField({ label, value, onChange, rows = 4, placeholder }: { label: string; value: string; onChange: (v: string) => void; rows?: number; placeholder?: string }) {
  let bad = false
  if (value.trim()) { try { JSON.parse(value) } catch { bad = true } }
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 12, color: '#64748B', marginBottom: 4 }}>{label}{bad && <span style={{ color: '#C9332B', marginLeft: 8 }}>JSON 格式有误</span>}</div>
      <Input.TextArea rows={rows} value={value} onChange={(e) => onChange(e.target.value)} placeholder={placeholder}
        style={{ fontFamily: 'ui-monospace, Menlo, Consolas, monospace', fontSize: 12.5, borderColor: bad ? '#F5A6A0' : undefined }} />
    </div>
  )
}
function parseJson(s: string, fallback: any = null) { if (!s || !s.trim()) return fallback; try { return JSON.parse(s) } catch { return undefined } }
function stringify(v: any) { return v == null ? '' : JSON.stringify(v, null, 2) }

// ── 能力 ──────────────────────────────────────────────────────────────────────
function Capabilities() {
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [edit, setEdit] = useState<any | null>(null)
  const load = () => { setLoading(true); dataRegistriesApi.listCapabilities().then((r) => setRows(r.data)).finally(() => setLoading(false)) }
  useEffect(load, [])

  const save = async () => {
    const pm = parseJson(edit.parameter_mapping_s, {}); const oe = parseJson(edit.output_extract_s, {}); const envs = parseJson(edit.supported_environments_s, [])
    if (pm === undefined || oe === undefined || envs === undefined) { message.error('JSON 字段格式有误'); return }
    if (!edit.capability_id?.trim() || !edit.version?.trim() || !edit.provider_type) { message.warning('能力ID/版本/类型必填'); return }
    try {
      await dataRegistriesApi.upsertCapability({
        capability_id: edit.capability_id.trim(), version: edit.version.trim(), name: edit.name, provider_type: edit.provider_type,
        business_domain: edit.business_domain, executor_ref: edit.executor_ref, parameter_mapping: pm, output_extract: oe,
        supported_environments: envs, idempotency_supported: !!edit.idempotency_supported, cleanup_mode: edit.cleanup_mode || 'TTL',
        timeout_seconds: edit.timeout_seconds || 30, max_concurrency: edit.max_concurrency || 5, retention_hours: edit.retention_hours || 24, owner: edit.owner,
      })
      message.success('已保存'); setEdit(null); load()
    } catch (e: any) { message.error(e?.response?.data?.detail || '保存失败') }
  }
  const lifecycle = async (fn: Promise<any>, ok: string) => { try { await fn; message.success(ok); load() } catch (e: any) { message.error(e?.response?.data?.detail || '操作失败') } }

  return (
    <>
      <Button type="primary" style={{ marginBottom: 12 }} onClick={() => setEdit({ provider_type: 'MOCK', parameter_mapping_s: '', output_extract_s: '', supported_environments_s: '["sit"]' })}>+ 注册能力</Button>
      <Table size="small" loading={loading} dataSource={rows} rowKey="id" pagination={false} columns={[
        { title: '能力ID', dataIndex: 'capability_id' }, { title: '版本', dataIndex: 'version', width: 80 },
        { title: '类型', dataIndex: 'provider_type', width: 100 },
        { title: '状态', dataIndex: 'status', width: 150, render: (s: string, r: any) => (<><Tag color={CAP_STATUS_COLOR[s] || 'default'}>{s}</Tag>{r.approval_status === 'APPROVED' && <Tag color="green">已认证</Tag>}</>) },
        { title: '操作', width: 240, render: (_: any, r: any) => (
          <Space size={4}>
            <Button size="small" type="link" onClick={() => setEdit({ ...r, parameter_mapping_s: stringify(r.parameter_mapping), output_extract_s: stringify(r.output_extract), supported_environments_s: stringify(r.supported_environments) })}>编辑</Button>
            {r.status !== 'ACTIVE' ? <Button size="small" type="link" onClick={() => lifecycle(dataRegistriesApi.activateCapability(r.id), '已激活')}>激活</Button>
              : <Button size="small" type="link" onClick={() => lifecycle(dataRegistriesApi.disableCapability(r.id), '已停用')}>停用</Button>}
            <Button size="small" type="link" danger onClick={() => lifecycle(dataRegistriesApi.removeCapability(r.id), '已删除')}>删除</Button>
          </Space>) },
      ]} />
      <Modal open={!!edit} title="数据能力" width={720} onCancel={() => setEdit(null)} onOk={save} okText="保存">
        {edit && (<>
          <Space wrap style={{ marginBottom: 10 }}>
            <Input style={{ width: 220 }} placeholder="能力ID 如 order.create" value={edit.capability_id} onChange={(e) => setEdit({ ...edit, capability_id: e.target.value })} />
            <Input style={{ width: 100 }} placeholder="版本" value={edit.version} onChange={(e) => setEdit({ ...edit, version: e.target.value })} />
            <Select style={{ width: 130 }} value={edit.provider_type} onChange={(v) => setEdit({ ...edit, provider_type: v })} options={PROVIDER_TYPES.map((p) => ({ value: p, label: p }))} />
            <Input style={{ width: 180 }} placeholder="名称" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
          </Space>
          <Space wrap style={{ marginBottom: 10 }}>
            <Input style={{ width: 220 }} placeholder="业务域 business_domain" value={edit.business_domain} onChange={(e) => setEdit({ ...edit, business_domain: e.target.value })} />
            <Input style={{ width: 260 }} placeholder="executor_ref 如 api-case://TC-XXX 或 service名" value={edit.executor_ref} onChange={(e) => setEdit({ ...edit, executor_ref: e.target.value })} />
            <span style={{ fontSize: 12, color: '#64748B' }}>幂等</span><Switch checked={!!edit.idempotency_supported} onChange={(v) => setEdit({ ...edit, idempotency_supported: v })} />
          </Space>
          <Space wrap style={{ marginBottom: 10 }}>
            <span style={{ fontSize: 12, color: '#64748B' }}>超时(s)</span><InputNumber value={edit.timeout_seconds || 30} onChange={(v) => setEdit({ ...edit, timeout_seconds: v })} />
            <span style={{ fontSize: 12, color: '#64748B' }}>并发</span><InputNumber value={edit.max_concurrency || 5} onChange={(v) => setEdit({ ...edit, max_concurrency: v })} />
            <Select style={{ width: 130 }} value={edit.cleanup_mode || 'TTL'} onChange={(v) => setEdit({ ...edit, cleanup_mode: v })} options={['TTL', 'DELETE', 'RELEASE', 'NONE'].map((p) => ({ value: p, label: p }))} />
            <span style={{ fontSize: 12, color: '#64748B' }}>保留(h)</span><InputNumber value={edit.retention_hours || 24} onChange={(v) => setEdit({ ...edit, retention_hours: v })} />
          </Space>
          <JsonField label="parameter_mapping（HTTP用 {service, auth:{service,appid}, request:{method,url,body,headers,params}}；MOCK用 {mock_output:{...}}）" rows={7} value={edit.parameter_mapping_s} onChange={(v) => setEdit({ ...edit, parameter_mapping_s: v })} placeholder={'{\n  "service": "order-service",\n  "auth": { "service": "order-service", "appid": "xxx" },\n  "request": { "method": "POST", "url": "/api/order/create", "body": { "amount": "{{amount}}" } }\n}'} />
          <JsonField label="output_extract（{字段: jsonpath}，HTTP 抽取用）" value={edit.output_extract_s} onChange={(v) => setEdit({ ...edit, output_extract_s: v })} placeholder='{"order_id": "$.data.orderId"}' />
          <JsonField label="supported_environments" rows={2} value={edit.supported_environments_s} onChange={(v) => setEdit({ ...edit, supported_environments_s: v })} placeholder='["sit","dev"]' />
        </>)}
      </Modal>
    </>
  )
}

// ── 场景 ──────────────────────────────────────────────────────────────────────
function Scenarios() {
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [edit, setEdit] = useState<any | null>(null)
  const load = () => { setLoading(true); dataRegistriesApi.listScenarios().then((r) => setRows(r.data)).finally(() => setLoading(false)) }
  useEffect(load, [])

  const save = async () => {
    const wf = parseJson(edit.workflow_s, []); const g = parseJson(edit.guarantees_s, {}); const pc = parseJson(edit.postconditions_s, []); const out = parseJson(edit.outputs_s, {})
    if ([wf, g, pc, out].includes(undefined)) { message.error('JSON 字段格式有误'); return }
    if (!edit.scenario_id?.trim() || !edit.version?.trim()) { message.warning('场景ID/版本必填'); return }
    try {
      await dataRegistriesApi.upsertScenario({ scenario_id: edit.scenario_id.trim(), version: edit.version.trim(), name: edit.name, data_type: edit.data_type, workflow: wf, guarantees: g, postconditions: pc, outputs: out })
      message.success('已保存'); setEdit(null); load()
    } catch (e: any) { message.error(e?.response?.data?.detail || '保存失败') }
  }
  const lifecycle = async (fn: Promise<any>, ok: string) => { try { await fn; message.success(ok); load() } catch (e: any) { message.error(e?.response?.data?.detail || '操作失败') } }

  return (
    <>
      <Button type="primary" style={{ marginBottom: 12 }} onClick={() => setEdit({ workflow_s: '[]', guarantees_s: '{}', postconditions_s: '[]', outputs_s: '{}' })}>+ 注册场景</Button>
      <Table size="small" loading={loading} dataSource={rows} rowKey="id" pagination={false} columns={[
        { title: '场景ID', dataIndex: 'scenario_id' }, { title: '版本', dataIndex: 'version', width: 80 }, { title: '主对象', dataIndex: 'data_type', width: 100 },
        { title: '状态', dataIndex: 'status', width: 90, render: (s: string) => <Tag color={s === 'ACTIVE' ? 'green' : 'default'}>{s}</Tag> },
        { title: '操作', width: 200, render: (_: any, r: any) => (
          <Space size={4}>
            <Button size="small" type="link" onClick={() => setEdit({ ...r, workflow_s: stringify(r.workflow), guarantees_s: stringify(r.guarantees), postconditions_s: stringify(r.postconditions), outputs_s: stringify(r.outputs) })}>编辑</Button>
            {r.status !== 'ACTIVE' && <Button size="small" type="link" onClick={() => lifecycle(dataRegistriesApi.publishScenario(r.id), '已发布')}>发布</Button>}
            <Button size="small" type="link" danger onClick={() => lifecycle(dataRegistriesApi.removeScenario(r.id), '已删除')}>删除</Button>
          </Space>) },
      ]} />
      <Modal open={!!edit} title="数据场景" width={760} onCancel={() => setEdit(null)} onOk={save} okText="保存">
        {edit && (<>
          <Space wrap style={{ marginBottom: 10 }}>
            <Input style={{ width: 240 }} placeholder="场景ID" value={edit.scenario_id} onChange={(e) => setEdit({ ...edit, scenario_id: e.target.value })} />
            <Input style={{ width: 100 }} placeholder="版本" value={edit.version} onChange={(e) => setEdit({ ...edit, version: e.target.value })} />
            <Input style={{ width: 160 }} placeholder="主对象 data_type" value={edit.data_type} onChange={(e) => setEdit({ ...edit, data_type: e.target.value })} />
            <Input style={{ width: 200 }} placeholder="名称" value={edit.name} onChange={(e) => setEdit({ ...edit, name: e.target.value })} />
          </Space>
          <JsonField label="workflow（步骤编排：[{use:能力ID, version, output:输出名, input:{...${上步.字段}}}]）" rows={6} value={edit.workflow_s} onChange={(v) => setEdit({ ...edit, workflow_s: v })} />
          <JsonField label="guarantees（本场景保证达成的目标状态 {output_key:{state:value}}）" value={edit.guarantees_s} onChange={(v) => setEdit({ ...edit, guarantees_s: v })} />
          <JsonField label="postconditions（独立校验：[{validator:能力ID, version, input, expected:{...}}]）" value={edit.postconditions_s} onChange={(v) => setEdit({ ...edit, postconditions_s: v })} />
          <JsonField label="outputs（按 output_key 导出 {order:{orderId:'${order.order_id}'}}）" value={edit.outputs_s} onChange={(v) => setEdit({ ...edit, outputs_s: v })} />
        </>)}
      </Modal>
    </>
  )
}

// ── Schema ────────────────────────────────────────────────────────────────────
function Schemas() {
  const [rows, setRows] = useState<any[]>([])
  const [loading, setLoading] = useState(false)
  const [edit, setEdit] = useState<any | null>(null)
  const load = () => { setLoading(true); dataRegistriesApi.listSchemas().then((r) => setRows(r.data)).finally(() => setLoading(false)) }
  useEffect(load, [])
  const save = async () => {
    const sj = parseJson(edit.schema_json_s, {})
    if (sj === undefined) { message.error('schema_json 格式有误'); return }
    if (!edit.data_type?.trim() || !edit.schema_version?.trim()) { message.warning('data_type/版本必填'); return }
    try { await dataRegistriesApi.upsertSchema({ data_type: edit.data_type.trim(), schema_version: edit.schema_version.trim(), schema_json: sj, owner: edit.owner, description: edit.description }); message.success('已保存'); setEdit(null); load() }
    catch (e: any) { message.error(e?.response?.data?.detail || '保存失败') }
  }
  return (
    <>
      <Button type="primary" style={{ marginBottom: 12 }} onClick={() => setEdit({ schema_json_s: '{\n  "states": {},\n  "constraints": {}\n}' })}>+ 注册 Schema</Button>
      <Table size="small" loading={loading} dataSource={rows} rowKey="id" pagination={false} columns={[
        { title: '数据类型', dataIndex: 'data_type' }, { title: '版本', dataIndex: 'schema_version', width: 90 },
        { title: '状态', dataIndex: 'status', width: 100, render: (s: string) => <Tag color={s === 'ACTIVE' ? 'green' : 'default'}>{s}</Tag> },
        { title: '操作', width: 100, render: (_: any, r: any) => <Button size="small" type="link" onClick={() => setEdit({ ...r, schema_json_s: stringify(r.schema_json) })}>编辑</Button> },
      ]} />
      <Modal open={!!edit} title="数据对象 Schema" width={640} onCancel={() => setEdit(null)} onOk={save} okText="保存">
        {edit && (<>
          <Space style={{ marginBottom: 10 }}>
            <Input style={{ width: 200 }} placeholder="data_type 如 order" value={edit.data_type} onChange={(e) => setEdit({ ...edit, data_type: e.target.value })} />
            <Input style={{ width: 120 }} placeholder="版本 如 1.0" value={edit.schema_version} onChange={(e) => setEdit({ ...edit, schema_version: e.target.value })} />
          </Space>
          <JsonField label="schema_json（states/constraints/sensitive 字段定义）" rows={8} value={edit.schema_json_s} onChange={(v) => setEdit({ ...edit, schema_json_s: v })} />
        </>)}
      </Modal>
    </>
  )
}

export default function DataOrchestration() {
  return (
    <div style={{ padding: 24 }}>
      <div style={{ fontSize: 12.5, color: '#64748B', marginBottom: 12, lineHeight: 1.7 }}>
        注册【已认证的造数能力】与【把能力编排成造出目标数据的场景】。用例的数据要求标 AUTO + 绑定场景后，
        执行前会自动造数并注入。只有【已激活(ACTIVE)】的能力和【已发布】的场景才会被编排引用。
      </div>
      <Tabs items={[
        { key: 'cap', label: '数据能力', children: <Capabilities /> },
        { key: 'scn', label: '数据场景', children: <Scenarios /> },
        { key: 'schema', label: '对象 Schema', children: <Schemas /> },
      ]} />
    </div>
  )
}
