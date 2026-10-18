/**
 * 跨标签会话失效信号（storage 事件）。
 *
 * 退出登录 / 会话被吊销的标签调用 notifySessionInvalidated() 写入一个每次不同的值，
 * 浏览器只把 storage 事件派发给其它同源标签（发起标签自身不会收到），
 * 由 installCrossTabSessionConvergence 完成收敛。
 *
 * 选择 localStorage 而不是 BroadcastChannel：与令牌持久化共用同一存储机制，
 * 无需管理连接生命周期，且信号与存储状态天然同源；
 * 值取时间戳 + 随机数，保证连续两次退出的值不同、storage 事件必定触发。
 */
export const SESSION_INVALIDATION_KEY = 'nodesteer_session_invalidated'

/**
 * 安全获取 localStorage：沙箱 iframe / 阻止 Cookie 等上下文里，
 * **访问 `localStorage` 属性本身**就会抛 SecurityError（不只是写入失败），
 * 故必须连属性访问一起包在 try 内（代码评审 minor-02）。
 */
function safeStorage(): Storage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage
  } catch {
    return null
  }
}

/** 广播会话失效信号；存储不可用时静默降级（不影响本标签自身退出）。 */
export function notifySessionInvalidated(): void {
  const storage = safeStorage()
  if (!storage) return
  try {
    storage.setItem(
      SESSION_INVALIDATION_KEY,
      `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`
    )
  } catch {
    /* 隐私模式等写入失败：其它标签收不到信号，但本标签退出不受影响 */
  }
}

/** 订阅其它标签的会话失效信号，返回取消订阅函数。 */
export function subscribeSessionInvalidated(handler: () => void): () => void {
  const onStorage = (event: StorageEvent) => {
    if (event.key === SESSION_INVALIDATION_KEY) handler()
  }
  window.addEventListener('storage', onStorage)
  return () => window.removeEventListener('storage', onStorage)
}
