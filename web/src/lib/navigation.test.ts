import { describe, expect, it } from 'vitest'
import { isSignInPath, safeRedirect, signInRedirectTarget } from './navigation'

describe('navigation redirects', () => {
  it('does not loop back to the sign-in page', () => {
    expect(isSignInPath('/sign-in')).toBe(true)
    expect(isSignInPath('/sign-in?redirect=%2Ftasks')).toBe(true)
    expect(safeRedirect('/sign-in?redirect=%2Ftasks')).toBe('/')
  })

  it('does not nest sign-in into the redirect target', () => {
    expect(signInRedirectTarget('/nodes')).toBe('/nodes')
    expect(signInRedirectTarget('/sign-in?redirect=%2Fnodes')).toBeNull()
    expect(
      signInRedirectTarget(
        '/sign-in?redirect=%2Fsign-in%3Fredirect%3D%252Fnodes'
      )
    ).toBeNull()
  })

  it('keeps same-origin paths and rejects external URLs', () => {
    expect(safeRedirect('/tasks?status=running#top')).toBe(
      '/tasks?status=running#top'
    )
    expect(safeRedirect('https://example.com/tasks')).toBe('/')
    expect(safeRedirect('//example.com/tasks')).toBe('/')
  })
})
