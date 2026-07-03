import { useState, useEffect } from 'react'
import { Modal, Button, Typography, Space, Card, Tag, Select, message } from 'antd'
import { executionsApi, workerApi, enumsApi } from '../api'
import type { UrlMatrix } from '../api'
import { platformTagStyle } from '../styles/tagColors'
import { MONO_FONT } from '../styles/theme'

// 复制到剪贴板：优先 navigator.clipboard(仅 HTTPS/localhost 可用)，HTTP 下回退 execCommand
async function copyText(text: string) {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      message.success('已复制')
      return
    }
  } catch { /* 落到回退 */ }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.style.position = 'fixed'; ta.style.top = '-9999px'
    document.body.appendChild(ta); ta.focus(); ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    ok ? message.success('已复制') : message.warning('复制失败，请手动选中复制')
  } catch {
    message.warning('复制失败，请手动选中复制')
  }
}

// 探测当前浏览器所在系统，用于「连接我的真机」默认给对应平台的执行助手与命令
function detectOS(): 'mac' | 'win' {
  const s = `${navigator.platform || ''} ${navigator.userAgent || ''}`.toLowerCase()
  if (s.includes('mac') || s.includes('darwin') || s.includes('iphone') || s.includes('ipad')) return 'mac'
  return 'win'
}

// 业务端 → 执行口径分组：内置兜底（新端若未在「枚举管理→端」配置执行口径时使用）。
// 正式来源是 platform 枚举的 parent_key（可在枚举管理里配置），由 loadPlatformGroups() 载入并覆盖此表。
const PLATFORM_GROUP_FALLBACK: Record<string, 'pc' | 'app' | 'miniprogram' | 'api'> = {
  'web-admin': 'pc', 'web-portal': 'pc',
  'android-app': 'app', 'ios-app': 'app', 'mini-app': 'miniprogram',
  web: 'pc', backend_api: 'api', android: 'app', ios: 'app', miniprogram: 'miniprogram',
}

// 运行时从枚举配置载入的「端→执行口径」映射，覆盖兜底表。App 启动调 loadPlatformGroups() 拉一次。
let dynamicPlatformGroup: Record<string, 'pc' | 'app' | 'miniprogram' | 'api'> = {}

/** 从后端 platform 枚举的 parent_key 载入「端→执行口径」映射（配置驱动）。失败则保留兜底。 */
export async function loadPlatformGroups(): Promise<void> {
  try {
    const r = await enumsApi.list('platform')
    const m: Record<string, 'pc' | 'app' | 'miniprogram' | 'api'> = {}
    for (const e of r.data || []) {
      const pk = e.parent_key
      if (pk === 'pc' || pk === 'app' || pk === 'miniprogram' || pk === 'api') m[e.key] = pk
    }
    dynamicPlatformGroup = m
  } catch { /* 拉取失败保留内置兜底 */ }
}

function platformGroupOf(p: string): 'pc' | 'app' | 'miniprogram' | 'api' | undefined {
  return dynamicPlatformGroup[p] || PLATFORM_GROUP_FALLBACK[p]
}

/** 仅 PC / 接口 可在服务端自动执行；App(真机) 与 小程序 需专用环境 */
export function isAutoExecutable(bucket: string): boolean {
  return bucket === 'web' || bucket === 'api'
}

/** 按端分类用例 → web / api / mobile / miniprogram（多端优先级 api > app > 小程序 > pc）。 */
export function categorizeCaseByPlatform(c: any): 'web' | 'api' | 'mobile' | 'miniprogram' {
  const platforms: string[] = c?.platforms || []
  const groups = platforms.map(platformGroupOf).filter(Boolean)
  if (c?.case_type === 'api' || groups.includes('api')) return 'api'
  if (groups.includes('app')) return 'mobile'
  if (groups.includes('miniprogram')) return 'miniprogram'
  return 'web'
}

/**
 * 统一的执行测试配置弹框（需求详情与用例库共用）。
 * - PC/Web：账号切换（框架已配账号 / 临时账号，用完即弃）
 * - 接口：填代码仓库 / API 基础 URL
 * - 移动端：选目标真机（默认你自己执行机连的设备，下拉含你的+公共设备），无设备时引导「连接我的真机」
 */
