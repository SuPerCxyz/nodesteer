import { create } from 'zustand'
import { getToken, setToken, type SessionInfo } from '@/lib/api'
import { notifySessionInvalidated } from '@/lib/session-sync'

export type AuthUser = {
  accountNo: string
  email: string
  role: string[]
  exp: number
  username?: string
  userId?: string
  /** 头像 URL（/api/me、/api/login 返回），缺省表示无 */
  avatar?: string
}

type AuthState = {
  auth: {
    user: AuthUser | null
    accessToken: string
    setUser: (user: AuthUser | null) => void
    setAccessToken: (accessToken: string) => void
    resetAccessToken: () => void
    reset: () => void
  }
}

export function sessionToUser(session: SessionInfo): AuthUser {
  return {
    accountNo: session.user_id,
    email: session.username,
    role: [session.role],
    exp: new Date(session.expires).getTime(),
    username: session.username,
    userId: session.user_id,
    avatar: session.avatar || undefined,
  }
}

export const useAuthStore = create<AuthState>()((set) => ({
  auth: {
    user: null,
    accessToken: getToken() || '',
    setUser: (user) => set((state) => ({ auth: { ...state.auth, user } })),
    setAccessToken: (accessToken) => {
      setToken(accessToken)
      set((state) => ({ auth: { ...state.auth, accessToken } }))
    },
    resetAccessToken: () => {
      setToken(null)
      set((state) => ({ auth: { ...state.auth, accessToken: '' } }))
    },
    reset: () => {
      setToken(null)
      set((state) => ({ auth: { ...state.auth, user: null, accessToken: '' } }))
      // 会话已在本标签失效（退出登录 / 401）：广播信号，其它标签立即收敛。
      notifySessionInvalidated()
    },
  },
}))
