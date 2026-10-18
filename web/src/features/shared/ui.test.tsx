import { describe, expect, it, beforeAll } from 'vitest'
import { render } from 'vitest-browser-react'
import i18n from '@/i18n'
import { statusLabel } from '@/lib/status-labels'
import { ErrorState, StatusBadge } from './ui'

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
