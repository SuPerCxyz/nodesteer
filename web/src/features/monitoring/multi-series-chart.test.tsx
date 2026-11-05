import i18n from '@/i18n'
import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { page as browserPage, userEvent } from 'vitest/browser'
import { ThemeProvider } from '@/context/theme-provider'
import {
  MultiSeriesChart,
  downsampleSegment,
  downsampleStackSegments,
  maxPointsForWidth,
  monotoneCubicSegments,
  type ChartSeries,
} from './multi-series-chart'

const BASE_TIME = Date.parse('2026-10-17T00:00:00Z')

function points(values: number[], stepMs = 60_000) {
  return values.map((value, index) => ({
    timestamp: new Date(BASE_TIME + index * stepMs).toISOString(),
    value,
    quality: 1,
  }))
}

function series(index: number): ChartSeries {
  return {
    key: `series-${index}`,
    name: `Series ${index}`,
    points: points([10 + index, 12 + index, 11 + index, 14 + index]),
    type: 'area',
    stepNs: 60_000_000_000,
  }
}

function renderChart(
  data: ChartSeries[],
  theme: 'light' | 'dark' = 'dark',
  stacked = false
) {
  return render(
    <ThemeProvider defaultTheme={theme} storageKey={`chart-test-${theme}`}>
      <MultiSeriesChart
        series={data}
        height={220}
        yDomain={[0, 100]}
        stacked={stacked}
      />
    </ThemeProvider>
  )
}

function cubicY(
  segment: ReturnType<typeof monotoneCubicSegments>[number],
  t: number
) {
  const inverse = 1 - t
  return (
    inverse ** 3 * segment.from.y +
    3 * inverse ** 2 * t * segment.control1.y +
    3 * inverse * t ** 2 * segment.control2.y +
    t ** 3 * segment.to.y
  )
}

