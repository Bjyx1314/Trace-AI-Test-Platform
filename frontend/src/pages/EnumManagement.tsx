import { useEffect, useState } from 'react'
import { Modal, Form, Input, Select, message } from 'antd'
import { enumsApi } from '../api'
import type { UrlMatrix } from '../api'
import { confirmDialog } from '../components/ConfirmModal'
import dayjs from 'dayjs'

// base_url(pc端地址) 与 app_package(应用包名) 不单列：合并进「端」——见端配置表。
const CATEGORIES = ['priority', 'category', 'severity', 'product_line', 'module', 'platform']

const CATEGORY_META: Record<string, { label: string; icon: string; desc: string }> = {
  priority: { label: '用例优先级', icon: 'flag', desc: '用例执行的优先级（P0/P1…）' },
  category: { label: '场景类型', icon: 'category', desc: '用例的场景/功能分类' },
  severity: { label: '缺陷等级', icon: 'bug_report', desc: '缺陷的严重程度分级' },
  product_line: { label: '研发领域', icon: 'hub', desc: '产品线 / 研发领域划分' },
  module: { label: '功能模块', icon: 'widgets', desc: '功能模块划分' },
  platform: { label: '端', icon: 'devices', desc: '端 · 执行口径 · 地址 · 应用包名' },
}
const CATEGORY_LABEL: Record<string, string> = Object.fromEntries(
  Object.entries(CATEGORY_META).map(([k, v]) => [k, v.label]),
)
CATEGORY_LABEL.base_url = 'pc端地址'

const PLATFORM_GROUP_OPTIONS = [
  { value: 'pc', label: 'PC / Web' },
  { value: 'app', label: '移动端' },
  { value: 'miniprogram', label: '小程序' },
  { value: 'api', label: '接口' },
]
const PLATFORM_GROUP_LABEL: Record<string, string> = Object.fromEntries(
  PLATFORM_GROUP_OPTIONS.map((o) => [o.value, o.label]),
)
// 执行口径标签配色（spec §8）
const TAG_STYLE: Record<string, { color: string; border: string; bg: string }> = {
  pc: { color: '#3a6ea5', border: '#d5e2f0', bg: '#eef4fb' },
  app: { color: '#7a5bb0', border: '#e2daf0', bg: '#f4f0fb' },
  miniprogram: { color: '#2f9e6f', border: '#cbe8dc', bg: '#eaf6f0' },
  api: { color: '#b05a3c', border: '#f0dccf', bg: '#fbf0ea' },
}

// 等级款徽标内容：取标签字母数字前 2 位（P0、1…）
const rankBadge = (label: string) => {
  const m = (label || '').match(/[A-Za-z0-9]+/)
  return (m ? m[0] : label).slice(0, 2).toUpperCase()
}

type EnumItem = {
  id: string; category: string; key: string; label: string
  parent_key?: string | null; sort_order: number; is_active: boolean
}

