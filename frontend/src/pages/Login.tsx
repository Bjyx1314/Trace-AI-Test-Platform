import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { message } from 'antd'
import { authApi } from '../api'
import { useAuthStore } from '../store/authStore'

const BRAND_GRAD = 'linear-gradient(140deg,#F0A070,#D97350)'

function FeatureTag({ icon, color, text }: { icon: string; color: string; text: string }) {
  return (
    <span style={{
      display: 'inline-flex', alignItems: 'center', gap: 6, padding: '6px 12px', borderRadius: 999,
      background: 'rgba(255,255,255,.07)', border: '1px solid rgba(255,255,255,.1)',
      fontSize: 12, color: 'rgba(255,255,255,.7)',
    }}>
      <span className="ms" style={{ fontSize: 15, color }}>{icon}</span>{text}
    </span>
  )
}

function Field({ icon, rightIcon, onRightClick, ...props }: any) {
  return (
    <div style={{ position: 'relative', marginBottom: 16 }}>
      <span className="ms" style={{ position: 'absolute', left: 12, top: '50%', transform: 'translateY(-50%)', fontSize: 18, color: '#B0BAC4', pointerEvents: 'none' }}>{icon}</span>
      <input
        {...props}
        style={{
          width: '100%', height: 44, padding: rightIcon ? '0 40px 0 40px' : '0 12px 0 40px',
          border: '1.5px solid #E7ECF0', borderRadius: 10, fontSize: 14, color: '#0F172A', background: '#fff', outline: 'none', transition: 'all .2s',
        }}
        onFocus={(e) => { e.currentTarget.style.borderColor = '#D97757'; e.currentTarget.style.boxShadow = '0 0 0 3px rgba(217,119,87,.12)' }}
        onBlur={(e) => { e.currentTarget.style.borderColor = '#E7ECF0'; e.currentTarget.style.boxShadow = 'none' }}
      />
      {rightIcon && (
        <span className="ms" onClick={onRightClick} style={{ position: 'absolute', right: 12, top: '50%', transform: 'translateY(-50%)', fontSize: 18, color: '#CBD5E1', cursor: 'pointer' }}>{rightIcon}</span>
      )}
    </div>
  )
}

