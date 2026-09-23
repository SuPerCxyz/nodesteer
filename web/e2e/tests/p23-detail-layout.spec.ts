import { test, expect } from 'playwright/test'
import { apiRequest, loginViaApi, TEST_URLS } from './fixtures'

/**
 * P23 详情页单页化与内容宽度一致性（2026-09 布局改造）
 * - 列表页与详情页使用同一内容宽度策略（全宽 + 2400px 上限）
 * - 任务/执行详情取消 Tab，改为单页分区
 * - 详情字段响应式栅格：>=1024px 2 列，>=1536px 3 列
 * - 侧边栏在 <=700px 视口高度隐藏分组标签，保证菜单项全部可见
 */
test.describe('P23 详情页单页化与内容宽度', () => {
  async function firstIds(page: Parameters<typeof loginViaApi>[0]) {
    const nodes = await apiRequest(page, '/api/nodes')
    const tasks = await apiRequest(page, '/api/tasks')
    const execs = await apiRequest(page, '/api/executions?limit=1')
    const pick = (b: unknown) =>
      Array.isArray(b) ? b[0] : ((b as { items?: unknown[] })?.items || [])[0]
    return {
      nodeId: (pick(nodes.body) as { id: string })?.id,
      taskId: (pick(tasks.body) as { id: string })?.id,
      execId: (pick(execs.body) as { id: string })?.id,
    }
  }

  async function mainWidth(page: Parameters<typeof loginViaApi>[0], path: string) {
    await page.goto(path)
    await page.waitForTimeout(1200)
    return page.evaluate(() => {
      const m = document.querySelector('main')
      return m ? Math.round(m.getBoundingClientRect().width) : null
    })
  }

  test('P23-01 列表与详情内容宽度一致，4K 下不超过 2400px', async ({ page }) => {
    await loginViaApi(page)
    const { nodeId, taskId, execId } = await firstIds(page)
    await page.setViewportSize({ width: 1920, height: 1080 })
    const widths = []
    for (const p of [
      TEST_URLS.nodes,
      `/agents/${nodeId}`,
      TEST_URLS.tasks,
      `/tasks/${taskId}`,
      TEST_URLS.executions,
      `/executions/${execId}`,
    ]) {
      widths.push(await mainWidth(page, p))
    }
    expect(new Set(widths).size).toBe(1)

    await page.setViewportSize({ width: 3840, height: 2160 })
    const wide = await mainWidth(page, TEST_URLS.tasks)
    expect(wide).toBe(2400)
  })

  test('P23-02 任务详情单页分区且字段多列', async ({ page }) => {
    await loginViaApi(page)
    const { taskId } = await firstIds(page)
    await page.setViewportSize({ width: 1920, height: 1080 })
    await page.goto(`/tasks/${taskId}`)
    await page.waitForTimeout(1500)
    await expect(page.locator('main [role=tablist]')).toHaveCount(0)
    for (const title of [/概览|overview/i, /定义|definition/i, /目标|targets?/i, /执行|executions?/i, /调度|schedules?/i]) {
      await expect(page.locator('main').getByText(title).first()).toBeVisible()
    }
    const cols = await page.evaluate(() => {
      const grids = [...document.querySelectorAll('main dl')].filter((d) => d.querySelector('dt'))
      return Math.max(0, ...grids.map((g) => getComputedStyle(g).gridTemplateColumns.split(' ').length))
    })
    expect(cols).toBeGreaterThanOrEqual(2)
  })

  test('P23-03 执行详情单页且日志区可用', async ({ page }) => {
    await loginViaApi(page)
    const { execId } = await firstIds(page)
    await page.goto(`/executions/${execId}`)
    await page.waitForTimeout(1500)
    await expect(page.locator('main [role=tablist]')).toHaveCount(0)
    const logs = page.locator('#execution-logs')
    await expect(logs).toBeVisible()
    await expect(logs.getByRole('button', { name: /换行|wrap/i })).toBeVisible()
    await expect(logs.getByRole('button', { name: /复制日志|copy logs/i })).toBeVisible()
  })

  test('P23-04 短视口侧边栏全部可见且无需滚动', async ({ page }) => {
    await loginViaApi(page)
    await page.setViewportSize({ width: 1280, height: 640 })
    await page.goto(TEST_URLS.nodes)
    await page.waitForTimeout(1200)
    const short = await page.evaluate(() => {
      const items = [...document.querySelectorAll('[data-slot=sidebar] [data-sidebar=menu-button]')]
        .filter((e) => !e.closest('[data-slot=sidebar-footer]'))
        .map((e) => e.getBoundingClientRect().bottom)
      const footer = document.querySelector('[data-slot=sidebar-footer]')
      const content = document.querySelector('[data-slot=sidebar-content]')
      const labels = [...document.querySelectorAll('[data-slot=sidebar-group-label]')].filter(
        (e) => e.getBoundingClientRect().height > 0
      ).length
      return {
        count: items.length,
        lastBottom: Math.round(Math.max(...items)),
        footerTop: Math.round(footer!.getBoundingClientRect().top),
        scrollable: content!.scrollHeight > content!.clientHeight,
        labels,
      }
    })
    expect(short.count).toBeGreaterThanOrEqual(11)
    expect(short.lastBottom).toBeLessThanOrEqual(short.footerTop)
    expect(short.scrollable).toBe(false)
    expect(short.labels).toBe(0)

    await page.setViewportSize({ width: 1366, height: 768 })
    await page.reload()
    await page.waitForTimeout(1200)
    const normal = await page.evaluate(
      () =>
        [...document.querySelectorAll('[data-slot=sidebar-group-label]')].filter(
          (e) => e.getBoundingClientRect().height > 0
        ).length
    )
    expect(normal).toBeGreaterThan(0)
  })
})