export default function EnumManagement() {
  const [data, setData] = useState<EnumItem[]>([])
  const [matrix, setMatrix] = useState<UrlMatrix | null>(null)
  const [pkgMap, setPkgMap] = useState<Record<string, { id: string; label: string }>>({})
  const [filterCat, setFilterCat] = useState<string | undefined>()
  const [search, setSearch] = useState('')
  const [activeNav, setActiveNav] = useState<string>('priority')

  const loadAll = () => {
    enumsApi.list(undefined).then((r) => setData(r.data as any))
    enumsApi.urlMatrix().then((r) => setMatrix(r.data)).catch(() => setMatrix(null))
    enumsApi.list('app_package').then((r) => {
      const m: Record<string, { id: string; label: string }> = {}
      r.data.forEach((e: any) => { if (e.label) m[e.key] = { id: e.id, label: e.label } })
      setPkgMap(m)
    }).catch(() => setPkgMap({}))
  }
  useEffect(loadAll, [])

  // ── 编辑气泡（重命名 + 执行口径 + 启用/停用 + 删除）─────────────────────
  const [pop, setPop] = useState<
    { item: EnumItem; rect: { top: number; left: number }; label: string; parent: string | null; active: boolean } | null
  >(null)
  const openPop = (item: EnumItem, e: React.MouseEvent) => {
    const r = (e.currentTarget as HTMLElement).getBoundingClientRect()
    setPop({
      item,
      rect: { top: r.bottom + 6, left: Math.min(r.left, window.innerWidth - 284) },
      label: item.label, parent: item.parent_key ?? null, active: item.is_active !== false,
    })
  }
  const savePop = async () => {
    if (!pop) return
    const p = pop
    const label = p.label.trim()
    if (!label) { message.warning('名称不能为空'); return }
    await enumsApi.update(p.item.id, {
      category: p.item.category, key: label, label,
      parent_key: p.item.category === 'platform' ? (p.parent || null) : (p.item.parent_key ?? null),
      sort_order: p.item.sort_order ?? 0, is_active: p.active,
    })
    message.success('已保存')
    setPop(null); loadAll()
  }
  const deletePop = async () => {
    if (!pop) return
    if (!(await confirmDialog({ title: '删除枚举值', desc: `确认删除「${pop.item.label}」？`, ok: '删除', danger: true }))) return
    await enumsApi.delete(pop.item.id)
    message.success('已删除'); setPop(null); loadAll()
  }

  // ── 新增枚举值弹窗 ─────────────────────────────────────────────────────
  const [createOpen, setCreateOpen] = useState(false)
  const [form] = Form.useForm()
  const openCreate = (category?: string) => {
    form.resetFields()
    if (category) form.setFieldValue('category', category)
    setCreateOpen(true)
  }
  const submitCreate = async (values: any) => {
    const label = (values.label || '').trim()
    await enumsApi.create({
      category: values.category, key: label, label,
      parent_key: values.category === 'platform' ? (values.parent_key || null) : null,
    })
    message.success('已创建'); setCreateOpen(false); loadAll()
  }

  // ── 地址 / 应用包名 弹窗 ────────────────────────────────────────────────
  // pc 端地址：一个弹框同时编辑该端所有环境(SIT/dev…)地址；保存时只要求至少填一个（都可留空清除）
  const [addrOpen, setAddrOpen] = useState(false)
  const [addrEnd, setAddrEnd] = useState<string | null>(null)  // 正在编辑地址的端名
  const [addrForm] = Form.useForm()
  const cellOf = (endKey: string | null, env: string) =>
    matrix?.platforms.find((p) => p.key === endKey)?.urls?.[env]
  const openAddr = (platform_key: string) => {
    setAddrEnd(platform_key)
    const vals: Record<string, string> = {}
    ;(matrix?.envs || []).forEach((e) => { vals[e.key] = cellOf(platform_key, e.key)?.url || '' })
    addrForm.setFieldsValue(vals)
    setAddrOpen(true)
  }
  const submitAddr = async (values: any) => {
    const envs = matrix?.envs || []
    if (!envs.some((e) => (values[e.key] || '').trim())) { message.warning('请至少填写一个环境的地址'); return }
    for (const e of envs) {
      const v = (values[e.key] || '').trim()
      if (v && !/^https?:\/\//i.test(v)) { message.warning(`${e.label} 地址需以 http:// 或 https:// 开头`); return }
    }
    try {
      for (const e of envs) {
        const v = (values[e.key] || '').trim()
        const cell = cellOf(addrEnd, e.key)
        if (v) {
          if (cell) { if (cell.url !== v) await enumsApi.update(cell.id, { category: e.category, key: addrEnd!, label: v } as any) }
          else await enumsApi.create({ category: e.category, key: addrEnd!, label: v } as any)
        } else if (cell) {
          await enumsApi.delete(cell.id)  // 清空=删除该环境地址
        }
      }
      message.success('已保存'); setAddrOpen(false); loadAll()
    } catch (e: any) {
      message.error(e?.response?.data?.detail || '保存失败，请重试')
    }
  }

  const [pkgOpen, setPkgOpen] = useState(false)
  const [pkgEditing, setPkgEditing] = useState<{ id: string } | null>(null)
  const [pkgForm] = Form.useForm()
  const openPkg = (init: { platform_key: string; id?: string; pkg?: string }) => {
    setPkgEditing(init.id ? { id: init.id } : null)
    pkgForm.setFieldsValue({ platform_key: init.platform_key, pkg: init.pkg || '' })
    setPkgOpen(true)
  }
  const submitPkg = async (values: any) => {
    const payload = { category: 'app_package', key: values.platform_key, label: (values.pkg || '').trim() }
    if (pkgEditing) await enumsApi.update(pkgEditing.id, payload as any)
    else await enumsApi.create(payload as any)
    message.success(pkgEditing ? '已更新' : '已保存'); setPkgOpen(false); loadAll()
  }
  const deletePkg = async () => {
    if (!pkgEditing) return
    await enumsApi.delete(pkgEditing.id); message.success('已删除'); setPkgOpen(false); loadAll()
  }

  // ── 操作记录抽屉 ───────────────────────────────────────────────────────
  const [logCat, setLogCat] = useState<string | null>(null)
  const [logs, setLogs] = useState<any[]>([])
  const [logsLoading, setLogsLoading] = useState(false)
  const openLogs = (category: string) => {
    setLogCat(category); setLogs([]); setLogsLoading(true)
    enumsApi.logs(category).then((r) => setLogs(r.data)).finally(() => setLogsLoading(false))
  }

  // ── 派生 ───────────────────────────────────────────────────────────────
  const kw = search.trim().toLowerCase()
  const cats = CATEGORIES.filter((c) => !filterCat || c === filterCat)
  const itemsOf = (cat: string) => data.filter((d) =>
    d.category === cat && (!kw || d.label.toLowerCase().includes(kw)))
  const countOf = (cat: string) => data.filter((d) => d.category === cat).length
  const total = CATEGORIES.reduce((a, c) => a + countOf(c), 0)

  const scrollTo = (cat: string) => {
    setActiveNav(cat)
    document.getElementById(`enum-card-${cat}`)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  }

  return (
    <div className="em-root">
      <style>{EM_CSS}</style>

      {/* 左侧分类导航 */}
      <aside className="em-rail">
        <div className="em-rail-title">枚举分类</div>
        {CATEGORIES.map((c) => (
          <a key={c} className={`em-ni ${activeNav === c ? 'on' : ''}`} onClick={() => scrollTo(c)}>
            <span className="ms ni-i">{CATEGORY_META[c].icon}</span>
            <span style={{ flex: 1 }}>{CATEGORY_META[c].label}</span>
            <span className="nc">{countOf(c)}</span>
          </a>
        ))}
        <div className="em-tot">共 <b>{total}</b> 个枚举值 · {CATEGORIES.length} 类</div>
      </aside>

      {/* 主区 */}
      <main className="em-main">
        <div className="em-head">
          <div style={{ flex: 1 }}>
            <h1 className="em-h1">枚举管理</h1>
            <div className="em-sub">维护用例/缺陷的各类枚举、端的执行口径与地址、App 应用包名</div>
          </div>
          <div className="em-ctrls">
            <div className="em-search">
              <span className="ms si">search</span>
              <input placeholder="搜索枚举值…" value={search} onChange={(e) => setSearch(e.target.value)} />
              {search && <span className="ms sx" onClick={() => setSearch('')}>close</span>}
            </div>
            <Select placeholder="分类筛选" allowClear value={filterCat} style={{ width: 150, height: 38 }}
              onChange={setFilterCat} options={CATEGORIES.map((c) => ({ value: c, label: CATEGORY_META[c].label }))} />
            <button className="em-btn primary" onClick={() => openCreate()}>
              <span className="ms" style={{ fontSize: 17 }}>add</span>新增枚举值
            </button>
          </div>
        </div>

        {cats.map((cat) => {
          const items = itemsOf(cat)
          const meta = CATEGORY_META[cat]
          const isRank = cat === 'priority' || cat === 'severity'
          return (
            <section key={cat} id={`enum-card-${cat}`} className="em-card">
              <div className="em-chead">
                <span className="ci ms">{meta.icon}</span>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span className="nm">{meta.label}</span>
                    <span className="cnt">{countOf(cat)}</span>
                  </div>
                  <div className="ds">{meta.desc}</div>
                </div>
                <a className="link" onClick={() => openLogs(cat)}>
                  <span className="ms" style={{ fontSize: 15 }}>history</span>操作记录
                </a>
                {cat === 'platform' && (
                  <a className="link" onClick={() => openLogs('base_url')}>
                    <span className="ms" style={{ fontSize: 15 }}>history</span>地址记录
                  </a>
                )}
                <a className="link acc" onClick={() => openCreate(cat)}>
                  <span className="ms" style={{ fontSize: 15 }}>add</span>添加
                </a>
              </div>

              <div className="em-cbody">
                {cat === 'platform' ? (
                  <PlatformTable items={items} matrix={matrix} pkgMap={pkgMap}
                    onEditEnd={openPop} onCell={openAddr} onPkg={openPkg} />
                ) : items.length === 0 ? (
                  <span className="em-empty">{kw ? '无匹配枚举值' : '暂无枚举值，点右上「添加」新增'}</span>
                ) : (
                  <div className="chipwrap">
                    {items.map((it) => {
                      const off = it.is_active === false
                      return (
                        <span key={it.id} className={`chip ${off ? 'off' : ''} ${isRank ? 'rank' : ''}`}
                          onClick={(e) => openPop(it, e)}>
                          {isRank
                            ? <span className="badge">{rankBadge(it.label)}</span>
                            : <span className={`state-dot ${off ? 'd-off' : 'd-on'}`} />}
                          {it.label}
                        </span>
                      )
                    })}
                    <span className="add-chip" onClick={() => openCreate(cat)}>
                      <span className="ms" style={{ fontSize: 15 }}>add</span>新增
                    </span>
                  </div>
                )}
              </div>
            </section>
          )
        })}
      </main>

      {/* 编辑气泡 */}
      {pop && (
        <>
          <div className="em-pop-scrim" onClick={() => setPop(null)} />
          <div className="em-pop" style={{ top: pop.rect.top, left: pop.rect.left }} onClick={(e) => e.stopPropagation()}>
            <div className="pt">重命名</div>
            <input className="re" value={pop.label} autoFocus
              onChange={(e) => setPop({ ...pop, label: e.target.value })}
              onKeyDown={(e) => { if (e.key === 'Enter') savePop() }} />
            {pop.item.category === 'platform' && (
              <>
                <div className="pt" style={{ marginTop: 12 }}>执行口径</div>
                <Select style={{ width: '100%' }} value={pop.parent ?? undefined}
                  placeholder="选择执行口径" options={PLATFORM_GROUP_OPTIONS}
                  onChange={(v) => setPop({ ...pop, parent: v })} />
              </>
            )}
            <div className="stt">
              <span>{pop.active ? '已启用' : '已停用'}</span>
              <span className={`switch ${pop.active ? 'on' : ''}`} onClick={() => setPop({ ...pop, active: !pop.active })}>
                <span className="knob" />
              </span>
            </div>
            <div className="pf">
              <button className="pdel" onClick={deletePop}>删除</button>
              <button className="psave" onClick={savePop}>保存</button>
            </div>
          </div>
        </>
      )}

      {/* 操作记录抽屉 */}
      {logCat && (
        <>
          <div className="em-scrim" onClick={() => setLogCat(null)} />
          <div className="em-drawer">
            <div className="dh">
              <div style={{ flex: 1 }}>
                <div className="dt">操作记录</div>
                <div className="dsub">{CATEGORY_LABEL[logCat] || logCat}</div>
              </div>
              <span className="x ms" onClick={() => setLogCat(null)}>close</span>
            </div>
            <div className="dbody">
              {logsLoading ? <div className="em-empty" style={{ padding: 24 }}>加载中…</div>
                : logs.length === 0 ? <div className="em-empty" style={{ padding: 24 }}>暂无操作记录</div>
                  : logs.map((lg) => <LogItem key={lg.id} log={lg} />)}
            </div>
          </div>
        </>
      )}

      {/* 新增枚举值弹窗 */}
      <Modal title="新增枚举值" open={createOpen} onCancel={() => setCreateOpen(false)}
        onOk={() => form.submit()} okText="创建" destroyOnClose>
        <Form form={form} layout="vertical" onFinish={submitCreate} style={{ marginTop: 8 }}>
          <Form.Item name="category" label="分类" rules={[{ required: true }]}>
            <Select options={CATEGORIES.map((c) => ({ value: c, label: CATEGORY_META[c].label }))} />
          </Form.Item>
          <Form.Item name="label" label="枚举值" rules={[{ required: true }]}>
            <Input placeholder="如 P0 / android-app / 登录模块" />
          </Form.Item>
          <Form.Item noStyle shouldUpdate={(a, b) => a.category !== b.category}>
            {() => form.getFieldValue('category') === 'platform' ? (
              <Form.Item name="parent_key" label="执行口径" rules={[{ required: true, message: '请选择执行口径' }]}>
                <Select placeholder="PC/移动端/小程序/接口" options={PLATFORM_GROUP_OPTIONS} />
              </Form.Item>
            ) : null}
          </Form.Item>
        </Form>
      </Modal>

      {/* 地址弹窗：一个端的所有环境地址放一起，至少填一个即可 */}
      <Modal title={`配置地址 — ${matrix?.platforms.find((p) => p.key === addrEnd)?.label || addrEnd || ''}`}
        open={addrOpen} onCancel={() => setAddrOpen(false)} onOk={() => addrForm.submit()} okText="保存" destroyOnClose>
        <div style={{ fontSize: 12.5, color: '#6a7180', margin: '2px 0 12px' }}>
          按环境分别填该端的被测地址，<b>至少填一个</b>；某环境留空表示不配置（已配的清空即删除）。
        </div>
        <Form form={addrForm} layout="vertical" onFinish={submitAddr}>
          {(matrix?.envs || []).map((e) => (
            <Form.Item key={e.key} name={e.key} label={`${e.label} 地址`}
              rules={[{ validator: (_, v) => (!v || /^https?:\/\//i.test(v.trim()) ? Promise.resolve() : Promise.reject(new Error('需以 http:// 或 https:// 开头'))) }]}>
              <Input placeholder="留空=不配置该环境" style={{ fontFamily: 'ui-monospace, Menlo, monospace' }} allowClear />
            </Form.Item>
          ))}
        </Form>
      </Modal>

      {/* 应用包名弹窗 */}
      <Modal title={pkgEditing ? '编辑应用包名' : '配置应用包名'} open={pkgOpen} onCancel={() => setPkgOpen(false)}
        onOk={() => pkgForm.submit()} destroyOnClose
        footer={pkgEditing ? [
          <button key="d" className="em-mbtn danger" onClick={async () => { if (await confirmDialog({ title: '删除包名', desc: '确认删除该端的应用包名？', ok: '删除', danger: true })) deletePkg() }}>删除</button>,
          <button key="c" className="em-mbtn" onClick={() => setPkgOpen(false)}>取消</button>,
          <button key="o" className="em-mbtn primary" onClick={() => pkgForm.submit()}>保存</button>,
        ] : undefined}>
        <Form form={pkgForm} layout="vertical" onFinish={submitPkg} style={{ marginTop: 8 }}>
          <Form.Item name="platform_key" label="端"><Input disabled /></Form.Item>
          <Form.Item name="pkg" label="应用包名（android package）" rules={[{ required: true, message: '请填写应用包名' }]}>
            <Input placeholder="如 com.example.app" style={{ fontFamily: 'ui-monospace, Menlo, monospace' }} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  )
}

// ── 端配置表（spec §8）──────────────────────────────────────────────────
function PlatformTable({ items, matrix, pkgMap, onEditEnd, onCell, onPkg }: {
  items: EnumItem[]
  matrix: UrlMatrix | null
  pkgMap: Record<string, { id: string; label: string }>
  onEditEnd: (item: EnumItem, e: React.MouseEvent) => void
  onCell: (platform_key: string) => void
  onPkg: (init: { platform_key: string; id?: string; pkg?: string }) => void
}) {
  if (!items.length) return <span className="em-empty">暂无端，点右上「添加」新增</span>
  const envs = matrix?.envs || []
  const urlOf = (key: string, env: string) => matrix?.platforms.find((p) => p.key === key)?.urls?.[env]
  return (
    <table className="ep-table">
      <thead>
        <tr>
          <th style={{ width: 150 }}>端</th>
          <th style={{ width: 110 }}>执行口径</th>
          {envs.map((e) => <th key={e.key}>{e.label} 地址</th>)}
          <th style={{ width: 200 }}>应用包名</th>
        </tr>
      </thead>
      <tbody>
        {items.map((row) => {
          const tg = TAG_STYLE[row.parent_key || ''] || TAG_STYLE.pc
          const pk = pkgMap[row.key]
          return (
            <tr key={row.id}>
              <td><a className="ep-name" onClick={(e) => onEditEnd(row, e)}>{row.label}</a></td>
              <td>
                <span className="ep-tag" style={{ color: tg.color, borderColor: tg.border, background: tg.bg }}
                  onClick={(e) => onEditEnd(row, e)}>
                  {PLATFORM_GROUP_LABEL[row.parent_key || ''] || '未分组'}
                </span>
              </td>
              {envs.map((e) => {
                if (row.parent_key !== 'pc') return <td key={e.key}><span className="ep-dash">—</span></td>
                const cell = urlOf(row.key, e.key)
                return (
                  <td key={e.key}>
                    {cell
                      ? <a className="url" onClick={() => onCell(row.key)}>{cell.url}</a>
                      : <a className="cfg-link" onClick={() => onCell(row.key)}>＋ 配置地址</a>}
                  </td>
                )
              })}
              <td>
                {row.parent_key !== 'app' ? <span className="ep-dash">—</span>
                  : pk?.label
                    ? <a className="url" onClick={() => onPkg({ platform_key: row.key, id: pk.id, pkg: pk.label })}>{pk.label}</a>
                    : <a className="cfg-link" onClick={() => onPkg({ platform_key: row.key })}>＋ 配置包名</a>}
              </td>
            </tr>
          )
        })}
      </tbody>
    </table>
  )
}

// ── 操作记录条（spec §10）──────────────────────────────────────────────
const ACT: Record<string, { label: string; color: string; bg: string }> = {
  create: { label: '新增', color: '#2f9e6f', bg: '#e7f3ee' },
  update: { label: '编辑', color: '#3a6ea5', bg: '#eef4fb' },
  delete: { label: '删除', color: '#b05a3c', bg: '#fbe9e6' },
}
const AV_COLORS = ['#c9634a', '#3a6ea5', '#2f9e6f', '#7a5bb0', '#c98a2b']
function LogItem({ log }: { log: any }) {
  const op = ACT[log.operation] || { label: log.operation, color: '#6a7180', bg: '#f0f1f3' }
  const who = log.operator || '系统'
  const av = AV_COLORS[[...who].reduce((a, c) => a + c.charCodeAt(0), 0) % AV_COLORS.length]
  return (
    <div className="log-item">
      <span className="av" style={{ background: av }}>{who.slice(0, 1)}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div className="l1">
          <span className="badge-act" style={{ color: op.color, background: op.bg }}>{op.label}</span>
          <span className="val" style={{ marginLeft: 8 }}>{log.value}</span>
        </div>
        <div className="l2">{who} · {log.created_at ? dayjs(log.created_at).format('YYYY-MM-DD HH:mm') : '-'}</div>
      </div>
    </div>
  )
}

const EM_CSS = `
.em-root{
  --bg:#f6f7f9;--surface:#fff;--surface-2:#fbfbfa;--fg:#21252b;--muted:#6a7180;--faint:#9aa1ad;
  --border:#e9ebef;--border-2:#dfe2e7;--accent:#d97757;--accent-ink:#b05a3c;--accent-soft:#fbf0ea;--accent-line:#f0dccf;
  --ok:#2f9e6f;--off:#9aa1ad;--off-soft:#f0f1f3;
  --mono:ui-monospace,"SF Mono","JetBrains Mono",Menlo,Consolas,monospace;
  --shadow-sm:0 1px 2px rgba(24,28,35,.04),0 1px 3px rgba(24,28,35,.05);
  --shadow-md:0 8px 24px rgba(24,28,35,.10),0 2px 6px rgba(24,28,35,.06);
  --shadow-lg:0 24px 60px rgba(24,28,35,.18);
  display:grid;grid-template-columns:236px 1fr;max-width:1560px;margin:0 auto;color:var(--fg);
  font-size:14px;line-height:1.5;
}
@media(max-width:1080px){.em-root{grid-template-columns:1fr}.em-rail{display:none}}
.em-root .ms{font-variation-settings:'wght' 400}
/* 左栏 */
.em-rail{position:sticky;top:0;height:calc(100vh - 56px);padding:22px 16px 22px 24px;overflow:auto}
.em-rail-title{font-size:11px;letter-spacing:.09em;text-transform:uppercase;color:var(--faint);font-weight:650;margin:0 0 12px 8px}
.em-ni{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:9px;font-weight:550;color:var(--muted);cursor:pointer;user-select:none}
.em-ni .ni-i{font-size:18px;color:var(--faint);width:18px}
.em-ni .nc{font-family:var(--mono);font-weight:600;font-size:11px;color:var(--faint);background:var(--off-soft);padding:3px 7px;border-radius:20px}
.em-ni:hover{background:var(--surface);color:var(--fg)}
.em-ni.on{background:var(--accent-soft);color:var(--accent-ink)}
.em-ni.on .ni-i{color:var(--accent)}
.em-ni.on .nc{background:#fff;color:var(--accent-ink)}
.em-tot{margin-top:16px;padding:12px;border-top:1px dashed var(--border-2);font-size:12px;color:var(--muted)}
.em-tot b{font-family:var(--mono);font-weight:650;font-size:14px;color:var(--fg)}
/* 主区 */
.em-main{padding:24px 24px 80px;min-width:0}
.em-head{display:flex;align-items:flex-end;gap:16px;margin-bottom:18px}
.em-h1{font-size:22px;font-weight:700;letter-spacing:-.02em;margin:0}
.em-sub{font-size:13px;color:var(--muted);margin-top:4px}
.em-ctrls{display:flex;align-items:center;gap:10px}
.em-search{position:relative;width:230px;height:38px}
.em-search input{width:100%;height:38px;padding:0 32px 0 34px;border-radius:10px;border:1px solid var(--border-2);background:var(--surface);font-size:13.5px;outline:none}
.em-search input:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.em-search .si{position:absolute;left:11px;top:11px;font-size:16px;color:var(--faint)}
.em-search .sx{position:absolute;right:8px;top:8px;font-size:16px;color:var(--faint);cursor:pointer;background:var(--off-soft);border-radius:6px;padding:2px}
.em-btn{display:inline-flex;align-items:center;gap:7px;height:38px;padding:0 14px;border-radius:10px;border:1px solid var(--border-2);background:var(--surface);font-weight:600;font-size:13.5px;color:var(--fg);cursor:pointer}
.em-btn.primary{background:var(--accent);border-color:var(--accent);color:#fff;box-shadow:var(--shadow-sm)}
.em-btn.primary:hover{background:var(--accent-ink);border-color:var(--accent-ink)}
/* 卡片 */
.em-card{background:var(--surface);border:1px solid var(--border);border-radius:12px;box-shadow:var(--shadow-sm);margin-bottom:16px;scroll-margin-top:16px;overflow:hidden}
.em-chead{display:flex;align-items:center;gap:12px;padding:15px 18px;border-bottom:1px solid var(--border)}
.em-chead .ci{width:34px;height:34px;border-radius:9px;background:var(--accent-soft);color:var(--accent);display:flex;align-items:center;justify-content:center;font-size:19px;flex:none}
.em-chead .nm{font-weight:660;font-size:15px;letter-spacing:-.01em}
.em-chead .cnt{font-family:var(--mono);font-weight:600;font-size:11px;color:var(--muted);background:var(--off-soft);padding:3px 7px;border-radius:20px}
.em-chead .ds{font-size:12px;color:var(--faint);margin-top:2px}
.em-chead .link{display:inline-flex;align-items:center;gap:4px;padding:7px 10px;border-radius:8px;font-weight:600;font-size:13px;color:var(--muted);cursor:pointer}
.em-chead .link:hover{background:var(--surface-2)}
.em-chead .link.acc{color:var(--accent-ink)}
.em-chead .link.acc:hover{background:var(--accent-soft)}
.em-cbody{padding:16px 18px}
.em-empty{font-size:13px;color:var(--faint)}
/* chips */
.chipwrap{display:flex;flex-wrap:wrap;gap:8px}
.chip{display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 12px;border:1px solid var(--border-2);border-radius:8px;font-weight:550;font-size:13px;cursor:pointer;background:var(--surface)}
.chip:hover{border-color:var(--accent-line);box-shadow:var(--shadow-sm)}
.chip .state-dot{width:6px;height:6px;border-radius:50%}
.chip .d-on{background:var(--ok)}.chip .d-off{background:var(--off)}
.chip.off{background:var(--off-soft);color:var(--off);border-style:dashed;text-decoration:line-through;text-decoration-color:var(--faint)}
.chip.rank .badge{font-family:var(--mono);font-weight:700;font-size:10px;color:#fff;background:var(--accent);padding:3px 5px;border-radius:5px}
.add-chip{display:inline-flex;align-items:center;gap:4px;height:32px;padding:0 12px;border:1px dashed var(--border-2);border-radius:8px;font-weight:600;font-size:13px;color:var(--muted);cursor:pointer}
.add-chip:hover{border-color:var(--accent);color:var(--accent-ink);background:var(--accent-soft)}
/* 端表 */
.ep-table{width:100%;border-collapse:collapse;font-size:13.5px}
.ep-table th{padding:0 14px 10px;border-bottom:1px solid var(--border);font-weight:600;font-size:11.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--faint);text-align:left}
.ep-table td{padding:11px 14px;border-bottom:1px solid var(--border)}
.ep-table tbody tr:hover{background:var(--surface-2)}
.ep-name{font-weight:650;color:var(--accent-ink);cursor:pointer}
.ep-tag{display:inline-flex;align-items:center;height:24px;padding:0 9px;border-radius:6px;font-weight:600;font-size:12px;border:1px solid;cursor:pointer}
.ep-table .url{font-family:var(--mono);font-weight:500;font-size:12.5px;color:var(--accent-ink);cursor:pointer;word-break:break-all}
.ep-table .url:hover{text-decoration:underline}
.ep-table .cfg-link{color:var(--accent-ink);font-weight:600;font-size:13px;cursor:pointer;padding:5px 8px;border-radius:7px}
.ep-table .cfg-link:hover{background:var(--accent-soft)}
.ep-dash{color:var(--faint)}
/* 编辑气泡 */
.em-pop-scrim{position:fixed;inset:0;z-index:74}
.em-pop{position:fixed;z-index:75;width:264px;padding:14px;border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow-md)}
.em-pop .pt{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--faint);font-weight:650;margin-bottom:8px}
.em-pop .re{width:100%;height:38px;padding:0 11px;border:1px solid var(--border-2);border-radius:9px;font-size:14px;outline:none}
.em-pop .re:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.em-pop .stt{display:flex;align-items:center;justify-content:space-between;margin-top:12px;font-size:13px;color:var(--muted)}
.em-pop .switch{width:42px;height:24px;border-radius:20px;background:var(--off-soft);border:1px solid var(--border-2);position:relative;cursor:pointer;flex:none;transition:.15s}
.em-pop .switch .knob{position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:50%;background:#fff;box-shadow:var(--shadow-sm);transition:.15s}
.em-pop .switch.on{background:var(--accent);border-color:var(--accent)}
.em-pop .switch.on .knob{transform:translateX(18px)}
.em-pop .pf{display:flex;align-items:center;justify-content:space-between;margin-top:14px}
.em-pop .pdel{color:var(--accent-ink);font-weight:600;font-size:13px;padding:8px;border-radius:8px;border:none;background:none;cursor:pointer}
.em-pop .pdel:hover{background:#fbe9e6}
.em-pop .psave{height:36px;padding:0 16px;border-radius:9px;background:var(--accent);color:#fff;font-weight:650;font-size:13px;border:none;cursor:pointer}
.em-pop .psave:hover{background:var(--accent-ink)}
/* 抽屉 */
.em-scrim{position:fixed;inset:0;background:rgba(20,24,30,.34);z-index:60}
.em-drawer{position:fixed;top:0;right:0;height:100%;width:440px;max-width:92vw;background:var(--surface);box-shadow:var(--shadow-lg);z-index:70;display:flex;flex-direction:column}
.em-drawer .dh{display:flex;align-items:center;gap:12px;padding:18px 20px;border-bottom:1px solid var(--border)}
.em-drawer .dt{font-weight:660;font-size:16px}
.em-drawer .dsub{font-size:12px;color:var(--muted);margin-top:2px}
.em-drawer .x{width:32px;height:32px;border-radius:8px;background:var(--surface-2);display:flex;align-items:center;justify-content:center;color:var(--muted);cursor:pointer;font-size:18px}
.em-drawer .dbody{padding:8px 20px 24px;overflow:auto;flex:1}
.log-item{display:flex;gap:12px;padding:14px 0;border-bottom:1px solid var(--border)}
.log-item .av{width:32px;height:32px;border-radius:9px;color:#fff;font-weight:650;font-size:12px;display:flex;align-items:center;justify-content:center;flex:none}
.log-item .l1{font-size:13.5px}
.log-item .val{font-weight:600;color:var(--accent-ink)}
.log-item .l2{font-family:var(--mono);font-weight:500;font-size:11.5px;color:var(--faint);margin-top:3px}
.log-item .badge-act{font-size:11px;font-weight:650;padding:2px 7px;border-radius:5px}
/* 弹窗底部按钮 */
.em-mbtn{height:36px;padding:0 16px;border-radius:9px;border:1px solid var(--border-2);background:var(--surface);font-weight:600;font-size:13px;cursor:pointer}
.em-mbtn.primary{background:var(--accent);border-color:var(--accent);color:#fff}
.em-mbtn.danger{color:var(--accent-ink);border-color:#f0dccf;float:left}
`
