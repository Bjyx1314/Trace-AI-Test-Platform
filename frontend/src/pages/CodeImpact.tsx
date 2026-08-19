import { useEffect, useRef, useState } from 'react'
import {
  Card, Form, Input, Select, Button, Space, Tag, Table, message, Radio, Alert,
  Descriptions, Divider, Typography, List, Empty, Modal, Popconfirm,
} from 'antd'
import { useProjectStore } from '../store/projectStore'
import { requirementsApi, codeImpactApi, pipelineApi, businessRepoApi } from '../api'
import type { Requirement, ChangeImpactRecord, BusinessRepo } from '../types/api'
import ExperiencePanel from '../components/ExperiencePanel'

const RISK_COLOR: Record<string, string> = { P0: 'red', P1: 'orange', P2: 'default' }

// embedded=true 时收拢进「需求详情」的代码分析 tab：requirementId 固定为当前需求，
// 隐藏关联需求下拉；「生成测试用例」写入本需求用例后回调 onCasesGenerated 通知父页刷新用例 tab。
export default function CodeImpact({ requirementId, embedded, onCasesGenerated }: {
  requirementId?: string; embedded?: boolean; onCasesGenerated?: () => void
} = {}) {
  const { currentProject } = useProjectStore()
  const [form] = Form.useForm()
  // 人工拉取代码分析（req 1）默认走「指定仓库分支」；本地路径为备选
  const [mode, setMode] = useState<'repo_branch' | 'local_path'>('repo_branch')
  const [reqs, setReqs] = useState<Requirement[]>([])
  const [repos, setRepos] = useState<BusinessRepo[]>([])
  const [triggering, setTriggering] = useState(false)
  const [genLoading, setGenLoading] = useState(false)
  const [current, setCurrent] = useState<ChangeImpactRecord | null>(null)
  const [history, setHistory] = useState<ChangeImpactRecord[]>([])
  const [repoModal, setRepoModal] = useState(false)
  const [repoForm] = Form.useForm()
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  const loadRepos = () =>
    businessRepoApi.list(currentProject?.id).then((r) => setRepos(r.data)).catch(() => setRepos([]))

  useEffect(() => {
    if (!embedded) {
      requirementsApi.list(currentProject ? { project_id: currentProject.id } : undefined).then((r) => setReqs(r.data))
    }
    loadRepos()
    loadHistory()
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [currentProject, embedded, requirementId])

  const onCreateRepo = async () => {
    const v = await repoForm.validateFields()
    try {
      await businessRepoApi.create({ ...v, project_id: currentProject?.id })
      message.success('仓库已登记')
      setRepoModal(false)
      repoForm.resetFields()
      loadRepos()
    } catch { message.error('登记失败') }
  }

  const loadHistory = () => codeImpactApi.listByRequirement(embedded ? requirementId : undefined, 30).then((r) => setHistory(r.data))

  const startPoll = (impactId: string) => {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      const r = await codeImpactApi.get(impactId)
      setCurrent(r.data)
      if (['done', 'degraded', 'failed'].includes(r.data.status)) {
        if (pollRef.current) clearInterval(pollRef.current)
        loadHistory()
      }
    }, 4000)
  }

  const onTrigger = async () => {
    const v = await form.validateFields()
    setTriggering(true)
    try {
      // 内嵌模式固定关联当前需求
      const payload = embedded ? { trigger_mode: mode, ...v, requirement_id: requirementId } : { trigger_mode: mode, ...v }
      const r = await codeImpactApi.trigger(payload)
      setCurrent(r.data)
      message.success('已提交分析，结果生成中…')
      startPoll(r.data.impact_id)
      loadHistory()
    } catch {
      message.error('触发失败')
    } finally {
      setTriggering(false)
    }
  }

  // 基于影响面生成测试用例，写入本需求测试用例；内嵌时生成后回调父页刷新用例 tab
  const genGapCases = async (reqId?: string | null) => {
    const target = reqId || (embedded ? requirementId : undefined)
    if (!target) { message.warning('该分析未关联需求，无法生成测试用例'); return }
    setGenLoading(true)
    try {
      await pipelineApi.generateCases(target)
      message.success(embedded ? '已触发测试用例生成，稍后在「测试用例」查看' : '已触发缺口用例生成，请到需求详情查看')
      onCasesGenerated?.()
    } catch { message.error('生成失败') } finally { setGenLoading(false) }
  }

  const scope = current?.impact_scope
  const status = current?.status
  const analyzing = status === 'pending' || status === 'running'

  return (
    <div style={{ padding: embedded ? 0 : 20 }}>
      <Card size="small" title="发起代码影响分析" style={{ marginBottom: 16 }}>
        <Radio.Group value={mode} onChange={(e) => setMode(e.target.value)} style={{ marginBottom: 12 }}>
          <Radio.Button value="repo_branch">拉取仓库分析</Radio.Button>
          <Radio.Button value="local_path">本地仓库路径</Radio.Button>
        </Radio.Group>
        <Form form={form} layout="vertical">
          {!embedded && (
            <Form.Item name="requirement_id" label="关联需求（可选，关联后可自动增量生成缺口用例）">
              <Select allowClear showSearch optionFilterProp="label" placeholder="选择需求"
                options={reqs.map((r) => ({ label: r.title, value: r.id }))} style={{ maxWidth: 480 }} />
            </Form.Item>
          )}

          {mode === 'local_path' && (
            <Space wrap>
              <Form.Item name="repo_path" label="本地仓库路径" rules={[{ required: true }]}>
                <Input placeholder="/path/to/repo" style={{ width: 320 }} />
              </Form.Item>
              <Form.Item name="base_branch" label="base 分支"><Input placeholder="master" style={{ width: 160 }} /></Form.Item>
              <Form.Item name="target_branch" label="target 分支"><Input placeholder="feature/x" style={{ width: 160 }} /></Form.Item>
            </Space>
          )}
          {mode === 'repo_branch' && (
            <Space wrap align="end">
              <Form.Item name="business_repo_id" label="仓库名" rules={[{ required: true, message: '请选择仓库' }]}>
                <Select
                  showSearch optionFilterProp="label" placeholder="选择业务仓库" style={{ width: 240 }}
                  options={repos.map((r) => ({ label: r.name, value: r.id }))}
                  notFoundContent={<span style={{ color: '#999' }}>暂无登记仓库，点右侧「登记仓库」</span>}
                />
              </Form.Item>
              <Form.Item name="target_branch" label="目标分支" rules={[{ required: true, message: '请输入目标分支' }]}>
                <Input placeholder="feature/x" style={{ width: 180 }} />
              </Form.Item>
              <Form.Item name="commit_id" label="commit id（可选）">
                <Input placeholder="目标 commit，留空取分支最新" style={{ width: 220 }} />
              </Form.Item>
              <Form.Item label="基准分支">
                <Input value="master" disabled style={{ width: 120 }} />
              </Form.Item>
              <Form.Item label=" ">
                <Space>
                  <Button type="primary" loading={triggering} onClick={onTrigger}>开始分析</Button>
                  <Button onClick={() => setRepoModal(true)}>登记仓库</Button>
                </Space>
              </Form.Item>
            </Space>
          )}
          {mode !== 'repo_branch' && (
            <Button type="primary" loading={triggering} onClick={onTrigger}>开始分析</Button>
          )}
        </Form>
      </Card>

      {current && (
        <Card size="small" title={<span>分析结果 <Tag color={status === 'done' ? 'success' : status === 'degraded' ? 'warning' : status === 'failed' ? 'error' : 'processing'}>{status}</Tag></span>}
          style={{ marginBottom: 16 }}
          extra={<a href={codeImpactApi.reportUrl(current.impact_id)} target="_blank" rel="noreferrer">下载 impact.md</a>}>
          {analyzing && <Alert type="info" showIcon message="分析进行中，结果生成后自动刷新…" style={{ marginBottom: 12 }} />}
          {status === 'failed' && <Alert type="warning" showIcon message={`影响分析缺失：${current.error_message || '分析失败'}（不阻断流程）`} style={{ marginBottom: 12 }} />}

          {current.change_summary && (
            <Descriptions size="small" column={3} style={{ marginBottom: 12 }}>
              <Descriptions.Item label="总体风险"><Tag color={RISK_COLOR[current.change_summary.overall_risk || ''] || 'default'}>{current.change_summary.overall_risk || '—'}</Tag></Descriptions.Item>
              <Descriptions.Item label="变更文件数">{current.change_summary.changed_files_count ?? '—'}</Descriptions.Item>
              <Descriptions.Item label="功能簇">{(current.change_summary.feature_clusters || []).join('、') || '—'}</Descriptions.Item>
            </Descriptions>
          )}

          {scope && (
            <>
              <Divider orientation="left" plain style={{ margin: '8px 0' }}>影响面</Divider>
              {([['affected_pages', '页面'], ['affected_components', '组件'], ['affected_apis', '接口'], ['affected_services', '服务'], ['affected_flows', '流程']] as const).map(([k, label]) =>
                (scope[k]?.length ?? 0) > 0 ? (
                  <div key={k} style={{ marginBottom: 4 }}><b style={{ color: '#888' }}>{label}：</b>{scope[k]!.map((x) => <Tag key={x}>{x}</Tag>)}</div>
                ) : null,
              )}
            </>
          )}

          {(() => {
            const gi = (current as any).guardian_impact
            if (gi === undefined) return null  // 老记录未跑该字段
            if (!gi || !(gi.items?.length)) return (
              <Alert type="info" showIcon style={{ margin: '8px 0' }}
                message="存量依赖面：Guardian 合入态图谱未接入/不可用 —— 本次「谁依赖改动代码」为 AI 基于 diff 的推断，跨仓调用方可能看不全" />
            )
            return (
              <>
                <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>
                  存量依赖面 <Tag color="geekblue">Guardian 图谱·确定性</Tag>
                </Divider>
                {gi.items.map((it: any) => (
                  <div key={it.path} style={{ marginBottom: 6 }}>
                    <b style={{ color: '#555', fontFamily: 'monospace', fontSize: 12.5 }}>{it.path}</b>
                    {it.hotness != null && <Tag color="volcano" style={{ marginLeft: 6 }}>热度 {it.hotness}</Tag>}
                    <span style={{ color: '#94A3B8', fontSize: 11.5, marginLeft: 6 }}>依赖者 {it.dependents?.length ?? 0} 个</span>
                    <div style={{ marginTop: 2 }}>
                      {(it.dependents?.length ?? 0) === 0
                        ? <span style={{ color: '#999' }}>无存量依赖者 / 叶子文件 / 未索引（.vue 消费暂未入图）</span>
                        : it.dependents.slice(0, 20).map((d: any, i: number) => (
                          <Tag key={i} color="blue" style={{ marginBottom: 4, fontFamily: 'monospace', fontSize: 11.5 }}>{d.file}</Tag>
                        ))}
                      {(it.dependents?.length ?? 0) > 20 && <span style={{ color: '#999' }}>等 {it.dependents.length} 个</span>}
                    </div>
                    {(it.gaps?.length ?? 0) > 0 && <div style={{ color: '#d46b08', fontSize: 12 }}>影响未知: {it.gaps.slice(0, 3).join('；')}</div>}
                  </div>
                ))}
              </>
            )
          })()}

          {(current.suggested_validation_items?.length ?? 0) > 0 && (
            <>
              <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>
                建议覆盖项 <Button size="small" type="primary" loading={genLoading} onClick={() => genGapCases(current.requirement_id)}>生成测试用例</Button>
              </Divider>
              <Table
                size="small" rowKey={(_, i) => String(i)} pagination={false}
                dataSource={current.suggested_validation_items}
                columns={[
                  { title: '覆盖项', dataIndex: 'item', key: 'item' },
                  { title: '优先级', dataIndex: 'priority', key: 'priority', width: 90, render: (p: string) => <Tag color={RISK_COLOR[p] || 'default'}>{p}</Tag> },
                  { title: '原因', dataIndex: 'reason', key: 'reason' },
                  { title: '风险标签', dataIndex: 'risk_tags', key: 'risk_tags', render: (t?: string[]) => (t || []).map((x) => <Tag key={x}>{x}</Tag>) },
                ] as any}
              />
            </>
          )}

          {current.graph_expansion && (current.graph_expansion.nodes?.length ?? 0) > 0 && (
            <>
              <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>图谱扩散影响面（证据链）</Divider>
              <Space direction="vertical" style={{ width: '100%' }} size={2}>
                {current.graph_expansion.nodes!.slice(0, 20).map((n, i) => (
                  <div key={i} style={{ fontSize: 12 }}>
                    <Tag color={n.node_type === 'page' ? 'blue' : n.node_type === 'api' ? 'purple' : n.node_type === 'service' ? 'geekblue' : 'default'}>{n.node_type}</Tag>
                    {n.name} <span style={{ color: '#aaa' }}>置信{n.path_conf}</span>
                    {(n.evidence_chain?.length ?? 0) > 0 && <span style={{ color: '#bbb' }}> · {n.evidence_chain![n.evidence_chain!.length - 1]}</span>}
                  </div>
                ))}
                {(current.graph_expansion.truncated?.length ?? 0) > 0 && (
                  <div style={{ fontSize: 12, color: '#faad14' }}>⚠ {current.graph_expansion.truncated!.length} 个分支超出跳数未追踪（待确认）</div>
                )}
              </Space>
            </>
          )}

          {current.reuse && (
            <>
              <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>用例复用判断</Divider>
              <Space direction="vertical" style={{ width: '100%' }} size={4}>
                {(current.reuse.reusable || []).length > 0 && (
                  <div><Tag color="success">可复用 {current.reuse.reusable!.length}</Tag>
                    {current.reuse.reusable!.map((r, i) => <div key={i} style={{ fontSize: 12, marginLeft: 8 }}>· {r.case_id} {r.title?.slice(0, 24)}（{r.item}，相似{r.sim}）</div>)}</div>
                )}
                {(current.reuse.need_adjust || []).length > 0 && (
                  <div><Tag color="warning">需调整 {current.reuse.need_adjust!.length}</Tag>
                    {current.reuse.need_adjust!.map((r, i) => <div key={i} style={{ fontSize: 12, marginLeft: 8, color: '#d46b08' }}>· {r.case_id} {r.title?.slice(0, 24)} — {r.reason}</div>)}</div>
                )}
                {(current.reuse.need_new || []).length > 0 && (
                  <div><Tag color="processing">需新增 {current.reuse.need_new!.length}</Tag>
                    {current.reuse.need_new!.map((r, i) => <div key={i} style={{ fontSize: 12, marginLeft: 8 }}>· {r.item}</div>)}</div>
                )}
              </Space>
            </>
          )}

          {(current.risk_assessment?.length ?? 0) > 0 && (
            <>
              <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>风险评估</Divider>
              {current.risk_assessment!.map((r, i) => (
                <div key={i} style={{ marginBottom: 4 }}>
                  <Tag color={RISK_COLOR[r.risk_level || ''] || 'default'}>{r.risk_level}</Tag>
                  <b>{r.area}</b>：{r.reason}
                </div>
              ))}
            </>
          )}

          {(current.pending_questions?.length ?? 0) > 0 && (
            <>
              <Divider orientation="left" plain style={{ margin: '12px 0 8px' }}>待确认问题</Divider>
              <List size="small" dataSource={current.pending_questions} renderItem={(q) => <List.Item>{q}</List.Item>} />
            </>
          )}
        </Card>
      )}

      {current && (current.requirement_id || current.impact_id) && (
        <div style={{ marginBottom: 16 }}>
          <ExperiencePanel requirementId={current.requirement_id || undefined} impactId={current.impact_id} title="历史经验召回（本次影响面）" />
        </div>
      )}

      <Card size="small" title="历史分析记录">
        {history.length === 0 ? <Empty description="暂无记录" /> : (
          <Table
            size="small" rowKey="impact_id" pagination={{ pageSize: 10 }}
            dataSource={history}
            onRow={(r) => ({ onClick: () => { setCurrent(r); if (['pending', 'running'].includes(r.status)) startPoll(r.impact_id) }, style: { cursor: 'pointer' } })}
            columns={[
              { title: '触发方式', dataIndex: 'trigger_mode', key: 'trigger_mode', width: 120 },
              { title: '仓库/标签', dataIndex: 'repo_label', key: 'repo_label', render: (v: string) => v || '—' },
              { title: '状态', dataIndex: 'status', key: 'status', width: 100, render: (st: string) => <Tag color={st === 'done' ? 'success' : st === 'degraded' ? 'warning' : st === 'failed' ? 'error' : 'processing'}>{st}</Tag> },
              { title: '时间', dataIndex: 'created_at', key: 'created_at', width: 170, render: (v: string) => v?.replace('T', ' ').slice(0, 19) },
            ] as any}
          />
        )}
      </Card>

      <Modal title="登记业务仓库" open={repoModal} onOk={onCreateRepo} onCancel={() => setRepoModal(false)} okText="保存">
        <Form form={repoForm} layout="vertical">
          <Form.Item name="name" label="仓库名" rules={[{ required: true }]}>
            <Input placeholder="如 order-service" />
          </Form.Item>
          <Form.Item name="git_url" label="Git 地址" rules={[{ required: true }]}>
            <Input placeholder="http(s)://... 或 git@..." />
          </Form.Item>
          <Space wrap>
            <Form.Item name="default_branch" label="默认分支" initialValue="master">
              <Input placeholder="master" style={{ width: 160 }} />
            </Form.Item>
            <Form.Item name="token" label="只读 token（私有仓需要）">
              <Input.Password placeholder="deploy token，可留空" style={{ width: 260 }} />
            </Form.Item>
          </Space>
          {repos.length > 0 && (
            <>
              <Divider plain style={{ margin: '8px 0' }}>已登记</Divider>
              <List size="small" dataSource={repos} renderItem={(r) => (
                <List.Item actions={[
                  <Popconfirm key="d" title="删除该仓库登记？" onConfirm={async () => { await businessRepoApi.remove(r.id); loadRepos() }}>
                    <a style={{ color: '#cf1322' }}>删除</a>
                  </Popconfirm>,
                ]}>
                  <span>{r.name} <span style={{ color: '#999', fontSize: 12 }}>{r.git_url}</span></span>
                </List.Item>
              )} />
            </>
          )}
        </Form>
      </Modal>
    </div>
  )
}
