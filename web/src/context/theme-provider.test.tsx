import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { getCookie, removeCookie, setCookie } from '@/lib/cookies'
import { ThemeProvider, useTheme } from './theme-provider'

function Controls() {
  const { resolvedTheme, setTheme } = useTheme()
  return (
    <>
      <span>{resolvedTheme}</span>
      <button onClick={() => setTheme('light')}>选浅色</button>
    </>
  )
}

describe('系统主题同步', () => {
  let dark = false
  let media: EventTarget
  let meta: HTMLMetaElement
  let originalMeta: string | null
  let originalClass: string
  let originalCookie: string | undefined
  let createdMeta: boolean
  beforeEach(() => {
    dark = false
    media = new EventTarget()
    Object.defineProperty(media, 'matches', { get: () => dark })
    vi.stubGlobal(
      'matchMedia',
      vi.fn(() => media as MediaQueryList)
    )
    originalCookie = getCookie('vite-ui-theme')
    originalClass = document.documentElement.className
    removeCookie('vite-ui-theme')
    const existing = document.querySelector<HTMLMetaElement>(
      "meta[name='theme-color']"
    )
    createdMeta = !existing
    meta = existing || document.createElement('meta')
    meta.name = 'theme-color'
    originalMeta = meta.getAttribute('content')
    if (createdMeta) document.head.append(meta)
  })
  afterEach(() => {
    vi.unstubAllGlobals()
    document.documentElement.className = originalClass
    if (createdMeta) meta.remove()
    else if (originalMeta === null) meta.removeAttribute('content')
    else meta.content = originalMeta
    if (originalCookie) setCookie('vite-ui-theme', originalCookie)
    else removeCookie('vite-ui-theme')
  })
  it('媒体变化同步解析值/class/meta，明确浅色不被系统覆盖', async () => {
    const { getByText, getByRole } = await render(
      <ThemeProvider>
        <Controls />
      </ThemeProvider>
    )
    await expect.element(getByText('light', { exact: true })).toBeVisible()
    dark = true
    media.dispatchEvent(new Event('change'))
    await expect.element(getByText('dark', { exact: true })).toBeVisible()
    expect(document.documentElement.classList.contains('dark')).toBe(true)
    expect(meta.content).toBe('#020817')
    await userEvent.click(getByRole('button', { name: '选浅色' }))
    await expect.element(getByText('light', { exact: true })).toBeVisible()
    expect(meta.content).toBe('#fff')
    dark = false
    media.dispatchEvent(new Event('change'))
    dark = true
    media.dispatchEvent(new Event('change'))
    await expect.element(getByText('light', { exact: true })).toBeVisible()
    expect(meta.content).toBe('#fff')
  })
})
