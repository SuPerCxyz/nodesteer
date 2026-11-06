import '@/styles/index.css'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { PALETTE_MODE, applyPaletteMode } from './palette-mode'

const root = document.documentElement
const originalClass = root.className
const originalPalette = root.dataset.palette

/** Resolve a CSS custom property through the cascade to a concrete color. */
function resolveToken(token: string) {
  const probe = document.createElement('span')
  probe.style.color = `var(${token})`
  document.body.append(probe)
  const color = getComputedStyle(probe).color
  probe.remove()
  return color
}

function setDark(dark: boolean) {
  root.classList.toggle('dark', dark)
  root.classList.toggle('light', !dark)
}

beforeEach(() => {
  setDark(false)
  applyPaletteMode(root, 'spectrum')
})

afterEach(() => {
  root.className = originalClass
  if (originalPalette === undefined) {
    delete root.dataset.palette
  } else {
    root.dataset.palette = originalPalette
  }
})

describe('调色板模式', () => {
  it('默认启用 spectrum，并加载 E 浅色主色与界面底色', () => {
    expect(PALETTE_MODE).toBe('spectrum')
    applyPaletteMode(root)
    expect(root.dataset.palette).toBe('spectrum')
    expect(resolveToken('--primary')).toBe('rgb(59, 111, 245)')
    expect(resolveToken('--primary-2')).toBe('rgb(11, 150, 166)')
    expect(resolveToken('--background')).toBe('rgb(241, 245, 251)')
    expect(resolveToken('--card')).toBe('rgb(255, 255, 255)')
    expect(resolveToken('--foreground')).toBe('rgb(16, 28, 44)')
    expect(resolveToken('--muted-foreground')).toBe('rgb(91, 107, 128)')
  })

  it('切 new / legacy 分别恢复对应浅色主色', () => {
    applyPaletteMode(root, 'spectrum')
    expect(resolveToken('--primary')).toBe('rgb(59, 111, 245)')

    applyPaletteMode(root, 'new')
    expect(resolveToken('--primary')).toBe('rgb(0, 95, 251)')
    expect(resolveToken('--background')).toBe('rgb(255, 255, 255)')

    applyPaletteMode(root, 'legacy')
    expect(resolveToken('--primary')).toBe('rgb(30, 41, 59)')
    expect(resolveToken('--background')).toBe('rgb(255, 255, 255)')
  })

  it('深色模式下三种调色板主色分别生效', () => {
    setDark(true)

    applyPaletteMode(root, 'spectrum')
    expect(resolveToken('--primary')).toBe('rgb(122, 162, 255)')
    expect(resolveToken('--background')).toBe('rgb(10, 17, 28)')

    applyPaletteMode(root, 'new')
    expect(resolveToken('--primary')).toBe('rgb(91, 155, 255)')

    applyPaletteMode(root, 'legacy')
    expect(resolveToken('--primary')).toBe('rgb(226, 232, 240)')
    expect(resolveToken('--background')).toBe('rgb(15, 23, 42)')
  })

  it('普通系列与危险/警告色完全分离，Swap/iowait/磁盘写不落入告警色', () => {
    for (const dark of [false, true]) {
      setDark(dark)
      applyPaletteMode(root, 'spectrum')
      // E 规格中 info 与 series-1 同色，表示品牌信息色；危险/警告必须分离。
      expect(resolveToken('--chart-alert-info-from')).toBe(
        resolveToken('--chart-series-1-from')
      )
      const alerts = new Set(
        [
          '--chart-alert-danger-from',
          '--chart-alert-danger-to',
          '--chart-alert-warning-from',
          '--chart-alert-warning-to',
        ].map(resolveToken)
      )

      for (let index = 1; index <= 16; index++) {
        expect(alerts.has(resolveToken(`--chart-series-${index}-from`))).toBe(
          false
        )
        expect(alerts.has(resolveToken(`--chart-series-${index}-to`))).toBe(
          false
        )
      }

      for (const token of [
        '--chart-series-11-from',
        '--chart-series-11-to',
        '--chart-series-12-from',
        '--chart-series-12-to',
      ]) {
        expect(alerts.has(resolveToken(token))).toBe(false)
      }
    }
  })
})
