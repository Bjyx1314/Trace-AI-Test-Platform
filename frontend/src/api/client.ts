import axios from 'axios'

const api = axios.create({
  baseURL: '/api',
  timeout: 30000,
})

// 请求拦截：自动带上平台 JWT
api.interceptors.request.use((config) => {
  const jwt = localStorage.getItem('platform_jwt')
  if (jwt) {
    config.headers['Authorization'] = `Bearer ${jwt}`
  }
  return config
})

api.interceptors.response.use(
  (res) => res,
  (err) => {
    const url: string = err?.config?.url || ''
    // 登录接口的 401 由登录页自行展示错误，不做全局跳转
    const isAuthCall = url.includes('/auth/login')
    if (err?.response?.status === 401 && !isAuthCall) {
      // JWT 过期或无效，清除本地状态，跳转登录页
      localStorage.removeItem('platform_jwt')
      localStorage.removeItem('platform_user')
      if (!window.location.pathname.startsWith('/login')) {
        window.location.href = '/login'
      }
    }
    console.error('[API Error]', err?.response?.data || err.message)
    return Promise.reject(err)
  }
)

export default api
