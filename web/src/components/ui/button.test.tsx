import '@/styles/index.css'
import { afterEach, describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { Button } from './button'

function contrastRatioOf(element: Element): number {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const context = canvas.getContext('2d')!
  const resolveOverWhite = (css: string): [number, number, number] => {
    context.clearRect(0, 0, 1, 1)
    context.fillStyle = '#fff'
    context.fillRect(0, 0, 1, 1)
    context.fillStyle = css
    context.fillRect(0, 0, 1, 1)
    const [r, g, b] = context.getImageData(0, 0, 1, 1).data
    return [r, g, b]
  }
  const luminance = ([r, g, b]: [number, number, number]) => {
    const channel = (value: number) => {
      const srgb = value / 255
      return srgb <= 0.03928 ? srgb / 12.92 : ((srgb + 0.055) / 1.055) ** 2.4
    }
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)
  }
  const style = getComputedStyle(element)
  const foreground = resolveOverWhite(style.color)
  const background = resolveOverWhite(style.backgroundColor)
  const [light, dark] = [luminance(foreground), luminance(background)].sort(
    (a, b) => b - a
  )
  return (light + 0.05) / (dark + 0.05)
}

afterEach(async () => {
  document.documentElement.classList.remove('dark')
})

describe('Button header-action', () => {
  it('浅色主题使用近黑底白字并满足对比度', async () => {
    const page = await render(<Button variant='header-action'>创建</Button>)
    const button = page.getByRole('button').element()

    expect(getComputedStyle(button).backgroundColor).toBe('rgb(11, 18, 32)')
    expect(getComputedStyle(button).color).toBe('rgb(255, 255, 255)')
    expect(contrastRatioOf(button)).toBeGreaterThanOrEqual(4.5)
  })

  it('深色主题使用深灰底并保留可见边框', async () => {
    document.documentElement.classList.add('dark')
    const page = await render(<Button variant='header-action'>创建</Button>)
    const style = getComputedStyle(page.getByRole('button').element())

    expect(style.backgroundColor).toBe('rgb(30, 41, 59)')
    expect(style.borderTopWidth).toBe('1px')
    expect(style.borderTopColor).toBe('rgb(100, 116, 139)')
    expect(style.borderTopStyle).not.toBe('none')
  })

  it('危险操作仍然使用 destructive 红色', async () => {
    const page = await render(<Button variant='destructive'>删除</Button>)
    const button = page.getByRole('button').element()

    expect(button.className).toContain('bg-danger')
    expect(button.className).not.toContain('bg-header-action')
    expect(getComputedStyle(button).backgroundColor).toBe('rgb(198, 40, 40)')
  })
})
