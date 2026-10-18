import { getToken } from '@/lib/api'
import { useAuthStore } from '@/stores/auth-store'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installCrossTabSessionConvergence } from './session-convergence'
import { SESSION_INVALIDATION_KEY, notifySessionInvalidated } from './session-sync'

/**
 * 跨标签登出即时收敛（OBS-20261016-G1-X13-CONVERGENCE-SEMANTICS）。
 *
 * Vitest 浏览器模式无法真正开第二个标签页，这里按浏览器规范模拟跨标签信号：
 * storage 事件只投递给其它同源文档，因此用 `new StorageEvent('storage', ...)`
 * 注入「另一个标签」发出的信号；发起标签自身的行为（不收到自己的事件）由浏览器
 * 规范保证，测试用「同一页面不派发事件」等价断言。
 */

const sampleUser = {
  accountNo: 'ACC-1',
  email: 'user@example.com',
  role: ['user'],
  exp: 1_700_000_000,
}

const unsubscribers: Array<() => void> = []

/** 安装跨标签收敛并登记清理，避免监听器泄漏到后续用例。 */
function install(onConverged: () => void) {
  const unsubscribe = installCrossTabSessionConvergence(onConverged)
  unsubscribers.push(unsubscribe)
  return unsubscribe
}

/** 模拟本标签已有活动会话（store 与 localStorage 同时就绪）。 */
function signIn(token: string) {
  useAuthStore.getState().auth.setAccessToken(token)
  useAuthStore.getState().auth.setUser({ ...sampleUser })
}

/** 注入「其它标签」写入 localStorage 后本标签收到的 storage 事件。 */
function receiveCrossTabSignal(
  key: string = SESSION_INVALIDATION_KEY,
  newValue: string | null = 'other-tab-signal'
) {
  window.dispatchEvent(
    new StorageEvent('storage', {
      key,
      newValue,
      oldValue: null,
      storageArea: localStorage,
      url: window.location.href,
    })
  )
}

describe('跨标签会话失效收敛', () => {
  beforeEach(() => {
    localStorage.clear()
    useAuthStore.getState().auth.reset()
    // reset 会广播失效信号；清掉，保证每个用例从无信号状态开始
    localStorage.clear()
  })

  afterEach(() => {
    while (unsubscribers.length > 0) unsubscribers.pop()!()
  })

  it('本标签退出（reset）会广播失效信号，且连续两次信号值不同', () => {
    signIn('tab-a-token')

    useAuthStore.getState().auth.reset()

    const first = localStorage.getItem(SESSION_INVALIDATION_KEY)
    expect(first).toBeTruthy()
    expect(localStorage.getItem('nodesteer_token')).toBeNull()
    expect(getToken()).toBeNull()

    notifySessionInvalidated()
    expect(localStorage.getItem(SESSION_INVALIDATION_KEY)).not.toBe(first)
  })

  it('另一标签退出后，本标签 1 秒内清空会话并跳转登录页', async () => {
    const onConverged = vi.fn()
    install(onConverged)
    signIn('tab-b-token')

    const startedAt = performance.now()
    receiveCrossTabSignal()
    await expect
      .poll(() => onConverged.mock.calls.length, { timeout: 1000 })
      .toBe(1)

    expect(performance.now() - startedAt).toBeLessThanOrEqual(1000)
    expect(onConverged).toHaveBeenCalledTimes(1)
    // 本地会话（store + 内存 token + localStorage）全部清空，后续请求不再带旧会话
    expect(useAuthStore.getState().auth.user).toBeNull()
    expect(useAuthStore.getState().auth.accessToken).toBe('')
    expect(getToken()).toBeNull()
    expect(localStorage.getItem('nodesteer_token')).toBeNull()
  })

  it('未登录标签收到信号不收敛、不误跳', () => {
    const onConverged = vi.fn()
    install(onConverged)

    receiveCrossTabSignal()

    expect(onConverged).not.toHaveBeenCalled()
    expect(useAuthStore.getState().auth.accessToken).toBe('')
    expect(useAuthStore.getState().auth.user).toBeNull()
  })

  it('同一标签自身退出不触发自身重复跳转', () => {
    const onConverged = vi.fn()
    install(onConverged)
    signIn('tab-a-token')

    // 本标签退出：写入信号，但浏览器不会把 storage 事件派发给写入者自身
    useAuthStore.getState().auth.reset()

    expect(onConverged).not.toHaveBeenCalled()
    expect(localStorage.getItem(SESSION_INVALIDATION_KEY)).toBeTruthy()
    expect(useAuthStore.getState().auth.accessToken).toBe('')
  })

  it('多个标签同时退出 / 重复信号幂等：只收敛一次', async () => {
    const onConverged = vi.fn()
    install(onConverged)
    signIn('tab-b-token')

    receiveCrossTabSignal()
    receiveCrossTabSignal()

    await expect
      .poll(() => onConverged.mock.calls.length, { timeout: 1000 })
      .toBe(1)
    expect(useAuthStore.getState().auth.accessToken).toBe('')
  })

  it('其它 storage 键的变化不触发收敛', () => {
    const onConverged = vi.fn()
    install(onConverged)
    signIn('tab-b-token')

    receiveCrossTabSignal('nodesteer_lang')

    expect(onConverged).not.toHaveBeenCalled()
    expect(useAuthStore.getState().auth.accessToken).toBe('tab-b-token')
  })
})
