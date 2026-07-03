import { useEffect, useState } from 'react'
import { BrowserRouter, Routes, Route, useSearchParams, useNavigate } from 'react-router-dom'
import { ConfigProvider, Spin, Result } from 'antd'
import zhCN from 'antd/locale/zh_CN'
import { themeConfig } from './styles/theme'
import AppLayout from './components/AppLayout'
import Dashboard from './pages/Dashboard'
import Requirements from './pages/Requirements'
import RequirementDetail from './pages/RequirementDetail'
import TestCases from './pages/TestCases'
import TestCaseList from './pages/TestCaseList'
import ExecutionHistory from './pages/ExecutionHistory'
import DefectReview from './pages/DefectReview'
import EnumManagement from './pages/EnumManagement'
import PageCache from './pages/PageCache'
import FrameworkRepos from './pages/FrameworkRepos'
import UserManagement from './pages/UserManagement'
import SystemSettings from './pages/SystemSettings'
import Unauthorized from './pages/Unauthorized'
import Login from './pages/Login'
import { useAuthStore, restoreAuth } from './store/authStore'
import { authApi } from './api'
import { loadPlatformGroups } from './components/ExecConfigModal'

function AuthInit({ children }: { children: React.ReactNode }) {
  const [searchParams] = useSearchParams()
  const navigate = useNavigate()
  const { setUser } = useAuthStore()
  const [ready, setReady] = useState(false)

  useEffect(() => {
    const init = async () => {
      const urlToken = searchParams.get('token')

      if (urlToken) {
        // 从可选外部 SSO 跳转过来：用 token 静默换平台 JWT。
        try {
          const r = await authApi.verify(urlToken)
          localStorage.setItem('platform_jwt', r.data.jwt)
          localStorage.setItem('platform_user', JSON.stringify(r.data.user))
          setUser(r.data.user)
          // 清除 URL 中的 token 参数；若落在 /login 或 /unauthorized 则回首页
          const params = new URLSearchParams(searchParams)
          params.delete('token')
          const newSearch = params.toString()
          const p = window.location.pathname
          const target = (p === '/login' || p === '/unauthorized') ? '/' : p
          navigate(target + (newSearch ? `?${newSearch}` : ''), { replace: true })
        } catch {
          navigate('/unauthorized', { replace: true })
        }
      } else {
        // 尝试从 localStorage 恢复
        const saved = restoreAuth()
        if (saved) {
          setUser(saved as any)
        }
        // mock_mode 下不要求必须登录，正常进入
      }
      // 载入「端→执行口径」配置映射(来自 platform 枚举 parent_key)，供执行分流用。失败保留内置兜底，不阻断进入。
      loadPlatformGroups()
      setReady(true)
    }
    init()
  }, [])

  if (!ready) {
    return (
      <div style={{ display: 'flex', justifyContent: 'center', alignItems: 'center', height: '100vh' }}>
        <Spin size="large" tip="验证身份中..." />
      </div>
    )
  }

  return <>{children}</>
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const authed = !!localStorage.getItem('platform_jwt')
  const navigate = useNavigate()
  useEffect(() => {
    if (authed) return
    // 配置了外部 SSO 时跳转换票，否则使用平台本地登录页。
    const here = encodeURIComponent(window.location.href)
    const go = (base: string) => {
      const b = (base || '').replace(/\/+$/, '')
      if (!b) {
        navigate('/login', { replace: true })
        return
      }
      window.location.href = `${b}/api/auth/sso/launch?redirect=${here}`
    }
    authApi.ssoConfig()
      .then((r) => go(r.data.external_sso_url))
      .catch(() => navigate('/login', { replace: true }))
  }, [authed])
  if (!authed) return null
  return <>{children}</>
}

function RequireAdmin({ children }: { children: React.ReactNode }) {
  const { isAdmin } = useAuthStore()
  if (!isAdmin) {
    return (
      <Result status="403" title="无权限" subTitle="该功能仅管理员可访问，请联系管理员。" />
    )
  }
  return <>{children}</>
}

export default function App() {
  return (
    <ConfigProvider locale={zhCN} theme={themeConfig}>
      <BrowserRouter>
        <AuthInit>
          <Routes>
            <Route path="/login" element={<Login />} />
            <Route path="/unauthorized" element={<Unauthorized />} />
            <Route
              path="*"
              element={
                <RequireAuth>
                  <AppLayout>
                    <Routes>
                      <Route path="/" element={<Dashboard />} />
                      <Route path="/requirements" element={<Requirements />} />
                      <Route path="/requirements/:id" element={<RequirementDetail />} />
                      <Route path="/testcases" element={<TestCases />} />
                      <Route path="/testcases/list" element={<TestCaseList />} />
                      <Route path="/executions" element={<ExecutionHistory />} />
                      <Route path="/defects" element={<DefectReview />} />
                      <Route path="/enums" element={<RequireAdmin><EnumManagement /></RequireAdmin>} />
                      <Route path="/page-cache" element={<PageCache />} />
                      <Route path="/frameworks" element={<RequireAdmin><FrameworkRepos /></RequireAdmin>} />
                      <Route path="/users" element={<RequireAdmin><UserManagement /></RequireAdmin>} />
                      <Route path="/system-settings" element={<RequireAdmin><SystemSettings /></RequireAdmin>} />
                    </Routes>
                  </AppLayout>
                </RequireAuth>
              }
            />
          </Routes>
        </AuthInit>
      </BrowserRouter>
    </ConfigProvider>
  )
}
