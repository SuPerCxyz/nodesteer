import { describe, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { ErrorState } from './ui'

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
