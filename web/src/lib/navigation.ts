const SIGN_IN_PATH = '/sign-in'

export function currentPath(): string {
  if (typeof window === 'undefined') return '/'
  return `${window.location.pathname}${window.location.search}${window.location.hash}`
}

export function isSignInPath(value: string): boolean {
  try {
    const path = new URL(value, 'http://nodesteer.local').pathname
    return path === SIGN_IN_PATH
  } catch {
    return false
  }
}

/**
 * 未登录跳转登录页时的回跳目标。
 * 当前已在登录页时返回 null，避免把 sign-in 自身再包进 redirect 造成嵌套回跳。
 */
export function signInRedirectTarget(
  value: string = currentPath()
): string | null {
  return isSignInPath(value) ? null : value
}

/** Keep post-login navigation on this origin and never redirect back to sign-in. */
export function safeRedirect(value: string | null | undefined): string {
  if (!value) return '/'

  try {
    const origin =
      typeof window === 'undefined'
        ? 'http://nodesteer.local'
        : window.location.origin
    const target = new URL(value, origin)
    if (target.origin !== origin || target.pathname === SIGN_IN_PATH) {
      return '/'
    }
    return `${target.pathname}${target.search}${target.hash}` || '/'
  } catch {
    return '/'
  }
}
