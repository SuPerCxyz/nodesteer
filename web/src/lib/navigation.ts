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