export default function Login() {
  const navigate = useNavigate()
  const { setUser } = useAuthStore()
  const [tab, setTab] = useState<'password' | 'ldap'>('password')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [showPwd, setShowPwd] = useState(false)
  const [remember, setRemember] = useState(true)
  const [loading, setLoading] = useState(false)

  // 已登录（含外部 SSO 换取的 JWT）直接进首页。
  useEffect(() => {
    if (localStorage.getItem('platform_jwt')) navigate('/', { replace: true })
  }, [])

  const handleLogin = async () => {
    if (!username.trim() || !password) { message.warning('请输入账号和密码'); return }
    setLoading(true)
    try {
      const r = await authApi.login(username.trim(), password)
      localStorage.setItem('platform_jwt', r.data.jwt)
      localStorage.setItem('platform_user', JSON.stringify(r.data.user))
      setUser(r.data.user)
      message.success('登录成功')
      navigate('/', { replace: true })
    } catch (err: any) {
      message.error(err?.response?.data?.detail || '登录失败，请重试')
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={{ display: 'flex', height: '100vh', overflow: 'hidden', background: '#F7F9FB' }}>
      {/* 左侧品牌区 */}
      <div style={{ width: 480, flex: 'none', position: 'relative', overflow: 'hidden', padding: '48px 44px', display: 'flex', flexDirection: 'column', background: 'linear-gradient(155deg,#1a1a2e 0%,#16213e 50%,#0f3460 100%)' }}>
        {/* 装饰层 */}
        <div style={{ position: 'absolute', inset: 0, pointerEvents: 'none', backgroundImage: 'radial-gradient(rgba(255,255,255,.06) 1px, transparent 1px)', backgroundSize: '28px 28px' }} />
        <div style={{ position: 'absolute', top: -80, right: -80, width: 360, height: 360, pointerEvents: 'none', background: 'radial-gradient(rgba(217,119,87,.22), transparent 70%)' }} />
        <div style={{ position: 'absolute', bottom: -60, left: -60, width: 240, height: 240, pointerEvents: 'none', background: 'radial-gradient(rgba(123,184,232,.14), transparent 70%)' }} />

        {/* Logo */}
        <div style={{ position: 'relative', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 40, height: 40, borderRadius: 12, background: BRAND_GRAD, boxShadow: '0 6px 18px -6px rgba(201,107,68,.5)', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
            <span className="ms" style={{ fontSize: 22, color: '#fff' }}>hub</span>
          </div>
          <div>
            <div style={{ fontSize: 16, fontWeight: 700, color: '#fff', letterSpacing: '.3px' }}>Trace<span style={{ color: '#D97757' }}>AI</span></div>
            <div style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 10, color: 'rgba(255,255,255,.4)', letterSpacing: 1 }}>TEST PLATFORM</div>
          </div>
        </div>

        {/* 主文案 */}
        <div style={{ position: 'relative', marginTop: 'auto', marginBottom: 'auto' }}>
          <div style={{ width: 40, height: 3, background: 'linear-gradient(90deg,#D97757,transparent)', borderRadius: 999, marginBottom: 20 }} />
          <div style={{ fontSize: 30, fontWeight: 700, color: '#fff', lineHeight: 1.3, letterSpacing: '-.5px' }}>
            AI 驱动的<span style={{ color: '#D97757' }}>智能测试</span><br />全流程平台
          </div>
          <div style={{ fontSize: 14, color: 'rgba(255,255,255,.5)', lineHeight: 1.8, marginTop: 14 }}>
            需求分析 · 用例生成 · 自动执行 · 缺陷复核<br />让测试更快、更准、更省心
          </div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginTop: 28 }}>
            <FeatureTag icon="auto_fix_high" color="#D97757" text="AI 生成用例" />
            <FeatureTag icon="travel_explore" color="#7BB8E8" text="自动探索" />
            <FeatureTag icon="verified" color="#7ECBAF" text="质量门禁" />
          </div>
        </div>

        <div style={{ position: 'relative', fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: 'rgba(255,255,255,.25)' }}>v2.3.0 · 2026</div>
      </div>

      {/* 右侧登录区 */}
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', padding: 40, overflowY: 'auto' }}>
        <div style={{ width: '100%', maxWidth: 400, animation: 'fadeUp .4s ease both' }}>
          <div style={{ marginBottom: 32 }}>
            <div style={{ fontSize: 24, fontWeight: 700, color: '#0F172A' }}>欢迎回来</div>
            <div style={{ fontSize: 14, color: '#94A3B8', marginTop: 6 }}>登录 Trace AI 开始今天的测试工作</div>
          </div>

          {/* 分段选择器 */}
          <div style={{ display: 'flex', background: '#F1F4F6', borderRadius: 11, padding: 3, gap: 2, marginBottom: 28 }}>
            {([['password', '账号密码'], ['ldap', '域账号']] as const).map(([k, label]) => {
              const on = tab === k
              return (
                <div key={k} onClick={() => setTab(k)} style={{
                  flex: 1, padding: '9px 0', borderRadius: 8, fontSize: 13.5, fontWeight: 600, textAlign: 'center', cursor: 'pointer',
                  background: on ? '#fff' : 'transparent', color: on ? '#0F172A' : '#94A3B8',
                  boxShadow: on ? '0 1px 4px rgba(16,24,40,.1)' : 'none', transition: 'all .15s',
                }}>{label}</div>
              )
            })}
          </div>

          {tab === 'password' ? (
            <>
              <Field icon="person" placeholder="请输入账号" value={username} onChange={(e: any) => setUsername(e.target.value)} onKeyDown={(e: any) => e.key === 'Enter' && handleLogin()} />
              <Field icon="lock" type={showPwd ? 'text' : 'password'} placeholder="请输入密码" value={password}
                rightIcon={showPwd ? 'visibility' : 'visibility_off'} onRightClick={() => setShowPwd(!showPwd)}
                onChange={(e: any) => setPassword(e.target.value)} onKeyDown={(e: any) => e.key === 'Enter' && handleLogin()} />
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 4, marginBottom: 22 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 12.5, color: '#64748B' }}>
                  <span onClick={() => setRemember(!remember)} className="ms" style={{ fontSize: 18, color: '#C96B44', fontVariationSettings: remember ? "'FILL' 1" : "'FILL' 0" }}>{remember ? 'check_box' : 'check_box_outline_blank'}</span>
                  记住登录
                </label>
                <span style={{ fontSize: 12.5, color: '#C96B44', cursor: 'pointer' }}>忘记密码？</span>
              </div>
              <button onClick={handleLogin} disabled={loading} style={{
                width: '100%', height: 46, background: BRAND_GRAD, color: '#fff', border: 'none', borderRadius: 11,
                fontSize: 15, fontWeight: 600, boxShadow: '0 6px 18px -6px rgba(201,107,68,.45)', cursor: loading ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 8, opacity: loading ? 0.85 : 1,
              }}>
                <span className="ms" style={{ fontSize: 18 }}>login</span>{loading ? '登录中…' : '登录'}
              </button>
            </>
          ) : (
            <div style={{ background: '#EDF7F2', border: '1px solid #B0DCC4', borderRadius: 10, padding: '13px 16px', display: 'flex', gap: 10 }}>
              <span className="ms" style={{ fontSize: 18, color: '#1F7A5A' }}>info</span>
              <div style={{ fontSize: 12.5, color: '#1F7A5A', lineHeight: 1.7 }}>域账号(LDAP)登录暂未启用，请使用「账号密码」登录，或联系管理员开通。</div>
            </div>
          )}

          <div style={{ marginTop: 28, paddingTop: 20, borderTop: '1px solid #F1F4F6', textAlign: 'center', fontSize: 12, color: '#CBD5E1' }}>
            <span style={{ color: '#B0BAC4', cursor: 'pointer' }}>服务协议</span> · <span style={{ color: '#B0BAC4', cursor: 'pointer' }}>隐私政策</span>
          </div>
        </div>
      </div>
    </div>
  )
}
