import { useAuthStore } from '@/stores/auth-store'

export function useCanWrite() {
  return useAuthStore(
    (state) => state.auth.user?.role.includes('administrator') ?? false
  )
}

export function useCanRun() {
  return useAuthStore((state) => {
    const roles = state.auth.user?.role || []
    return roles.includes('administrator') || roles.includes('operator')
  })
}