export default function ExecConfigModal({
  open, cases, categorizeCase, execApiBaseUrl, setExecApiBaseUrl, onCancel, onConfirm,
}: {
  open: boolean
  cases: any[]
  categorizeCase: (c: any) => string
  execApiBaseUrl: string
  setExecApiBaseUrl: (v: string) => void
  onCancel: () => void
  onConfirm: (runMode: string, accountOverrides?: Record<string, any>, targetDevice?: string | null, env?: string, packageOverrides?: Record<string, string>) => void
}) {
  const automatedCases = cases.filter((c) => c.is_automated)
  const [view, setView] = useState<'choose' | 'config'>(automatedCases.length > 0 ? 'choose' : 'config')

  useEffect(() => {
    setView(automatedCases.length > 0 ? 'choose' : 'config')
    setOpenDD(null)
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // PC web 账号选择
  const webPlatforms = Array.from(new Set(
    cases.filter((c) => categorizeCase(c) === 'web').flatMap((c) => c.platforms || [])
  )) as string[]
  const [webAccounts, setWebAccounts] = useState<Record<string, { covered: boolean; auth_type?: string; tenant?: boolean; accounts: { role: string; label: string }[] }>>({})
  const [acctSel, setAcctSel] = useState<Record<string, any>>({})
  const [openDD, setOpenDD] = useState<string | null>(null)
  useEffect(() => {
    if (!open || webPlatforms.length === 0) { setWebAccounts({}); return }
    executionsApi.webAccounts(webPlatforms)
      .then((r) => {
        setWebAccounts(r.data || {})
        const init: Record<string, any> = {}
        Object.entries(r.data || {}).forEach(([p, info]: [string, any]) => {
          if (info.covered) init[p] = { mode: 'role', role: (info.accounts[0]?.role || 'default') }
        })
        setAcctSel(init)
      })
      .catch(() => setWebAccounts({}))
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // PC web 执行环境（默认 sit，可切 dev）；矩阵用于判断某端是否缺所选环境地址（缺则提示回退 SIT）
  const [execEnv, setExecEnv] = useState<string>('sit')
  const [urlMatrix, setUrlMatrix] = useState<UrlMatrix | null>(null)
  useEffect(() => {
    if (!open) return
    setExecEnv('sit')  // 每次打开重置为 SIT
    if (webPlatforms.length === 0) { setUrlMatrix(null); return }
    enumsApi.urlMatrix().then((r) => setUrlMatrix(r.data)).catch(() => setUrlMatrix(null))
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps
  const envOptions = urlMatrix?.envs?.length ? urlMatrix.envs : [{ key: 'sit', label: 'SIT', category: 'base_url' }]
  // 所选环境下，本批 web 端里缺地址的端（将回退 SIT）
  const missingEnvPlatforms = execEnv === 'sit' ? [] : webPlatforms.filter((p) => {
    const row = urlMatrix?.platforms.find((x) => x.key === p)
    return !row?.urls?.[execEnv]
  })

  const isSms = (p: string) => webAccounts[p]?.auth_type === 'sms_code'
  const buildOverrides = (): Record<string, any> => {
    const ov: Record<string, any> = {}
    Object.entries(acctSel).forEach(([p, sel]: [string, any]) => {
      if (!sel) return
      if (sel.mode === 'temp') {
        if (!sel.username) return
        if (isSms(p)) ov[p] = { username: sel.username, ...(sel.tenant_name ? { tenant_name: sel.tenant_name } : {}) }
        else if (sel.password) ov[p] = { username: sel.username, password: sel.password }
      } else if (sel.role && sel.role !== 'default') {
        ov[p] = { role: sel.role }
      }
    })
    return ov
  }
  const tempIncomplete = Object.entries(acctSel).some(([p, s]: [string, any]) =>
    s?.mode === 'temp' && (!s.username || (!isSms(p) && !s.password)))

  // 移动端真机（执行机 worker 上报，按归属选择）
  const [devices, setDevices] = useState<{ serial: string; model: string; worker_name?: string; is_shared?: boolean; is_public?: boolean; busy?: boolean; owner_user_id?: string | null }[]>([])
  // 远程真机（Sonic 云真机，serial 已编码为 "sonic:<udId>"）
  const [sonicDevices, setSonicDevices] = useState<{ serial: string; model: string; busy?: boolean; occupied_by?: string | null }[]>([])
  const [appQueue, setAppQueue] = useState(0)
  const [devChecking, setDevChecking] = useState(false)
  const [selectedDevice, setSelectedDevice] = useState<string | undefined>(undefined)
  // 无本地真机时的选择：'connect'=连自己的真机 | 'remote'=远程真机(Sonic) | 'public'=公共设备
  const [mobileMode, setMobileMode] = useState<'connect' | 'remote' | 'public' | undefined>(undefined)
  const [guideOpen, setGuideOpen] = useState(false)
  const [token, setToken] = useState('')
  const [myUid, setMyUid] = useState('')
  // 执行机所在系统（默认按当前浏览器探测；可在引导里切换——浏览的机器未必是插手机的执行机）
  const [osChoice, setOsChoice] = useState<'mac' | 'win'>(detectOS())
  const loadDevices = () => {
    setDevChecking(true)
    executionsApi.devices()
      .then((r) => { setDevices(r.data.devices || []); setSonicDevices(r.data.sonic_devices || []); setAppQueue(r.data.app_queue || 0) })
      .catch(() => { setDevices([]); setSonicDevices([]) })
      .finally(() => setDevChecking(false))
  }

  useEffect(() => {
    if (!open || cases.filter((c) => categorizeCase(c) === 'mobile').length === 0) return
    workerApi.installInfo(osChoice).then((r) => { setToken(r.data.worker_token); setMyUid(r.data.owner_user_id) }).catch(() => {})
    loadDevices()
  }, [open]) // eslint-disable-line react-hooks/exhaustive-deps

  // 还没检测到真机时自动轮询(用户刚把 worker 跑起来，无需关弹窗重开)
  useEffect(() => {
    if (!open || devices.length > 0 || sonicDevices.length > 0) return
    if (cases.filter((c) => categorizeCase(c) === 'mobile').length === 0) return
    const t = setInterval(loadDevices, 4000)
    return () => clearInterval(t)
  }, [open, devices.length, sonicDevices.length]) // eslint-disable-line react-hooks/exhaustive-deps

  // 我自己执行机连的设备 / 公共设备(admin 手机)
  const myDevice = myUid ? devices.find((d) => d.owner_user_id === myUid) : undefined
  const publicDevices = devices.filter((d) => d.is_public)
  const hasMyDevice = !!myDevice

  // 默认目标设备：有「我自己的设备」才自动选它；否则按所选模式定(连真机/远程/公共)。
  useEffect(() => {
    if (hasMyDevice) { setSelectedDevice(myDevice!.serial); return }
    if (mobileMode === 'public' && publicDevices.length) setSelectedDevice(publicDevices[0].serial)
    else if (mobileMode === 'remote' && sonicDevices.length) setSelectedDevice(sonicDevices[0].serial)
    else setSelectedDevice(undefined)
  }, [devices, sonicDevices, myUid, mobileMode]) // eslint-disable-line react-hooks/exhaustive-deps

  const webCases = cases.filter((c) => categorizeCase(c) === 'web')
  const apiCases = cases.filter((c) => categorizeCase(c) === 'api')
  const mobileCases = cases.filter((c) => categorizeCase(c) === 'mobile')
  // 移动端只有在选定了目标设备(我的设备 或 显式选了公共设备)时才计入可执行数
  const executableCount = webCases.length + apiCases.length + (selectedDevice ? mobileCases.length : 0)

  // App 换测试包：选完设备后可选是否换包；批量多 app 时按 app 分别选包版本(下拉数据源=后端 app-packages)
  const appPlatforms = Array.from(new Set(
    mobileCases.flatMap((c) => (c.platforms || []).filter((p: string) => platformGroupOf(p) === 'app'))
  )) as string[]
  const [changePkg, setChangePkg] = useState(false)
  const [pkgSel, setPkgSel] = useState<Record<string, string>>({})
  const [pkgOptions, setPkgOptions] = useState<Record<string, { id: string; label: string }[]>>({})
  useEffect(() => { setChangePkg(false); setPkgSel({}) }, [open])
  useEffect(() => {
    if (!open || !changePkg || appPlatforms.length === 0) return
    appPlatforms.forEach((app) => {
      executionsApi.appPackages(app)
        .then((r) => setPkgOptions((prev) => ({ ...prev, [app]: r.data.packages || [] })))
        .catch(() => {})
    })
  }, [open, changePkg, appPlatforms.join(',')]) // eslint-disable-line react-hooks/exhaustive-deps
  const buildPackageOverrides = (): Record<string, string> | undefined => {
    if (!changePkg) return undefined
    const ov: Record<string, string> = {}
    appPlatforms.forEach((app) => { if (pkgSel[app]) ov[app] = pkgSel[app] })
    return Object.keys(ov).length ? ov : undefined
  }

  if (!open) return null

  const BRAND = '#D97757', BRAND_SOFT = '#FBEEE6', BRAND_BORDER = '#EFD6C8'
  const BRAND_GRAD = 'linear-gradient(135deg,#E8916B 0%,#D97757 100%)'

  // 执行环境切换（PC/Web）：默认 SIT，可切开发；整批统一一个环境。放在执行账号上方，
  // choose(选择执行方式) 与 config(执行配置) 两个视图都展示，保证任何执行路径都能选到环境。
  const envSwitch = webCases.length > 0 ? (
    <div style={{ marginBottom: 16 }}>
      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#64748B', marginBottom: 10 }}>执行环境</div>
      <div style={{ display: 'flex', gap: 8 }}>
        {envOptions.map((e) => {
          const active = execEnv === e.key
          return (
            <div key={e.key} onClick={() => setExecEnv(e.key)}
              style={{ flex: 1, height: 40, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6, cursor: 'pointer',
                border: `1.5px solid ${active ? BRAND_BORDER : '#E7ECF0'}`, borderRadius: 10,
                background: active ? BRAND_SOFT : '#fff', color: active ? BRAND : '#64748B', fontSize: 13, fontWeight: active ? 600 : 500 }}>
              {active && <span className="ms" style={{ fontSize: 16 }}>check</span>}
              {e.label}
            </div>
          )
        })}
      </div>
      {missingEnvPlatforms.length > 0 && (
        <div style={{ marginTop: 10, background: '#FFFBE6', border: '1px solid #FADB6E', borderRadius: 9, padding: '9px 12px', display: 'flex', gap: 8 }}>
          <span className="ms" style={{ fontSize: 16, color: '#D48806', marginTop: 1 }}>info</span>
          <div style={{ fontSize: 12, color: '#8A6D0B', lineHeight: 1.7 }}>
            {missingEnvPlatforms.join('、')} 未配置该环境地址，将回退用 <b>SIT</b> 地址执行（可在「枚举管理 → pc端地址」补充）。
          </div>
        </div>
      )}
    </div>
  ) : null

  if (view === 'choose') {
    return (
      <Modal title="执行测试" open={open} onCancel={onCancel} width={520} footer={<Button onClick={onCancel}>取消</Button>}>
        <Typography.Text type="secondary" style={{ display: 'block', marginBottom: 16 }}>
          已选 {cases.length} 条用例，其中 {automatedCases.length} 条有自动化脚本，请选择执行方式：
        </Typography.Text>
        {envSwitch}
        <Space direction="vertical" style={{ width: '100%' }} size="middle">
          <Card hoverable onClick={() => onConfirm('automated', buildOverrides(), selectedDevice ?? null, execEnv, buildPackageOverrides())} style={{ cursor: 'pointer', borderColor: '#52c41a' }}>
            <Space align="start">
              <Tag color="success" style={{ fontSize: 13 }}>执行自动化用例</Tag>
              <Typography.Text type="secondary">直接运行已生成的自动化脚本，快速验证</Typography.Text>
            </Space>
          </Card>
          <Card hoverable onClick={() => setView('config')} style={{ cursor: 'pointer', borderColor: '#1890ff' }}>
            <Space align="start">
              <Tag color="blue" style={{ fontSize: 13 }}>重新执行测试</Tag>
              <Typography.Text type="secondary">按测试配置重新执行，通过后更新自动化脚本</Typography.Text>
            </Space>
          </Card>
        </Space>
      </Modal>
    )
  }

  const catOf = (p: string): { label: string; icon: string } => {
    const c = cases.find((x) => (x.platforms || []).includes(p))
    const k = c ? categorizeCase(c) : 'web'
    if (k === 'api') return { label: '接口', icon: 'api' }
    if (k === 'mobile') return { label: '移动端', icon: 'smartphone' }
    if (k === 'miniprogram') return { label: '小程序', icon: 'widgets' }
    return { label: 'PC / Web', icon: 'desktop_windows' }
  }
  const allPlatforms = Array.from(new Set(cases.flatMap((c) => c.platforms || []))) as string[]
  const inExec = tempIncomplete
  const execLabel = tempIncomplete ? '请填写临时账号' : `开始执行（${executableCount} 条）`
  const origin = typeof window !== 'undefined' ? window.location.origin : ''
  const tokenVal = token || '<向管理员索取>'
  // WORKER_OWNER=当前登录用户 → 设备归属你 → 执行时默认选你自己的设备。mac 走 bash，Windows 走 PowerShell。
  const runCmd = osChoice === 'mac'
    ? `chmod +x ./tp-worker && PLATFORM_URL="${origin}" WORKER_TOKEN="${tokenVal}" WORKER_OWNER="${myUid}" ./tp-worker`
    : `$env:PLATFORM_URL="${origin}"; $env:WORKER_TOKEN="${tokenVal}"; $env:WORKER_OWNER="${myUid}"; .\\tp-worker.exe`

  return (
    <>
    <div onClick={onCancel} style={{ position: 'fixed', inset: 0, zIndex: 1100, background: 'rgba(15,23,42,.4)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <style>{`
        @keyframes execFadeUp{from{opacity:0;transform:translateY(16px)}to{opacity:1;transform:none}}
        .exec-input{transition:all .15s}
        .exec-input:focus{border-color:${BRAND}!important;box-shadow:0 0 0 3px rgba(217,119,87,.12);outline:none}
        .exec-opt:hover{background:#FAFCFD}
      `}</style>
      <div onClick={(e) => e.stopPropagation()} style={{ width: 480, maxHeight: '88vh', background: '#fff', borderRadius: 18, boxShadow: '0 24px 64px -16px rgba(15,23,42,.28)', animation: 'execFadeUp .28s cubic-bezier(.22,1,.36,1)', display: 'flex', flexDirection: 'column' }}>
        {/* Header */}
        <div style={{ padding: '20px 24px 16px', borderBottom: '1px solid #F1F4F6' }}>
          <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between' }}>
            <div>
              <div style={{ fontSize: 16, fontWeight: 700, color: '#0F172A', marginBottom: 10 }}>执行测试配置</div>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                {allPlatforms.map((p) => {
                  const ct = catOf(p)
                  return (
                    <span key={p} style={{ ...platformTagStyle(p), display: 'inline-flex', alignItems: 'center', gap: 4, fontSize: 12, fontWeight: 600, borderRadius: 7, padding: '3px 9px', margin: 0 }}>
                      <span className="ms" style={{ fontSize: 14 }}>{ct.icon}</span>{p} · {ct.label}
                    </span>
                  )
                })}
              </div>
            </div>
            <span className="ms" onClick={onCancel} style={{ fontSize: 22, color: '#B0BAC4', cursor: 'pointer', lineHeight: 1 }}
              onMouseEnter={(e) => ((e.target as HTMLElement).style.color = '#475569')}
              onMouseLeave={(e) => ((e.target as HTMLElement).style.color = '#B0BAC4')}>close</span>
          </div>
        </div>

        {/* Body */}
        <div style={{ padding: '18px 24px', overflowY: 'auto' }}>
          {/* PC / Web */}
          {webCases.length > 0 && (
            <div style={{ marginBottom: apiCases.length || mobileCases.length ? 18 : 0 }}>
              <div style={{ background: BRAND_SOFT, border: `1px solid ${BRAND_BORDER}`, borderRadius: 11, padding: '13px 15px', display: 'flex', gap: 11, marginBottom: 16 }}>
                <span className="ms" style={{ fontSize: 18, color: BRAND, marginTop: 1 }}>info</span>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: BRAND }}>将用 Playwright 驱动浏览器执行</div>
                  <div style={{ fontSize: 12, color: '#7A6050', lineHeight: 1.7 }}>登录态由自动化框架自动保活（失效/新端自动重登）；如需用其他权限账号验证，可在下方按端切换。</div>
                </div>
              </div>

              {/* 执行环境切换（提取为 envSwitch，choose/config 两视图共用）：默认 SIT，账号上方 */}
              {envSwitch}

              {webPlatforms.filter((p) => webAccounts[p] && !webAccounts[p].covered).map((p) => (
                <div key={p} style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ ...platformTagStyle(p), margin: 0, fontSize: 12, borderRadius: 7, padding: '2px 8px' }}>{p}</span>
                  <span style={{ fontSize: 12, color: '#B0BAC4' }}>未接入自动化框架（地址无效/未配登录），暂不支持账号切换</span>
                </div>
              ))}
              {webPlatforms.filter((p) => webAccounts[p]?.covered).map((p) => {
                const info = webAccounts[p]
                const sel = acctSel[p] || { mode: 'role', role: 'default' }
                const setSel = (patch: any) => setAcctSel((prev) => ({ ...prev, [p]: { ...sel, ...patch } }))
                const opts = [
                  ...(info?.accounts || []).map((a) => ({ key: `role:${a.role}`, icon: 'person', name: a.label, desc: '框架配置账号' })),
                  { key: 'temp', icon: 'manage_accounts', name: '临时账号', desc: '本次执行用，用完自动清除登录态' },
                ]
                const curKey = sel.mode === 'temp' ? 'temp' : `role:${sel.role}`
                const cur = opts.find((o) => o.key === curKey) || opts[0]
                const isOpen = openDD === p
                return (
                  <div key={p} style={{ marginBottom: 14 }}>
                    <div style={{ fontSize: 12.5, fontWeight: 600, color: '#64748B', marginBottom: 10 }}>
                      执行账号{webPlatforms.filter((x) => webAccounts[x]?.covered).length > 1 ? ` · ${p}` : ''}
                    </div>
                    <div onClick={() => setOpenDD(isOpen ? null : p)} style={{ height: 42, padding: '0 14px', border: `1.5px solid ${isOpen ? BRAND_BORDER : '#E7ECF0'}`, borderRadius: 10, display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer' }}>
                      <span style={{ width: 28, height: 28, flex: 'none', borderRadius: '50%', background: BRAND_SOFT, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                        <span className="ms" style={{ fontSize: 16, color: BRAND }}>{cur.icon}</span>
                      </span>
                      <div style={{ flex: 1, minWidth: 0 }}>
                        <div style={{ fontSize: 13, fontWeight: 500, color: '#0F172A', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{cur.name}</div>
                        <div style={{ fontSize: 11, color: '#94A3B8' }}>{cur.desc}</div>
                      </div>
                      <span className="ms" style={{ fontSize: 20, color: '#B0BAC4', transition: 'transform .2s', transform: isOpen ? 'rotate(180deg)' : 'none' }}>expand_more</span>
                    </div>
                    {isOpen && (
                      <>
                      <div onClick={() => setOpenDD(null)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
                      <div style={{ position: 'relative', zIndex: 50, marginTop: 4, border: '1px solid #E7ECF0', borderRadius: 11, overflow: 'hidden', boxShadow: '0 4px 20px -6px rgba(15,23,42,.12)', background: '#fff' }}>
                        {opts.map((o, idx) => {
                          const selected = o.key === curKey
                          return (
                            <div key={o.key} className="exec-opt" onClick={() => { if (o.key === 'temp') setSel({ mode: 'temp' }); else setSel({ mode: 'role', role: o.key.replace('role:', '') }); setOpenDD(null) }}
                              style={{ padding: '11px 14px', display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', borderBottom: idx < opts.length - 1 ? '1px solid #F7F9FB' : 'none' }}>
                              <span style={{ width: 28, height: 28, flex: 'none', borderRadius: '50%', background: selected ? BRAND_SOFT : '#F2F5F8', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                <span className="ms" style={{ fontSize: 16, color: selected ? BRAND : '#94A3B8' }}>{o.icon}</span>
                              </span>
                              <div style={{ flex: 1, minWidth: 0 }}>
                                <div style={{ fontSize: 13, fontWeight: 500, color: '#0F172A' }}>{o.name}</div>
                                <div style={{ fontSize: 11, color: '#94A3B8' }}>{o.desc}</div>
                              </div>
                              {selected && <span className="ms" style={{ fontSize: 18, color: BRAND }}>check</span>}
                            </div>
                          )
                        })}
                      </div>
                      </>
                    )}
                    {sel.mode === 'temp' && (
                      <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
                        <input className="exec-input" placeholder={isSms(p) ? '手机号' : '账号'} value={sel.username || ''} onChange={(e) => setSel({ username: e.target.value })}
                          style={{ flex: 1, minWidth: 140, height: 38, padding: '0 12px', border: '1.5px solid #E7ECF0', borderRadius: 9, fontSize: 13 }} />
                        {isSms(p) ? (
                          info?.tenant && (
                            <input className="exec-input" placeholder="租户名（选填）" value={sel.tenant_name || ''} onChange={(e) => setSel({ tenant_name: e.target.value })}
                              style={{ flex: 1, minWidth: 140, height: 38, padding: '0 12px', border: '1.5px solid #E7ECF0', borderRadius: 9, fontSize: 13 }} />
                          )
                        ) : (
                          <input className="exec-input" type="password" placeholder="密码" value={sel.password || ''} onChange={(e) => setSel({ password: e.target.value })}
                            style={{ flex: 1, minWidth: 140, height: 38, padding: '0 12px', border: '1.5px solid #E7ECF0', borderRadius: 9, fontSize: 13 }} />
                        )}
                        {isSms(p) && <div style={{ width: '100%', fontSize: 11.5, color: '#94A3B8' }}>验证码由框架自动填充</div>}
                      </div>
                    )}
                  </div>
                )
              })}
              {webPlatforms.some((p) => webAccounts[p]?.covered) && (
                <div style={{ fontSize: 11.5, color: '#B0BAC4', marginTop: 7 }}>临时账号不写入自动化框架，本次批量执行结束后自动清除登录态。</div>
              )}
            </div>
          )}

          {/* 接口 */}
          {apiCases.length > 0 && (
            <div style={{ marginBottom: mobileCases.length ? 18 : 0 }}>
              <div style={{ background: BRAND_SOFT, border: `1px solid ${BRAND_BORDER}`, borderRadius: 11, padding: '13px 15px', display: 'flex', gap: 11, marginBottom: 14 }}>
                <span className="ms" style={{ fontSize: 18, color: BRAND, marginTop: 1 }}>info</span>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: BRAND }}>接口测试将直接调用代码仓库中的 API</div>
                  <div style={{ fontSize: 12, color: '#7A6050', lineHeight: 1.7 }}>连接本需求关联的代码仓库，读取并执行接口测试脚本，对真实 API 端点验证。</div>
                </div>
              </div>
              <div style={{ fontSize: 12.5, fontWeight: 600, color: '#64748B', marginBottom: 8 }}>代码仓库地址 / API 基础 URL</div>
              <input className="exec-input" placeholder="如 https://github.com/org/repo 或 http://localhost:8000" value={execApiBaseUrl} onChange={(e) => setExecApiBaseUrl(e.target.value)}
                style={{ width: '100%', height: 40, padding: '0 13px', border: '1.5px solid #E7ECF0', borderRadius: 10, fontSize: 13 }} />
            </div>
          )}

          {/* 移动端：选目标真机 */}
          {mobileCases.length > 0 && (
            <div>
              {devChecking && devices.length === 0 ? (
                <div style={{ fontSize: 13, color: '#64748B' }}>正在获取在线真机…</div>
              ) : hasMyDevice ? (
                /* 本地已连自己的真机 → 直接选目标设备(默认我的，可切公共) */
                <div>
                  <div style={{ background: '#F0FBF4', border: '1px solid #B7E4C7', borderRadius: 11, padding: '13px 15px', display: 'flex', gap: 11, marginBottom: 12 }}>
                    <span className="ms" style={{ fontSize: 18, color: '#128A43', marginTop: 1 }}>check_circle</span>
                    <div>
                      <div style={{ fontSize: 13, fontWeight: 600, color: '#128A43' }}>已连接你的真机</div>
                      <div style={{ fontSize: 12, color: '#3F7A56', lineHeight: 1.7 }}>App 用例将派发到所选真机的执行机本地执行（AI 视觉直连，无需 Appium）。</div>
                    </div>
                  </div>
                  <div style={{ fontSize: 12.5, fontWeight: 600, color: '#64748B', marginBottom: 8 }}>目标设备</div>
                  <Select style={{ width: '100%' }} dropdownStyle={{ zIndex: 1300 }} value={selectedDevice} onChange={setSelectedDevice}
                    options={[
                      ...devices
                        .filter((d) => (myUid && d.owner_user_id === myUid) || d.is_public)
                        .map((d) => ({
                          value: d.serial,
                          label: (myUid && d.owner_user_id === myUid) ? `我的设备 · ${d.model}` : `公共设备 · ${d.model}${d.worker_name ? '（' + d.worker_name + '）' : ''}`,
                        })),
                      ...sonicDevices.map((d) => ({
                        value: d.serial,
                        label: `远程真机 · ${d.model}${d.busy ? '（占用中）' : ''}`,
                      })),
                    ]} />
                  <div style={{ fontSize: 11.5, color: '#B0BAC4', marginTop: 6 }}>默认用你自己执行机连的真机；也可选公共/远程真机(Sonic)，被占用时任务会排队。</div>
                </div>
              ) : (
                /* 本地无自己的真机 → 二选一：连接真机 / 使用公共设备 */
                <div>
                  <div style={{ background: '#FFFBF0', border: '1px solid #F9E2A0', borderRadius: 11, padding: '11px 14px', display: 'flex', gap: 10, marginBottom: 12 }}>
                    <span className="ms" style={{ fontSize: 17, color: '#E8930C', marginTop: 1 }}>info</span>
                    <div style={{ fontSize: 12.5, color: '#92400E', lineHeight: 1.7 }}>未检测到你本地连接的真机。请选择执行方式：</div>
                  </div>
                  <div style={{ display: 'flex', gap: 10 }}>
                    {/* 选项一：连接自己的真机 */}
                    <div onClick={() => setMobileMode('connect')}
                      style={{ flex: 1, cursor: 'pointer', border: `1.5px solid ${mobileMode === 'connect' ? BRAND : '#E7ECF0'}`, background: mobileMode === 'connect' ? '#FFF7F3' : '#fff', borderRadius: 11, padding: '13px 14px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                        <span className="ms" style={{ fontSize: 18, color: BRAND }}>usb</span>
                        <span style={{ fontSize: 13, fontWeight: 700, color: '#0F172A' }}>连接真机</span>
                      </div>
                      <div style={{ fontSize: 11.5, color: '#7A8290', lineHeight: 1.6 }}>用自己插真机的电脑运行执行助手，独占执行、无需排队。</div>
                    </div>
                    {/* 选项二：远程真机(Sonic) */}
                    <div onClick={() => sonicDevices.length && setMobileMode('remote')}
                      style={{ flex: 1, cursor: sonicDevices.length ? 'pointer' : 'not-allowed', opacity: sonicDevices.length ? 1 : 0.55, border: `1.5px solid ${mobileMode === 'remote' ? BRAND : '#E7ECF0'}`, background: mobileMode === 'remote' ? '#FFF7F3' : '#fff', borderRadius: 11, padding: '13px 14px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                        <span className="ms" style={{ fontSize: 18, color: BRAND }}>cloud</span>
                        <span style={{ fontSize: 13, fontWeight: 700, color: '#0F172A' }}>远程真机</span>
                      </div>
                      <div style={{ fontSize: 11.5, color: '#7A8290', lineHeight: 1.6 }}>
                        {sonicDevices.length ? `Sonic 云真机 ${sonicDevices.length} 台可选，无需本地设备。` : '暂无在线远程真机。'}
                      </div>
                    </div>
                    {/* 选项三：使用公共设备 */}
                    <div onClick={() => publicDevices.length && setMobileMode('public')}
                      style={{ flex: 1, cursor: publicDevices.length ? 'pointer' : 'not-allowed', opacity: publicDevices.length ? 1 : 0.55, border: `1.5px solid ${mobileMode === 'public' ? BRAND : '#E7ECF0'}`, background: mobileMode === 'public' ? '#FFF7F3' : '#fff', borderRadius: 11, padding: '13px 14px' }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                        <span className="ms" style={{ fontSize: 18, color: BRAND }}>devices</span>
                        <span style={{ fontSize: 13, fontWeight: 700, color: '#0F172A' }}>使用公共设备</span>
                      </div>
                      <div style={{ fontSize: 11.5, color: '#7A8290', lineHeight: 1.6 }}>
                        {publicDevices.length ? `公共测试机 ${publicDevices[0].model}，多人共用需排队。` : '暂无在线公共设备。'}
                      </div>
                    </div>
                  </div>

                  {/* 选「远程真机」→ 选一台 Sonic 设备 */}
                  {mobileMode === 'remote' && sonicDevices.length > 0 && (
                    <div style={{ marginTop: 12 }}>
                      <div style={{ fontSize: 12.5, fontWeight: 600, color: '#64748B', marginBottom: 8 }}>远程真机(Sonic)</div>
                      <Select style={{ width: '100%' }} dropdownStyle={{ zIndex: 1300 }} value={selectedDevice} onChange={setSelectedDevice}
                        options={sonicDevices.map((d) => ({ value: d.serial, label: `${d.model}${d.busy ? '（占用中）' : ''}` }))} />
                      <div style={{ fontSize: 11.5, color: '#B0BAC4', marginTop: 6 }}>执行时平台会自动占用该设备、用完释放；被占用时会排队。</div>
                    </div>
                  )}

                  {/* 选「连接真机」→ 引导下载助手 */}
                  {mobileMode === 'connect' && (
                    <div style={{ marginTop: 12, background: '#F8FAFC', border: '1px solid #E7ECF0', borderRadius: 11, padding: '13px 15px' }}>
                      <div style={{ fontSize: 12.5, color: '#475569', lineHeight: 1.8, marginBottom: 8 }}>
                        在插真机的电脑上下载运行「真机执行助手」(含 adb，免装环境)，手机连上后会自动上线，回到这里即可选到你的设备。
                      </div>
                      <Space>
                        <Button type="primary" size="small" onClick={() => { setGuideOpen(true); workerApi.installInfo(osChoice).then((r) => { setToken(r.data.worker_token); setMyUid(r.data.owner_user_id) }).catch(() => {}) }}>连接我的真机</Button>
                        <Button size="small" loading={devChecking} onClick={loadDevices}>刷新设备</Button>
                      </Space>
                      <div style={{ fontSize: 11.5, color: '#B0BAC4', marginTop: 6 }}>助手起来后会自动检测(每几秒刷新一次)。</div>
                    </div>
                  )}

                  {/* 选「使用公共设备」→ 排队提醒 */}
                  {mobileMode === 'public' && publicDevices.length > 0 && (
                    <div style={{ marginTop: 12, background: appQueue > 0 ? '#FFFBF0' : '#F0FBF4', border: `1px solid ${appQueue > 0 ? '#F9E2A0' : '#B7E4C7'}`, borderRadius: 11, padding: '13px 15px', display: 'flex', gap: 11 }}>
                      <span className="ms" style={{ fontSize: 18, color: appQueue > 0 ? '#E8930C' : '#128A43', marginTop: 1 }}>{appQueue > 0 ? 'schedule' : 'check_circle'}</span>
                      <div style={{ fontSize: 12.5, lineHeight: 1.75 }}>
                        {appQueue > 0 ? (
                          <>
                            <span style={{ fontWeight: 600, color: '#A16207' }}>公共设备当前有 {appQueue} 个 App 用例待执行/执行中</span>
                            <div style={{ color: '#92400E' }}>你的 {mobileCases.length} 条 App 用例将排队按序执行，等待时间较长。若急用建议改用「连接真机」独占执行。</div>
                          </>
                        ) : (
                          <>
                            <span style={{ fontWeight: 600, color: '#128A43' }}>公共设备空闲，可立即执行</span>
                            <div style={{ color: '#3F7A56' }}>你的 {mobileCases.length} 条 App 用例将派发到公共测试机执行。</div>
                          </>
                        )}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* 选完设备后：是否更换测试包。换包则按 app 选包版本(下拉数据源待接真实接口，现返回测试项) */}
              {selectedDevice && appPlatforms.length > 0 && (
                <div style={{ marginTop: 14, paddingTop: 14, borderTop: '1px dashed #E7ECF0' }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', userSelect: 'none' }}>
                    <input type="checkbox" checked={changePkg} onChange={(e) => setChangePkg(e.target.checked)} style={{ width: 15, height: 15, accentColor: BRAND }} />
                    <span style={{ fontSize: 13, fontWeight: 600, color: '#0F172A' }}>执行前更换测试包</span>
                    <span style={{ fontSize: 11.5, color: '#94A3B8' }}>下载新 apk 推到设备，先卸旧包再装新包</span>
                  </label>
                  {changePkg && (
                    <div style={{ marginTop: 10, display: 'flex', flexDirection: 'column', gap: 10 }}>
                      {appPlatforms.map((app) => {
                        const opts = pkgOptions[app] || []
                        return (
                          <div key={app} style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                            <span style={{ ...platformTagStyle(app), margin: 0, fontSize: 12, borderRadius: 7, padding: '2px 8px', whiteSpace: 'nowrap' }}>{app}</span>
                            <Select style={{ flex: 1 }} size="middle" placeholder={opts.length ? '选择包版本' : '暂无可选包（接口待接入）'}
                              value={pkgSel[app]} onChange={(v) => setPkgSel((prev) => ({ ...prev, [app]: v }))}
                              options={opts.map((o) => ({ value: o.id, label: o.label }))}
                              notFoundContent="暂无可选包（包版本查询接口待接入）"
                              dropdownStyle={{ zIndex: 1300 }} />
                          </div>
                        )
                      })}
                      <div style={{ fontSize: 11.5, color: '#B0BAC4' }}>不选包版本的 app 维持原样不换包；无论本地/远程/公共设备都会卸旧装新。</div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'flex-end', gap: 10, padding: '14px 24px 20px', borderTop: '1px solid #F1F4F6' }}>
          {automatedCases.length > 0 && (
            <button onClick={() => setView('choose')} style={{ height: 38, padding: '0 16px', background: '#fff', border: '1px solid #E7ECF0', borderRadius: 10, fontSize: 13.5, color: '#64748B', cursor: 'pointer', marginRight: 'auto' }}>返回</button>
          )}
          <button onClick={onCancel} style={{ height: 38, padding: '0 20px', background: '#fff', border: '1px solid #E7ECF0', borderRadius: 10, fontSize: 13.5, color: '#64748B', cursor: 'pointer' }}>取消</button>
          {executableCount > 0 && (
            <button disabled={inExec} onClick={() => !inExec && onConfirm('fresh', buildOverrides(), selectedDevice ?? null, execEnv, buildPackageOverrides())}
              style={{ height: 38, padding: '0 20px', borderRadius: 10, fontSize: 13.5, fontWeight: 600, border: 'none', cursor: inExec ? 'not-allowed' : 'pointer', background: inExec ? '#E7ECF0' : BRAND_GRAD, color: inExec ? '#94A3B8' : '#fff', boxShadow: inExec ? 'none' : '0 4px 14px -5px rgba(217,119,87,.5)' }}>
              {execLabel}
            </button>
          )}
        </div>
      </div>
    </div>

    <Modal title="连接我的真机" open={guideOpen} onCancel={() => setGuideOpen(false)} footer={null} width={560} zIndex={1200}>
      <div style={{ fontSize: 13, color: '#475569', lineHeight: 1.9 }}>
        {/* 执行机系统切换：浏览的机器未必是插手机的执行机，允许手动选 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12 }}>
          <span style={{ color: '#64748B' }}>执行机系统：</span>
          {(['win', 'mac'] as const).map((o) => (
            <Button key={o} size="small" type={osChoice === o ? 'primary' : 'default'} onClick={() => setOsChoice(o)}>
              {o === 'win' ? 'Windows' : 'Mac'}
            </Button>
          ))}
        </div>
        <p style={{ marginTop: 0 }}>在<b>插着安卓真机的{osChoice === 'mac' ? 'Mac' : '电脑'}</b>上按 3 步即可上线(无需装 Python/adb)：</p>
        <p><b>① 下载执行助手</b>(含 Python+依赖+adb)</p>
        <Button type="primary" href={workerApi.downloadUrl(osChoice)} target="_blank" style={{ marginBottom: 12 }}>
          {osChoice === 'mac' ? '下载 tp-worker（Mac）' : '下载 tp-worker.exe'}
        </Button>
        <p><b>② 首次启动(只需一次)</b>：在下载到的{osChoice === 'mac' ? '文件所在目录打开「终端」' : ' exe 所在目录打开 PowerShell'}，粘贴运行下面命令(已带好平台地址+令牌+你的归属)。<b>这条命令本身就会启动助手，不用再双击。</b></p>
        <div style={{ position: 'relative', background: '#0F172A', color: '#E2E8F0', borderRadius: 8, padding: '10px 12px', fontFamily: MONO_FONT, fontSize: 12, wordBreak: 'break-all', marginBottom: 6 }}>
          {runCmd}
          <Button size="small" style={{ position: 'absolute', top: 6, right: 6 }} onClick={() => copyText(runCmd)}>复制</Button>
        </div>
        {osChoice === 'mac' && (
          <p style={{ color: '#B0BAC4', fontSize: 11.5 }}>首次运行若被 Gatekeeper 拦截(“无法验证开发者”)，到「系统设置 → 隐私与安全性」点“仍要打开”即可。</p>
        )}
        <p style={{ color: '#16A34A', fontSize: 12 }}>首次运行后会自动记住配置 + <b>设为开机自启</b> —— 以后<b>开机自动上线，无需再操作</b>(也不必常开窗口；想停可{osChoice === 'mac' ? '在活动监视器结束 tp-worker' : '在任务管理器结束 tp-worker'})。</p>
        <p><b>③ 手机</b>用 USB 连{osChoice === 'mac' ? 'Mac' : '电脑'},开「USB 调试」并在手机上点「允许」,保持连接。</p>
        <p style={{ color: '#16A34A' }}>助手起来后会自动上报设备,回到执行弹框刷新即可在「目标设备」里默认选到你的设备。</p>
      </div>
    </Modal>
    </>
  )
}
