import { create } from 'zustand'

export interface PlatformUser {
  sub: string          // 平台账号或外部 SSO 用户标识
  role: 'admin' | 'user'
  name: string
  email: string
}

interface AuthStore {
  user: PlatformUser | null
  isAdmin: boolean
  setUser: (u: PlatformUser | null) => void
  logout: () => void
}

export const useAuthStore = create<AuthStore>((set) => ({
  user: null,
  isAdmin: false,
  setUser: (u) => set({ user: u, isAdmin: u?.role === 'admin' }),
  logout: () => {
    localStorage.removeItem('platform_jwt')
    localStorage.removeItem('platform_user')
    set({ user: null, isAdmin: false })
  },
}))

/** 从 localStorage 恢复用户状态（页面刷新后调用）。 */
export function restoreAuth(): PlatformUser | null {
  try {
    const raw = localStorage.getItem('platform_user')
    if (!raw) return null
    return JSON.parse(raw) as PlatformUser
  } catch {
    return null
  }
}
