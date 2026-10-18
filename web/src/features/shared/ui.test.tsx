import { describe, expect, it, beforeAll } from 'vitest'
import { render } from 'vitest-browser-react'
import i18n from '@/i18n'
import '@/styles/index.css'
import { statusLabel } from '@/lib/status-labels'
import { ErrorState, SearchToolbar, StatusBadge } from './ui'

describe('ErrorState', () => {
  it('renders string errors verbatim instead of falling back to noData', async () => {
    const { getByRole } = await render(<ErrorState error='请选择任务' />)

    await expect.element(getByRole('alert')).toHaveTextContent('请选择任务')
  })

  it('renders Error instances', async () => {
    const { getByRole } = await render(<ErrorState error={new Error('boom')} />)

    await expect.element(getByRole('alert')).toHaveTextContent('boom')
  })

  it('renders object errors with a message field', async () => {
    const { getByRole } = await render(
      <ErrorState error={{ message: 'object message' }} />
    )

    await expect.element(getByRole('alert')).toHaveTextContent('object message')
  })

  it('falls back to noData when the input is not readable', async () => {
    const { getByRole } = await render(<ErrorState error={undefined} />)

    await expect.element(getByRole('alert')).toHaveTextContent('暂无数据')
  })

  it('ignores blank strings and falls back to noData', async () => {
    const { getByRole } = await render(<ErrorState error='   ' />)

    await expect.element(getByRole('alert')).toHaveTextContent('暂无数据')
  })
})

describe('StatusBadge 本地化（OBS-20261014-STATUSBADGE-MAINTENANCE-DISABLED-NO-ZH / TRANSFER-STATUS-BADGE-NOT-LOCALIZED）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it.each([
    ['maintenance', '维护'],
    ['disabled', '已禁用'],
    ['uploading', '上传中'],
    ['delivering', '投递中'],
  ])('%s 徽标显示中文文案', async (status, label) => {
    const page = await render(<StatusBadge status={status} />)

    await expect.element(page.getByText(label, { exact: true })).toBeVisible()
    await expect
      .element(page.getByText(status, { exact: true }))
      .not.toBeInTheDocument()
  })

  it('statusLabel 返回本地化文案，未知值回退原始枚举', () => {
    expect(statusLabel('maintenance')).toBe('维护')
    expect(statusLabel('delivering')).toBe('投递中')
    expect(statusLabel('weird_state')).toBe('weird state')
  })
})

/** 计算元素前景/背景对比度（背景为半透明时先与页面白底合成） */
function contrastRatioOf(element: Element): number {
  const canvas = document.createElement('canvas')
  canvas.width = canvas.height = 1
  const context = canvas.getContext('2d')!
  // 先铺白底再画目标色：oklch()/color-mix() 与半透明色都会被画布解析为 sRGB
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

describe('状态徽标与错误提示对比度（P11 a11y：WCAG 1.4.3 ≥ 4.5:1）', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it.each([
    ['online', '在线'],
    ['offline', '离线'],
    ['running', '运行中'],
    ['degraded', '降级'],
  ])('%s 徽标文字与底色对比度 ≥ 4.5', async (status, label) => {
    const page = await render(<StatusBadge status={status} />)
    const badge = page.container.querySelector('[data-slot="badge"]')!

    await expect.element(page.getByText(label, { exact: true })).toBeVisible()
    expect(badge).not.toBeNull()
    expect(contrastRatioOf(badge)).toBeGreaterThanOrEqual(4.5)
  })

  it('错误提示文案与底色对比度 ≥ 4.5', async () => {
    const page = await render(<ErrorState error='请选择任务' />)
    const alert = page.container.querySelector('[role="alert"]')!

    expect(alert).not.toBeNull()
    expect(contrastRatioOf(alert)).toBeGreaterThanOrEqual(4.5)
  })
})

describe('SearchToolbar 输入可访问名（P17 a11y：placeholder-only 输入补 aria-label）', () => {
  it('搜索输入以 placeholder 文案作为可访问名，而非仅靠 placeholder 属性', async () => {
    const page = await render(
      <SearchToolbar
        value=''
        onChange={() => {}}
        placeholder='搜索 Agent...'
      />
    )

    const input = page.getByRole('textbox', { name: '搜索 Agent...' })
    await expect.element(input).toBeVisible()
    await expect.element(input).toHaveAttribute('aria-label', '搜索 Agent...')
  })
})