describe('MultiSeriesChart', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  afterEach(async () => {
    await browserPage.viewport(1280, 800)
    vi.unstubAllGlobals()
  })

  it('渲染 6 条曲线、独立渐变和面积路径', async () => {
    const data = Array.from({ length: 6 }, (_, index) => series(index))
    const page = await renderChart(data)

    await expect.element(page.getByTestId('multi-series-chart')).toBeVisible()
    expect(page.getByTestId('chart-area').elements()).toHaveLength(6)
    expect(page.getByTestId('chart-line').elements()).toHaveLength(6)
    expect(document.querySelectorAll('linearGradient')).toHaveLength(12)
    const ids = [...document.querySelectorAll('linearGradient')].map(
      (node) => node.id
    )
    expect(new Set(ids).size).toBe(12)
    await expect
      .element(page.getByRole('button', { name: /Series 1/ }))
      .toBeVisible()
    expect(
      page
        .getByRole('button', { name: /Series 1/ })
        .element()
        .getAttribute('aria-pressed')
    ).toBe('true')
  })

  it('图例点击隐藏对应曲线与面积', async () => {
    const data = Array.from({ length: 3 }, (_, index) => series(index))
    const page = await renderChart(data)

    await userEvent.click(page.getByRole('button', { name: /Series 0/ }))

    expect(
      page
        .getByRole('button', { name: /Series 0/ })
        .element()
        .getAttribute('aria-pressed')
    ).toBe('false')
    expect(page.getByTestId('chart-area').elements()).toHaveLength(2)
    expect(page.getByTestId('chart-line').elements()).toHaveLength(2)
  })

  it('悬停显示时间和各序列值', async () => {
    const data = [series(0), series(1)]
    const page = await renderChart(data)

    await userEvent.hover(page.getByTestId('chart-overlay'))

    const tooltip = page.getByTestId('chart-tooltip')
    await expect.element(tooltip).toBeVisible()
    await expect.element(tooltip).toHaveTextContent('Series 0')
    await expect.element(tooltip).toHaveTextContent('Series 1')
  })

  it('容量图坐标、图例及 tooltip 使用一致的字节单位', async () => {
    const data: ChartSeries[] = [
      {
        key: 'used',
        name: 'Used',
        points: points([1024 ** 3, 2 * 1024 ** 3]),
        unit: 'B',
      },
    ]
    const page = await render(
      <ThemeProvider defaultTheme='dark' storageKey='chart-bytes'>
        <MultiSeriesChart series={data} unit='B' ariaLabel='capacity' />
      </ThemeProvider>
    )
    const chart = page.getByTestId('multi-series-chart')
    await expect.element(chart).toHaveTextContent('2.0 GiB')
    const axisText = [...chart.element().querySelectorAll('svg text')].map(
      (node) => node.textContent || ''
    )
    expect(axisText.some((text) => text.includes('GiB'))).toBe(true)
    await userEvent.hover(page.getByTestId('chart-overlay'))
    await expect
      .element(page.getByTestId('chart-tooltip'))
      .toHaveTextContent('GiB')
  })

  it('堆叠模式按容差对齐偏移时间戳并生成面积', async () => {
    const data = Array.from({ length: 6 }, (_, index) => {
      const item = series(index)
      return {
        ...item,
        points: item.points.map((point) => ({
          ...point,
          timestamp: new Date(
            Date.parse(point.timestamp) + index * 300
          ).toISOString(),
        })),
      }
    })
    const page = await renderChart(data, 'dark', true)

    expect(page.getByTestId('chart-area').elements()).toHaveLength(6)
    expect(page.getByTestId('chart-line').elements()).toHaveLength(6)
  })

  it('堆叠模式在面积之上绘制 line 系列', async () => {
    const total: ChartSeries = {
      ...series(2),
      key: 'cpu.total',
      seriesId: 'cpu.total',
      paletteId: 'cyan-blue',
      type: 'line',
    }
    const page = await renderChart([series(0), series(1), total], 'dark', true)
    const container = page.getByTestId('multi-series-chart').element()

    expect(page.getByTestId('chart-area').elements()).toHaveLength(2)
    expect(page.getByTestId('chart-line').elements()).toHaveLength(3)

    const groups = [...container.querySelectorAll('g[data-series-id]')]
    const totalIndex = groups.findIndex(
      (node) => node.getAttribute('data-series-id') === 'cpu.total'
    )
    const areaGroups = groups.filter((node) =>
      node.getAttribute('data-series-id')?.startsWith('series-')
    )
    expect(totalIndex).toBeGreaterThan(groups.lastIndexOf(areaGroups[1]))

    const totalGroup = groups[totalIndex]
    expect(totalGroup.getAttribute('data-palette-id')).toBe('cyan-blue')
    const totalLine = totalGroup.querySelector('[data-testid="chart-line"]')
    expect(totalLine?.getAttribute('stroke-width')).toBe('1.8')
    expect(totalLine?.getAttribute('d')).toContain('C')
    const glow = totalGroup.querySelector('[data-testid="chart-glow"]')
    expect(glow?.getAttribute('stroke-width')).toBe('4')
    expect(glow?.getAttribute('stroke-opacity')).toBe('0.06')

    const stroke = totalLine?.getAttribute('stroke') || ''
    const gradientId = stroke.match(/url\(#(.+)\)/)?.[1]
    const gradient = gradientId ? document.getElementById(gradientId) : null
    expect(gradient?.querySelector('stop')?.getAttribute('stop-color')).toBe(
      '#2DF3D8'
    )
  })

  it('堆叠模式 line 系列按 gap 分段', async () => {
    const total: ChartSeries = {
      ...series(2),
      key: 'cpu.total',
      seriesId: 'cpu.total',
      paletteId: 'cyan-blue',
      type: 'line',
      points: [
        { timestamp: new Date(BASE_TIME).toISOString(), value: 1, quality: 1 },
        {
          timestamp: new Date(BASE_TIME + 60_000).toISOString(),
          value: 2,
          quality: 1,
        },
        {
          timestamp: new Date(BASE_TIME + 5 * 60_000).toISOString(),
          value: 3,
          quality: 1,
        },
        {
          timestamp: new Date(BASE_TIME + 6 * 60_000).toISOString(),
          value: 4,
          quality: 1,
        },
      ],
    }
    const page = await renderChart([series(0), total], 'dark', true)
    const groups = [
      ...page
        .getByTestId('multi-series-chart')
        .element()
        .querySelectorAll('g[data-series-id="cpu.total"]'),
    ]
    expect(groups).toHaveLength(2)
    expect(
      groups.every((group) =>
        group
          .querySelector('[data-testid="chart-line"]')
          ?.getAttribute('d')
          ?.includes('C')
      )
    ).toBe(true)
  })

  it('堆叠模式 line 单点段使用圆点', async () => {
    const total: ChartSeries = {
      ...series(2),
      key: 'cpu.total',
      seriesId: 'cpu.total',
      paletteId: 'cyan-blue',
      type: 'line',
      points: points([42]),
    }
    const page = await renderChart([series(0), total], 'dark', true)
    const container = page.getByTestId('multi-series-chart').element()

    expect(page.getByTestId('chart-point').elements()).toHaveLength(1)
    const totalGroup = container.querySelector('g[data-series-id="cpu.total"]')
    expect(totalGroup?.querySelector('[data-testid="chart-line"]')).toBeNull()
  })

  it('堆叠模式在缺失点处断开面积', async () => {
    const data = Array.from({ length: 6 }, (_, index) => ({
      ...series(index),
      points: [0, 1, 7, 8].map((minute, pointIndex) => ({
        timestamp: new Date(
          BASE_TIME + minute * 60_000 + index * 300
        ).toISOString(),
        value: 10 + pointIndex,
        quality: 1,
      })),
    }))
    const page = await renderChart(data, 'dark', true)

    expect(page.getByTestId('chart-area').elements().length).toBeGreaterThan(6)
  })

  it('按图表宽度分配点数上限', () => {
    expect(maxPointsForWidth(1200)).toBe(120)
    expect(maxPointsForWidth(600)).toBe(60)
    expect(maxPointsForWidth(320)).toBe(40)
    expect(maxPointsForWidth(1600)).toBe(120)
  })

  it('单调三次插值使用平滑曲线且不过冲', () => {
    const points = [
      { x: 0, y: 0 },
      { x: 10, y: 100 },
      { x: 20, y: 20 },
      { x: 30, y: 60 },
      { x: 40, y: 60 },
      { x: 50, y: 0 },
    ]
    const segments = monotoneCubicSegments(points)
    expect(segments).toHaveLength(points.length - 1)
    for (const segment of segments) {
      const min = Math.min(segment.from.y, segment.to.y)
      const max = Math.max(segment.from.y, segment.to.y)
      for (let step = 0; step <= 100; step++) {
        const y = cubicY(segment, step / 100)
        expect(y).toBeGreaterThanOrEqual(min - 1e-6)
        expect(y).toBeLessThanOrEqual(max + 1e-6)
      }
    }
    expect(segments[0].to.y).toBe(100)
  })

  it('降采样保留首尾与尖峰，且 gap 分段独立', () => {
    const points = Array.from({ length: 1000 }, (_, index) => ({
      timestamp: index * 1000,
      value: index === 500 ? 100 : 10 + (index % 3),
    }))
    const result = downsampleSegment(points, 120)
    expect(result.length).toBeLessThanOrEqual(120)
    expect(result[0]).toEqual(points[0])
    expect(result[result.length - 1]).toEqual(points[points.length - 1])
    expect(result.some((point) => point.value === 100)).toBe(true)

    const first = points.slice(0, 500)
    const second = points.slice(600)
    const firstResult = downsampleSegment(first, 120)
    const secondResult = downsampleSegment(second, 120)
    expect(firstResult[firstResult.length - 1]).toEqual(first[first.length - 1])
    expect(secondResult[0]).toEqual(second[0])
  })

  it('堆叠公共网格降采样保持各序列对齐', () => {
    const segment = Array.from({ length: 1000 }, (_, index) => ({
      time: index * 1000,
      values: [10 + (index % 2), index === 500 ? 100 : 10],
    }))
    const result = downsampleStackSegments([segment], 120)
    expect(result[0].length).toBeLessThanOrEqual(120)
    expect(result[0][0]).toEqual(segment[0])
    expect(result[0][result[0].length - 1]).toEqual(segment[segment.length - 1])
    expect(result[0].some((point) => point.values[1] === 100)).toBe(true)
    expect(result[0].every((point) => point.values.length === 2)).toBe(true)
  })

  it('大量数据时图表路径点数不超过上限，线宽与光晕符合规范', async () => {
    const points = Array.from({ length: 1000 }, (_, index) => ({
      timestamp: new Date(BASE_TIME + index * 1000).toISOString(),
      value: index === 500 ? 100 : 10 + (index % 5),
      quality: 1,
    }))
    const page = await renderChart([
      {
        key: 'many',
        name: 'Many',
        points,
        type: 'area',
        stepNs: 1_000_000_000,
      },
    ])
    const line = page.getByTestId('chart-line').element()
    const path = line.getAttribute('d') || ''
    expect(path).toContain('C')
    expect(path).not.toContain('L')
    const anchors =
      (path.match(/M/g) || []).length + (path.match(/C/g) || []).length
    expect(anchors).toBeLessThanOrEqual(120)
    expect(line.getAttribute('stroke-width')).toBe('1.8')
    const glow = page.getByTestId('chart-glow').element()
    expect(glow.getAttribute('stroke-width')).toBe('4')
    expect(glow.getAttribute('stroke-opacity')).toBe('0.06')
  })

  it('单点段使用圆点而不是曲线', async () => {
    const page = await renderChart([
      {
        key: 'single',
        name: 'Single',
        points: points([42]),
        type: 'area',
        stepNs: 60_000_000_000,
      },
    ])

    expect(page.getByTestId('chart-point').elements()).toHaveLength(1)
    expect(page.getByTestId('chart-line').elements()).toHaveLength(0)
    expect(page.getByTestId('chart-area').elements()).toHaveLength(0)
  })

  it.each([
    [1200, 120],
    [600, 60],
    [320, 40],
  ])('容器宽度 %ipx 时最多 %i 个点', async (width, expected) => {
    await browserPage.viewport(width, 900)
    const data = Array.from({ length: 1000 }, (_, index) => ({
      timestamp: new Date(BASE_TIME + index * 1000).toISOString(),
      value: 10 + (index % 5),
      quality: 1,
    }))
    const page = await renderChart([
      {
        key: 'many',
        name: 'Many',
        points: data,
        type: 'area',
        stepNs: 1_000_000_000,
      },
    ])

    await expect
      .poll(() => {
        const container = page
          .getByTestId('multi-series-chart')
          .element() as HTMLElement
        return Number(
          container.querySelector('svg')?.getAttribute('viewBox')?.split(' ')[2]
        )
      })
      .toBe(width)

    const path =
      page.getByTestId('chart-line').element().getAttribute('d') || ''
    const anchors =
      (path.match(/M/g) || []).length + (path.match(/C/g) || []).length
    expect(anchors).toBe(expected)
  })

  it('缺失数据断点不跨 gap 连接面积', async () => {
    const data: ChartSeries[] = [
      {
        key: 'gap',
        name: 'Gap',
        points: [
          {
            timestamp: new Date(BASE_TIME).toISOString(),
            value: 1,
            quality: 1,
          },
          {
            timestamp: new Date(BASE_TIME + 60_000).toISOString(),
            value: 2,
            quality: 1,
          },
          {
            timestamp: new Date(BASE_TIME + 5 * 60_000).toISOString(),
            value: 3,
            quality: 1,
          },
          {
            timestamp: new Date(BASE_TIME + 6 * 60_000).toISOString(),
            value: 4,
            quality: 1,
          },
        ],
        type: 'area',
        stepNs: 60_000_000_000,
      },
    ]
    const page = await renderChart(data)

    expect(page.getByTestId('chart-area').elements()).toHaveLength(2)
  })

  it('深色主题使用深色渐变颜色', async () => {
    const page = await renderChart(
      [{ ...series(0), paletteId: 'cyan-blue' }],
      'dark'
    )
    await expect
      .element(page.getByTestId('multi-series-chart'))
      .toHaveAttribute('data-theme', 'dark')
    expect(
      page
        .getByTestId('multi-series-chart')
        .element()
        .querySelector('linearGradient stop')
        ?.getAttribute('stop-color')
    ).toBe('#2DF3D8')
  })

  it('浅色主题使用浅色渐变颜色', async () => {
    const page = await renderChart(
      [{ ...series(0), paletteId: 'cyan-blue' }],
      'light'
    )
    await expect
      .element(page.getByTestId('multi-series-chart'))
      .toHaveAttribute('data-theme', 'light')
    expect(
      page
        .getByTestId('multi-series-chart')
        .element()
        .querySelector('linearGradient stop')
        ?.getAttribute('stop-color')
    ).toBe('#00A7A5')
  })

  it('stroke/fill/图例/tooltip/hover 使用同一调色板', async () => {
    const page = await renderChart(
      [{ ...series(0), paletteId: 'blue-violet' }],
      'dark'
    )
    const container = page.getByTestId('multi-series-chart').element()
    const gradients = [...container.querySelectorAll('linearGradient')]
    expect(gradients[0].querySelector('stop')?.getAttribute('stop-color')).toBe(
      '#509FFF'
    )
    expect(gradients[1].querySelector('stop')?.getAttribute('stop-color')).toBe(
      '#509FFF'
    )

    const legend = page.getByRole('button', { name: /Series 0/ }).element()
    const legendSwatch = legend.querySelector('span')
    expect(legendSwatch?.getAttribute('style')).toContain('rgb(80, 159, 255)')
    expect(legendSwatch?.getAttribute('style')).toContain('rgb(162, 108, 255)')

    await userEvent.hover(page.getByTestId('chart-overlay'))
    const hoverPoint = page.getByTestId('chart-hover-point').element()
    expect(hoverPoint.getAttribute('fill')).toBe('#509FFF')
    expect(hoverPoint.getAttribute('data-palette-id')).toBe('blue-violet')
    const tooltipSwatch = page.getByTestId('chart-tooltip-swatch').element()
    expect(tooltipSwatch.getAttribute('style')).toContain('rgb(80, 159, 255)')
    expect(tooltipSwatch.getAttribute('style')).toContain('rgb(162, 108, 255)')
  })

  it('主题切换保持 paletteId 只替换端点颜色', async () => {
    const darkPage = await renderChart(
      [{ ...series(0), paletteId: 'coral' }],
      'dark'
    )
    const darkContainer = darkPage.getByTestId('multi-series-chart').element()
    expect(
      darkContainer
        .querySelector('g[data-series-id]')
        ?.getAttribute('data-palette-id')
    ).toBe('coral')
    expect(
      darkContainer
        .querySelector('linearGradient stop')
        ?.getAttribute('stop-color')
    ).toBe('#FF688E')
    await darkPage.unmount()

    const lightPage = await renderChart(
      [{ ...series(0), paletteId: 'coral' }],
      'light'
    )
    const lightContainer = lightPage.getByTestId('multi-series-chart').element()
    expect(
      lightContainer
        .querySelector('g[data-series-id]')
        ?.getAttribute('data-palette-id')
    ).toBe('coral')
    expect(
      lightContainer
        .querySelector('linearGradient stop')
        ?.getAttribute('stop-color')
    ).toBe('#D84B72')
  })

  it('多实例渐变 ID 唯一', async () => {
    const page = await render(
      <ThemeProvider defaultTheme='dark' storageKey='chart-multi-instance'>
        <div>
          <MultiSeriesChart
            series={[series(0)]}
            height={220}
            yDomain={[0, 100]}
          />
          <MultiSeriesChart
            series={[series(1)]}
            height={220}
            yDomain={[0, 100]}
          />
        </div>
      </ThemeProvider>
    )
    const ids = [...document.querySelectorAll('linearGradient')].map(
      (node) => node.id
    )
    expect(ids.length).toBeGreaterThan(0)
    expect(new Set(ids).size).toBe(ids.length)
    await page.unmount()
  })

  it('16 条曲线使用 16 个不同调色板', async () => {
    const data = Array.from({ length: 16 }, (_, index) => ({
      ...series(index),
      seriesId: `series-${index}`,
    }))
    const page = await renderChart(data)
    const container = page.getByTestId('multi-series-chart').element()
    const paletteIds = [...container.querySelectorAll('g[data-series-id]')].map(
      (node) => node.getAttribute('data-palette-id')
    )
    expect(paletteIds).toHaveLength(16)
    expect(new Set(paletteIds).size).toBe(16)
  })

  it('图例隐藏不改变其他曲线颜色', async () => {
    const page = await renderChart([series(0), series(1)])
    const container = page.getByTestId('multi-series-chart').element()
    const readPalettes = () => {
      const map = new Map<string, string>()
      for (const node of container.querySelectorAll('g[data-series-id]')) {
        map.set(
          node.getAttribute('data-series-id') || '',
          node.getAttribute('data-palette-id') || ''
        )
      }
      return map
    }
    const before = readPalettes()
    await userEvent.click(page.getByRole('button', { name: /Series 0/ }))
    expect(readPalettes().get('series-1')).toBe(before.get('series-1'))
  })

  it('空数据不渲染图表', async () => {
    const page = await renderChart([])

    expect(page.getByTestId('multi-series-chart').elements()).toHaveLength(0)
  })

  it('窄屏不产生横向溢出', async () => {
    await browserPage.viewport(390, 800)
    const data = Array.from({ length: 6 }, (_, index) => series(index))
    const page = await renderChart(data)

    await expect.element(page.getByTestId('multi-series-chart')).toBeVisible()
    expect(document.documentElement.scrollWidth).toBeLessThanOrEqual(390)
  })

  it('ResizeObserver 宽度变化后更新 viewBox 与绘图范围', async () => {
    class MockResizeObserver {
      static instances: MockResizeObserver[] = []
      callback: ResizeObserverCallback
      constructor(callback: ResizeObserverCallback) {
        this.callback = callback
        MockResizeObserver.instances.push(this)
      }
      observe() {}
      unobserve() {}
      disconnect() {}
    }
    vi.stubGlobal(
      'ResizeObserver',
      MockResizeObserver as unknown as typeof ResizeObserver
    )

    const page = await renderChart([series(0)])
    await expect
      .poll(() => MockResizeObserver.instances.length)
      .toBeGreaterThan(0)
    const container = page
      .getByTestId('multi-series-chart')
      .element() as HTMLElement
    Object.defineProperty(container, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({
        width: 1200,
        height: 220,
        top: 0,
        left: 0,
        right: 1200,
        bottom: 220,
        x: 0,
        y: 0,
        toJSON: () => ({}),
      }),
    })
    const observer = MockResizeObserver.instances[0]
    observer.callback([], observer as unknown as ResizeObserver)

    await expect
      .poll(() => container.querySelector('svg')?.getAttribute('viewBox'))
      .toContain('0 0 1200')
    const path = container.querySelector('[data-testid="chart-area"]')
    const numbers = (path?.getAttribute('d') || '')
      .match(/-?\d+(\.\d+)?/g)
      ?.map(Number)
    expect(Math.max(...(numbers || [0]))).toBeLessThanOrEqual(1200)
    await page.unmount()
  })

  it.each([320, 768, 1280, 1600])('图表填满 %ipx 容器', async (width) => {
    await browserPage.viewport(width, 900)
    const page = await renderChart([series(0)])
    await expect
      .poll(() => {
        const container = page
          .getByTestId('multi-series-chart')
          .element() as HTMLElement
        const svg = container.querySelector('svg')
        const viewBoxWidth = Number(svg?.getAttribute('viewBox')?.split(' ')[2])
        return Math.abs(viewBoxWidth - container.getBoundingClientRect().width)
      })
      .toBeLessThanOrEqual(1)
    const svg = page.getByRole('img').element() as SVGSVGElement
    const container = page
      .getByTestId('multi-series-chart')
      .element() as HTMLElement
    expect(
      Math.abs(
        svg.viewBox.baseVal.width - container.getBoundingClientRect().width
      )
    ).toBeLessThanOrEqual(1)
    await page.unmount()
  })
})
