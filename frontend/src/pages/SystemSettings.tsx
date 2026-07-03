import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { message, Table, Tag, Alert } from 'antd'
import { systemApi, type AutomationSwitch, type AiConfig } from '../api'
import { useAuthStore } from '../store/authStore'

const BRAND = '#D97757', BRAND_SOFT = '#FBEEE6', BRAND_TEXT = '#C25E3F'
const BRAND_GRAD = 'linear-gradient(135deg,#E8916B 0%,#D97757 100%)'
const MONO = "'JetBrains Mono','SFMono-Regular',Consolas,monospace"

const CARD: CSSProperties = {
  background: '#fff', border: '1px solid #ECEFF2', borderRadius: 16,
  boxShadow: '0 1px 3px rgba(16,24,40,.05)', overflow: 'hidden',
}
const LABEL: CSSProperties = { fontSize: 12, fontWeight: 600, color: '#475569', marginBottom: 8 }
const FIELD: CSSProperties = {
  width: '100%', height: 40, padding: '0 14px', border: '1.5px solid #E7ECF0', borderRadius: 10,
  background: '#FAFBFC', fontSize: 12.5, color: '#334155', fontFamily: MONO, outline: 'none',
}
function SectionHeader({ icon, iconBg, iconColor, title, desc }: { icon: string; iconBg: string; iconColor: string; title: string; desc: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 14, padding: '20px 24px 16px', borderBottom: '1px solid #F1F4F6' }}>
      <div style={{ width: 38, height: 38, flex: 'none', borderRadius: 11, background: iconBg, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span className="ms" style={{ fontSize: 22, color: iconColor }}>{icon}</span>
      </div>
      <div>
        <div style={{ fontSize: 14.5, fontWeight: 700, color: '#0F172A' }}>{title}</div>
        <div style={{ fontSize: 11.5, color: '#94A3B8', marginTop: 2 }}>{desc}</div>
      </div>
    </div>
  )
}
// 自定义开关 44×25
function Toggle({ on, loading, onClick }: { on: boolean; loading?: boolean; onClick: () => void }) {
  return (
    <div onClick={() => !loading && onClick()} style={{ display: 'inline-flex', alignItems: 'center', gap: 9, cursor: loading ? 'wait' : 'pointer', opacity: loading ? 0.6 : 1 }}>
      <div style={{
        width: 44, height: 25, borderRadius: 999, position: 'relative', transition: 'all .2s',
        background: on ? BRAND : '#E2E8F0', boxShadow: on ? '0 2px 6px -2px rgba(217,119,87,.45)' : 'none',
      }}>
        <div style={{ position: 'absolute', top: 4.5, left: on ? 23 : 5, width: 16, height: 16, borderRadius: '50%', background: '#fff', boxShadow: '0 1px 3px rgba(0,0,0,.15)', transition: 'all .2s' }} />
      </div>
      <span style={{ fontSize: 11.5, fontWeight: 600, color: on ? BRAND_TEXT : '#94A3B8' }}>{on ? '已开启' : '已关闭'}</span>
    </div>
  )
}

