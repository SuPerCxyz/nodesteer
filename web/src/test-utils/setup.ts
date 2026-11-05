type NavigateEventLike = {
  cancelable: boolean
  destination: { url: string; sameDocument?: boolean }
  preventDefault: () => void
}

type NavigationLike = {
  addEventListener: (
    type: 'navigate',
    listener: (event: NavigateEventLike) => void
  ) => void
}

/**
 * 浏览器模式下所有测试文件共享同一个页面。
 *
 * 应用代码在保存成功、登录成功等路径会调用 `window.location.assign/replace`，
 * 表单未 preventDefault 时也会触发真实导航。一旦 iframe 离开测试入口，Vitest 会
 * 报 "Cannot connect to the iframe" 或后续文件 "Failed to fetch dynamically imported
 * module"，表现为随机失败。
 *
 * Chromium 中 `location.assign/replace` 是不可配置的 unforgeable 属性，无法直接
 * stub；这里用 Navigation API 在导航发生前取消跨文档导航。同文档的 hash 跳转
 * （如 SkipToMain 的 `#content`）必须放行，保持原有行为。
 *
 * 必须在 setup 模块加载时立即安装，不能放到 beforeAll：测试文件在 beforeAll
 * 之前就被动态 import，导入期间发生的导航同样会打断 iframe。
 */
export const blockedNavigations: string[] = []

const navigation =
  typeof window === 'undefined'
    ? undefined
    : (window as unknown as { navigation?: NavigationLike }).navigation

navigation?.addEventListener('navigate', (event) => {
  if (!event.cancelable || event.destination.sameDocument) return
  blockedNavigations.push(event.destination.url)
  event.preventDefault()
})
