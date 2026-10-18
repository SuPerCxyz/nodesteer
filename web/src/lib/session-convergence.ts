import { useAuthStore } from '@/stores/auth-store'
import { subscribeSessionInvalidated } from './session-sync'

/**
 * 安装跨标签会话失效收敛：收到其它标签的失效信号时，本标签仍持有会话才清本地会话
 * 并执行 onConverged（由调用方清查询缓存并跳转登录页）；未登录标签忽略信号，避免误跳。
 *
 * 边界语义：
 * - 同一标签自身的退出不会收到自己的 storage 事件，不会重复跳转；
 * - 多个标签同时退出时，先收敛者已清空会话，后续信号因无会话而幂等跳过；
 * - 收敛后本标签也会广播信号（reset 内部），其它已收敛标签同样幂等跳过。
 */
export function installCrossTabSessionConvergence(
  onConverged: () => void
): () => void {
  return subscribeSessionInvalidated(() => {
    const { user, accessToken } = useAuthStore.getState().auth
    if (!user && !accessToken) return
    useAuthStore.getState().auth.reset()
    onConverged()
  })
}
