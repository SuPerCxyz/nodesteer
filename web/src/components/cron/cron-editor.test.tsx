import { useState } from 'react'
import i18n from '@/i18n'
import { beforeAll, describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { CronEditor } from './cron-editor'

/** 受控包装：表达式由父组件（ScheduleEditor）持有 */
function Harness({ initial }: { initial: string }) {
  const [value, setValue] = useState(initial)
  return <CronEditor value={value} onChange={setValue} />
}

function renderEditor(initial: string) {
  // render 返回 Promise，必须 await 后才能解构查询函数
  return Promise.resolve(render(<Harness initial={initial} />))
}

const FIELD_KEYS = ['分', '时', '日', '月', '周'] as const

describe('CronEditor 字段 ↔ 表达式双向同步', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  it('渲染 5 个字段并按初始表达式回填', async () => {
    const { getByLabelText } = await renderEditor('0 9 * * 1-5')

    for (const key of FIELD_KEYS) {
      await expect.element(getByLabelText(key)).toBeVisible()
    }
    await expect.element(getByLabelText('分')).toHaveValue('0')
    await expect.element(getByLabelText('时')).toHaveValue('9')
    await expect.element(getByLabelText('日')).toHaveValue('*')
    await expect.element(getByLabelText('月')).toHaveValue('*')
    await expect.element(getByLabelText('周')).toHaveValue('1-5')
    await expect
      .element(getByLabelText('Cron 表达式'))
      .toHaveValue('0 9 * * 1-5')
  })

  it('手改合法表达式 → 立即回填 5 个字段', async () => {
    const { getByLabelText } = await renderEditor('')

    await userEvent.fill(getByLabelText('Cron 表达式'), '*/15 0,12 1-15 * 1-5')

    await expect.element(getByLabelText('分')).toHaveValue('*/15')
    await expect.element(getByLabelText('时')).toHaveValue('0,12')
    await expect.element(getByLabelText('日')).toHaveValue('1-15')
    await expect.element(getByLabelText('月')).toHaveValue('*')
    await expect.element(getByLabelText('周')).toHaveValue('1-5')
  })

  it('手改非法表达式 → 标红、不回填、不清空用户输入', async () => {
    const { getByLabelText, getByText } = await renderEditor('0 9 * * 1-5')
    const expression = getByLabelText('Cron 表达式')

    await userEvent.fill(expression, 'foo')

    await expect.element(expression).toHaveValue('foo')
    await expect.element(expression).toHaveAttribute('aria-invalid', 'true')
    await expect.element(getByText('Cron 表达式无效')).toBeVisible()
    // 字段保留上一次合法表达式的值，不被非法输入清空
    await expect.element(getByLabelText('分')).toHaveValue('0')
    await expect.element(getByLabelText('时')).toHaveValue('9')
    await expect.element(getByLabelText('周')).toHaveValue('1-5')
  })

  it('改字段 → 立即重新生成表达式', async () => {
    const { getByLabelText } = await renderEditor('0 9 * * 1-5')

    await userEvent.fill(getByLabelText('分'), '30')

    await expect
      .element(getByLabelText('Cron 表达式'))
      .toHaveValue('30 9 * * 1-5')
  })

  it('字段越界时字段与表达式同时标红', async () => {
    const { getByLabelText, getByText } = await renderEditor('0 9 * * 1-5')

    await userEvent.fill(getByLabelText('分'), '99')

    await expect
      .element(getByLabelText('Cron 表达式'))
      .toHaveValue('99 9 * * 1-5')
    await expect
      .element(getByLabelText('分'))
      .toHaveAttribute('aria-invalid', 'true')
    await expect.element(getByText('Cron 表达式无效')).toBeVisible()
  })

  it('描述符表达式保留手输能力并禁用字段，避免重新生成丢失前缀', async () => {
    const { getByLabelText, getByText } = await renderEditor('@daily')

    await expect
      .element(getByText('该表达式不支持图形化编辑，可继续手动输入。'))
      .toBeVisible()
    await expect.element(getByLabelText('分')).toBeDisabled()
    await expect
      .element(getByLabelText('Cron 表达式'))
      .not.toHaveAttribute('aria-invalid')
  })

  it('预设下拉按当前表达式回显，套用预设同步字段与表达式', async () => {
    const { getByLabelText, getByRole } = await renderEditor('0 * * * *')

    const preset = getByRole('combobox', { name: '常用预设' })
    await expect.element(preset).toHaveTextContent('每小时整点')

    await userEvent.click(preset)
    await userEvent.click(getByRole('option', { name: '工作日 09:00' }))

    await expect
      .element(getByLabelText('Cron 表达式'))
      .toHaveValue('0 9 * * 1-5')
    await expect.element(getByLabelText('周')).toHaveValue('1-5')
  })

  it('未知表达式显示为自定义预设', async () => {
    const { getByRole } = await renderEditor('7 8 9 10 *')

    await expect
      .element(getByRole('combobox', { name: '常用预设' }))
      .toHaveTextContent('自定义')
  })
})
