import { useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { Popover } from 'antd'
import { useProjectStore } from '../store/projectStore'
import { useAuthStore } from '../store/authStore'
import { projectsApi, usersApi } from '../api'
import { TECH_GRADIENT } from '../styles/theme'

const SIDER_WIDTH = 236

type NavItem = { key: string; icon: string; label: string; adminOnly?: boolean; match?: (p: string) => boolean }
type NavGroup = { label: string; items: NavItem[] }

const NAV_GROUPS: NavGroup[] = [
  { label: '概览', items: [
    { key: '/', icon: 'space_dashboard', label: '质量看板', match: (p) => p === '/' },
  ]},
  { label: '测试管理', items: [
    { key: '/requirements', icon: 'assignment', label: '需求列表', match: (p) => p.startsWith('/requirements') },
    { key: '/testcases', icon: 'list_alt', label: '用例库', match: (p) => p.startsWith('/testcases') },
    { key: '/executions', icon: 'history', label: '执行历史' },
    { key: '/defects', icon: 'bug_report', label: '缺陷复核' },
  ]},
  { label: 'AI 执行', items: [
    { key: '/page-cache', icon: 'schema', label: '页面结构缓存' },
  ]},
  { label: '系统', items: [
    { key: '/frameworks', icon: 'deployed_code', label: '框架仓库', adminOnly: true },
    { key: '/enums', icon: 'label', label: '枚举管理', adminOnly: true },
    { key: '/users', icon: 'group', label: '用户管理', adminOnly: true },
    { key: '/system-settings', icon: 'tune', label: '系统设置', adminOnly: true },
  ]},
]

const TITLE_MAP: { test: (p: string) => boolean; title: string }[] = [
  { test: (p) => p === '/', title: '质量看板' },
  { test: (p) => p.startsWith('/requirements/'), title: '需求详情' },
  { test: (p) => p.startsWith('/requirements'), title: '需求列表' },
  { test: (p) => p.startsWith('/testcases/list'), title: '用例列表' },
  { test: (p) => p.startsWith('/testcases'), title: '用例库' },
  { test: (p) => p.startsWith('/executions'), title: '执行历史' },
  { test: (p) => p.startsWith('/defects'), title: '缺陷复核' },
  { test: (p) => p.startsWith('/page-cache'), title: '页面结构缓存' },
  { test: (p) => p.startsWith('/frameworks'), title: '框架仓库' },
  { test: (p) => p.startsWith('/enums'), title: '枚举管理' },
  { test: (p) => p.startsWith('/users'), title: '用户管理' },
  { test: (p) => p.startsWith('/system-settings'), title: '系统设置' },
]

export default function AppLayout({ children }: { children: ReactNode }) {
  const navigate = useNavigate()
  const location = useLocation()
  const { currentProject, projects, setCurrentProject, setProjects } = useProjectStore()
  const { user, isAdmin, logout } = useAuthStore()
  const path = location.pathname

  const handleLogout = () => {
    logout()
    navigate('/login', { replace: true })
  }

  const [missingKeys, setMissingKeys] = useState<any[]>([])

  useEffect(() => {
    projectsApi.list().then((r) => {
      setProjects(r.data)
      if (!currentProject && r.data.length > 0) setCurrentProject(r.data[0])
    })
  }, [])

  // 管理员：拉取"已登录但未分配 AI key"的普通用户(管理员用默认 key，不计)，作为铃铛提醒
  useEffect(() => {
    if (!isAdmin) { setMissingKeys([]); return }
    usersApi.list()
      .then((r) => setMissingKeys((r.data || []).filter((u: any) => u.is_active && !u.has_ai_key && u.username !== 'admin')))
      .catch(() => setMissingKeys([]))
  }, [isAdmin, path])

  const displayName = user?.name || user?.email || '访客'
  const avatarChar = displayName.slice(0, 1).toUpperCase()
  const topTitle = TITLE_MAP.find((t) => t.test(path))?.title || 'Trace AI'

  const isActive = (it: NavItem) => (it.match ? it.match(path) : path === it.key || path.startsWith(it.key + '/'))

  return (
    <div style={{ display: 'flex', height: '100vh', width: '100%', overflow: 'hidden', background: '#F7F9FB' }}>
      {/* ===== SIDEBAR ===== */}
      <aside style={{
        width: SIDER_WIDTH, flex: 'none', background: '#fff', borderRight: '1px solid #ECEFF2',
        display: 'flex', flexDirection: 'column', height: '100%',
      }}>
        {/* logo */}
        <div style={{ padding: '20px 18px 16px', display: 'flex', alignItems: 'center', gap: 11 }}>
          <div style={{
            width: 38, height: 38, borderRadius: 11, background: TECH_GRADIENT, display: 'flex',
            alignItems: 'center', justifyContent: 'center', boxShadow: '0 5px 14px -5px rgba(217,119,87,.5)',
          }}>
            <svg width="22" height="22" viewBox="0 0 22 22" fill="none">
              <path d="M11 2L19.5 7V15L11 20L2.5 15V7L11 2Z" stroke="rgba(255,255,255,0.55)" strokeWidth="1.2" fill="none" />
              <path d="M7 11l2.5 2.5L15 8.5" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
              <circle cx="11" cy="2" r="1.3" fill="#fff" opacity="0.9" />
            </svg>
          </div>
          <div style={{ lineHeight: 1.1 }}>
            <div style={{ fontSize: 15, fontWeight: 700, letterSpacing: '.3px', color: '#0F172A' }}>
              Trace<span style={{ color: '#D97757', fontSize: 11, fontWeight: 500, marginLeft: 3, verticalAlign: 2 }}>AI</span>
            </div>
            <div style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10, color: '#94A3B8', marginTop: 3, letterSpacing: '.8px' }}>TEST PLATFORM</div>
          </div>
        </div>

        {/* nav */}
        <nav style={{ flex: 1, overflowY: 'auto', padding: '6px 0 14px' }}>
          {NAV_GROUPS.map((g) => {
            const items = g.items.filter((it) => !it.adminOnly || isAdmin)
            if (!items.length) return null
            return (
              <div key={g.label}>
                <div style={{ fontSize: 11, fontWeight: 500, color: '#94A3B8', letterSpacing: '1.2px', padding: '12px 22px 6px' }}>{g.label}</div>
                {items.map((it) => {
                  const active = isActive(it)
                  return (
                    <a key={it.key} onClick={() => navigate(it.key)}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 11, padding: '9px 14px', margin: '2px 12px',
                        borderRadius: 10, fontSize: 13.5, cursor: 'pointer', textDecoration: 'none', transition: 'all .15s',
                        color: active ? '#B5600A' : '#64748B', background: active ? '#FEF3EE' : 'transparent',
                        fontWeight: active ? 600 : 400,
                      }}
                      onMouseEnter={(e) => { if (!active) { e.currentTarget.style.background = '#F3F6F8'; e.currentTarget.style.color = '#1E293B' } }}
                      onMouseLeave={(e) => { if (!active) { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = '#64748B' } }}
                    >
                      <span className="ms" style={{ fontSize: 20 }}>{it.icon}</span>
                      <span>{it.label}</span>
                    </a>
                  )
                })}
              </div>
            )
          })}
        </nav>

        {/* user footer */}
        <div style={{ padding: 12, borderTop: '1px solid #ECEFF2' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 11, padding: '8px 10px', borderRadius: 11 }}>
            <div style={{
              width: 34, height: 34, flex: 'none', borderRadius: '50%', background: TECH_GRADIENT, color: '#fff',
              display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 14, fontWeight: 700,
            }}>{avatarChar}</div>
            <div style={{ flex: 1, lineHeight: 1.2, minWidth: 0 }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: '#1E293B', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{displayName}</div>
              <div style={{ fontSize: 11, color: '#94A3B8', marginTop: 2 }}>{isAdmin ? '管理员' : '普通用户'}</div>
            </div>
            <span className="ms" title="退出登录" onClick={handleLogout}
              style={{ fontSize: 18, color: '#CBD5E1', cursor: 'pointer' }}>logout</span>
          </div>
        </div>
      </aside>

      {/* ===== MAIN ===== */}
      <main style={{ flex: 1, display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0 }}>
        <header style={{
          height: 62, flex: 'none', background: 'rgba(247,249,251,.82)', backdropFilter: 'blur(8px)',
          borderBottom: '1px solid #ECEFF2', display: 'flex', alignItems: 'center', padding: '0 26px', gap: 16, zIndex: 5,
        }}>
          <div style={{ fontSize: 16, fontWeight: 700, color: '#0F172A', letterSpacing: '.2px' }}>{topTitle}</div>
          <div style={{ flex: 1 }} />
          <Popover
            trigger="click" placement="bottomRight"
            title={<span style={{ fontWeight: 700 }}>提醒</span>}
            content={
              <div style={{ width: 320, maxHeight: 360, overflowY: 'auto' }}>
                {missingKeys.length === 0 ? (
                  <div style={{ color: '#94A3B8', fontSize: 13, padding: '12px 4px', textAlign: 'center' }}>暂无提醒</div>
                ) : (
                  <>
                    <div style={{ fontSize: 12.5, color: '#B5710A', background: '#FEF6E7', border: '1px solid #F7E3BE', borderRadius: 8, padding: '8px 10px', marginBottom: 8 }}>
                      {missingKeys.length} 位用户登录后未分配 AI key，无法使用 AI 功能，请为其配置：
                    </div>
                    {missingKeys.map((u) => (
                      <div key={u.id} onClick={() => navigate('/users')}
                        style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '7px 8px', borderRadius: 8, cursor: 'pointer', fontSize: 13 }}
                        onMouseEnter={(e) => (e.currentTarget.style.background = '#F5F7FA')}
                        onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}>
                        <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7, color: '#334155' }}>
                          <span className="ms" style={{ fontSize: 16, color: '#EF4444' }}>key_off</span>
                          {u.name || u.username || u.email}
                        </span>
                        <span style={{ color: '#D97757', fontSize: 12 }}>去配置 ›</span>
                      </div>
                    ))}
                  </>
                )}
              </div>
            }>
            <button style={{
              width: 38, height: 38, border: '1px solid #E7ECF0', background: '#fff', borderRadius: 10,
              display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#64748B', cursor: 'pointer', position: 'relative',
            }}>
              <span className="ms" style={{ fontSize: 20 }}>notifications</span>
              {missingKeys.length > 0 && (
                <span style={{ position: 'absolute', top: 9, right: 10, width: 6, height: 6, borderRadius: '50%', background: '#EF4444', border: '1.5px solid #fff' }} />
              )}
            </button>
          </Popover>
        </header>

        <div style={{ flex: 1, overflowY: 'auto', overflowX: 'hidden' }}>
          {children}
        </div>
      </main>
    </div>
  )
}