export default function SystemSettings() {
  const { isAdmin } = useAuthStore()
  const [data, setData] = useState<AutomationSwitch[]>([])
  const [loading, setLoading] = useState(false)
  const [savingPlatform, setSavingPlatform] = useState<string | null>(null)

  const [ssoUrl, setSsoUrl] = useState('')
  const [ssoResolved, setSsoResolved] = useState('')
  const [ssoDefault, setSsoDefault] = useState('')
  const [ssoSaving, setSsoSaving] = useState(false)

  const [ai, setAi] = useState<AiConfig | null>(null)
  const [aiForm, setAiForm] = useState({ provider: '', model: '', base_url: '', api_key: '' })
  const [aiSaving, setAiSaving] = useState(false)
  const [provOpen, setProvOpen] = useState(false)
  const [showKey, setShowKey] = useState(false)

  const load = () => {
    setLoading(true)
    systemApi.automationSwitches().then((r) => setData(r.data))
      .catch((err) => message.error(err?.response?.data?.detail || '加载失败'))
      .finally(() => setLoading(false))
  }
  const loadSso = () => {
    systemApi.getSsoConfig()
      .then((r) => { setSsoUrl(r.data.external_sso_url); setSsoResolved(r.data.resolved); setSsoDefault(r.data.default) })
      .catch((err) => message.error(err?.response?.data?.detail || 'SSO 配置加载失败'))
  }
  const loadAi = () => {
    systemApi.getAiConfig()
      .then((r) => { setAi(r.data); setAiForm({ provider: r.data.provider, model: r.data.model, base_url: r.data.base_url, api_key: '' }) })
      .catch((err) => message.error(err?.response?.data?.detail || 'AI 配置加载失败'))
  }

  useEffect(() => { if (isAdmin) { load(); loadSso(); loadAi() } }, [isAdmin])

  const saveAi = async () => {
    setAiSaving(true)
    try {
      const r = await systemApi.setAiConfig({
        provider: aiForm.provider, model: aiForm.model.trim(), base_url: aiForm.base_url.trim(),
        ...(aiForm.api_key.trim() ? { api_key: aiForm.api_key.trim() } : {}),
      })
      setAi(r.data); setAiForm({ provider: r.data.provider, model: r.data.model, base_url: r.data.base_url, api_key: '' })
      message.success('AI 模型配置已保存')
    } catch (err: any) { message.error(err?.response?.data?.detail || '保存失败') } finally { setAiSaving(false) }
  }
  const saveSso = async () => {
    setSsoSaving(true)
    try {
      const r = await systemApi.setSsoConfig(ssoUrl.trim())
      setSsoUrl(r.data.external_sso_url); setSsoResolved(r.data.resolved); setSsoDefault(r.data.default)
      message.success('SSO 对接认证地址已保存')
    } catch (err: any) { message.error(err?.response?.data?.detail || '保存失败') } finally { setSsoSaving(false) }
  }
  const handleToggle = async (platform: string, enabled: boolean) => {
    setSavingPlatform(platform)
    try {
      const r = await systemApi.setAutomationSwitch(platform, enabled)
      setData((prev) => prev.map((x) => (x.platform === platform ? r.data : x)))
      message.success(`${r.data.label} 自动化生成已${enabled ? '开启' : '关闭'}`)
    } catch (err: any) { message.error(err?.response?.data?.detail || '更新失败') } finally { setSavingPlatform(null) }
  }

  if (!isAdmin) {
    return <div style={{ padding: 24 }}><Alert type="warning" showIcon message="无权限" description="该页面仅管理员可访问。" /></div>
  }

  const needsKey = aiForm.provider !== 'claude_cli'
  const providerLabel = ai?.providers.find((p) => p.value === aiForm.provider)?.label || aiForm.provider || '请选择 provider'
  const effective = ai?.model
    ? `${ai.provider} / ${ai.model}${ai.base_url ? ' / ' + ai.base_url : ''}`
    : '未配置模型'

  const saveBtn = (onClick: () => void, saving: boolean, h = 38, disabled = false) => (
    <button onClick={onClick} disabled={saving || disabled} style={{
      height: h, padding: '0 20px', background: disabled ? '#E7ECF0' : BRAND_GRAD, color: disabled ? '#94A3B8' : '#fff',
      border: 'none', borderRadius: 10, fontSize: 12.5, fontWeight: 600, cursor: saving || disabled ? 'not-allowed' : 'pointer',
      whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 5, boxShadow: disabled ? 'none' : '0 4px 12px -5px rgba(217,119,87,.45)',
    }}>
      <span className="ms" style={{ fontSize: 16 }}>save</span>{saving ? '保存中…' : '保存'}
    </button>
  )

  const columns = [
    {
      title: '端', dataIndex: 'label', key: 'label',
      render: (v: string, row: AutomationSwitch) => (
        <span style={{ fontSize: 12.5, fontWeight: 500, color: '#1E293B' }}>
          {v} <Tag style={{ marginLeft: 4 }}>{row.platform}</Tag>
        </span>
      ),
    },
    {
      title: '执行通过后生成自动化用例', key: 'enabled', width: 260,
      render: (_: any, row: AutomationSwitch) => (
        <Toggle on={row.enabled} loading={savingPlatform === row.platform} onClick={() => handleToggle(row.platform, !row.enabled)} />
      ),
    },
    {
      title: '最近修改人', dataIndex: 'updated_by', key: 'updated_by', width: 120,
      render: (v: string | null) => v ? (
        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 8 }}>
          <span style={{ width: 24, height: 24, borderRadius: '50%', background: BRAND_SOFT, color: BRAND_TEXT, fontSize: 11, fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>{String(v).slice(0, 1).toUpperCase()}</span>
          <span style={{ fontSize: 12.5 }}>{v}</span>
        </span>
      ) : <span style={{ color: '#CBD5E1' }}>—</span>,
    },
    {
      title: '修改时间', dataIndex: 'updated_at', key: 'updated_at', width: 180,
      render: (v: string | null) => <span style={{ fontFamily: MONO, fontSize: 11.5, color: '#94A3B8' }}>{v ? new Date(v).toLocaleString('zh-CN') : '—'}</span>,
    },
  ]

  return (
    <div style={{ padding: '24px 28px 40px', display: 'flex', flexDirection: 'column', gap: 20 }}>
      <style>{`.sys-input:focus{border-color:${BRAND}!important;box-shadow:0 0 0 3px rgba(217,119,87,.1)}`}</style>

      {/* Section 1 — AI 模型配置 */}
      <div style={CARD}>
        <SectionHeader icon="smart_toy" iconBg={BRAND_SOFT} iconColor={BRAND} title="AI 模型配置"
          desc="配置生成/执行所用的大模型 provider、模型、中转地址与 API Key，保存后立即生效（覆盖 .env）" />
        <div style={{ padding: '22px 24px', display: 'flex', flexDirection: 'column', gap: 18, maxWidth: 600 }}>
          {/* Provider */}
          <div style={{ position: 'relative' }}>
            <div style={LABEL}>Provider</div>
            <div onClick={() => setProvOpen(!provOpen)} style={{
              ...FIELD, background: '#fff', fontFamily: 'inherit', color: '#0F172A',
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', cursor: 'pointer',
              border: `1.5px solid ${provOpen ? BRAND : '#E7ECF0'}`,
              boxShadow: provOpen ? '0 0 0 3px rgba(217,119,87,.18)' : 'none', transition: 'all .15s',
            }}>
              <span>{providerLabel}</span>
              <span className="ms" style={{ fontSize: 20, color: '#B0BAC4', transition: 'transform .2s', transform: provOpen ? 'rotate(180deg)' : 'none' }}>expand_more</span>
            </div>
            {provOpen && (
              <>
              {/* 点击空白处收起下拉 */}
              <div onClick={() => setProvOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
              <div style={{ position: 'absolute', top: 'calc(100% + 6px)', left: 0, right: 0, zIndex: 50, background: '#fff', border: '1.5px solid #E7ECF0', borderRadius: 12, boxShadow: '0 8px 24px -6px rgba(15,23,42,.14)', padding: 6 }}>
                {(ai?.providers || []).map((p) => {
                  const on = p.value === aiForm.provider
                  return (
                    <div key={p.value} onClick={() => { setAiForm({ ...aiForm, provider: p.value }); setProvOpen(false) }}
                      style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '9px 12px', borderRadius: 8, cursor: 'pointer', fontSize: 12.5, background: on ? BRAND_SOFT : 'transparent', color: on ? BRAND_TEXT : '#334155', fontWeight: on ? 700 : 400 }}
                      onMouseEnter={(e) => { if (!on) e.currentTarget.style.background = '#FDF5F1' }}
                      onMouseLeave={(e) => { if (!on) e.currentTarget.style.background = 'transparent' }}>
                      <span>{p.label}</span>
                      {on && <span className="ms" style={{ fontSize: 17, color: BRAND }}>check</span>}
                    </div>
                  )
                })}
              </div>
              </>
            )}
          </div>
          {/* 模型名 */}
          <div>
            <div style={LABEL}>模型名</div>
            <input className="sys-input" style={FIELD} value={aiForm.model} onChange={(e) => setAiForm({ ...aiForm, model: e.target.value })}
              placeholder="请输入模型标识（必填，无默认模型）" />
          </div>
          {/* Base URL */}
          <div>
            <div style={LABEL}>中转 / Base URL</div>
            <input className="sys-input" style={FIELD} value={aiForm.base_url} onChange={(e) => setAiForm({ ...aiForm, base_url: e.target.value })}
              placeholder="如 https://api.example.com/v1（使用官方地址时可留空）" />
          </div>
          {/* API Key */}
          {needsKey && (
            <div>
              <div style={LABEL}>API Key</div>
              <div style={{ ...FIELD, display: 'flex', alignItems: 'center', gap: 10, padding: '0 12px 0 14px' }}>
                <input className="sys-input" type={showKey ? 'text' : 'password'}
                  style={{ flex: 1, border: 'none', outline: 'none', background: 'transparent', fontFamily: MONO, fontSize: 12.5, color: '#334155', boxShadow: 'none' }}
                  value={aiForm.api_key} onChange={(e) => setAiForm({ ...aiForm, api_key: e.target.value })}
                  placeholder={ai?.api_key_set ? `已配置(${ai.api_key_masked})，留空保持不变` : '请输入 API Key'} />
                <span className="ms" onClick={() => setShowKey(!showKey)} style={{ fontSize: 18, color: '#CBD5E1', cursor: 'pointer' }}
                  onMouseEnter={(e) => ((e.target as HTMLElement).style.color = '#94A3B8')}
                  onMouseLeave={(e) => ((e.target as HTMLElement).style.color = '#CBD5E1')}>{showKey ? 'visibility' : 'visibility_off'}</span>
              </div>
            </div>
          )}
          {/* 保存行 */}
          <div style={{ display: 'flex', alignItems: 'center', gap: 14, flexWrap: 'nowrap' }}>
            {saveBtn(saveAi, aiSaving, 38, !aiForm.provider || !aiForm.model.trim())}
            <span style={{ fontSize: 11.5, color: '#94A3B8', whiteSpace: 'nowrap', display: 'inline-flex', alignItems: 'center', gap: 5 }}>
              <span className="ms" style={{ fontSize: 15, color: '#B5E0C8' }}>check_circle</span>
              当前生效：<span style={{ fontFamily: MONO, color: '#64748B' }}>{effective}</span>
            </span>
          </div>
        </div>
      </div>

      {/* Section 2 — 单点登录 SSO */}
      <div style={CARD}>
        <SectionHeader icon="verified_user" iconBg="#EDF7F2" iconColor="#1F8A5B" title="单点登录 SSO"
          desc="配置可选的外部 SSO 地址；留空时使用平台本地账号登录" />
        <div style={{ padding: '22px 24px', maxWidth: 600 }}>
          <div style={{ display: 'flex', gap: 10 }}>
            <input className="sys-input" style={{ ...FIELD, flex: 1 }} value={ssoUrl} onChange={(e) => setSsoUrl(e.target.value)}
              placeholder={ssoDefault || 'https://sso.example.com'} />
            {saveBtn(saveSso, ssoSaving, 42)}
          </div>
          <div style={{ marginTop: 10, fontSize: 11.5, color: '#94A3B8', display: 'inline-flex', alignItems: 'center', gap: 6, flexWrap: 'wrap' }}>
            <span className="ms" style={{ fontSize: 14, color: '#B0BAC4' }}>info</span>
            当前生效地址：
            <code style={{ fontFamily: MONO, color: '#64748B', background: '#F2F5F8', border: '1px solid #E7ECF0', padding: '1px 7px', borderRadius: 5 }}>{ssoResolved || '—'}</code>
            （留空回落默认/环境变量）
          </div>
        </div>
      </div>

      {/* Section 3 — 自动化用例生成开关 */}
      <div style={CARD}>
        <SectionHeader icon="toggle_on" iconBg="#FEF6E7" iconColor="#E8930C" title="自动化用例生成开关"
          desc="按端控制「执行测试通过后是否自动生成自动化用例」，关闭后该端执行通过不再生成脚本" />
        <div style={{ padding: '8px 8px 4px' }}>
          <Table rowKey="platform" dataSource={data} columns={columns} loading={loading} pagination={false} />
        </div>
      </div>
    </div>
  )
}
