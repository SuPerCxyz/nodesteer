import { afterEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { HISTORY_REFRESH_MS, useHistoryRange } from './history-range'

function Probe() {
  const history = useHistoryRange()
  return (
    <div>
      <span data-testid='start'>{history.window.start?.toISOString()}</span>
      <span data-testid='range'>{history.range}</span>
      <button type='button' onClick={() => history.setRange('6h')}>
        switch
      </button>
    </div>
  )
}

afterEach(() => {
  vi.useRealTimers()
})

describe('history range window', () => {
  it('keeps start/end stable while time passes and refreshes only every 5 minutes', async () => {
    vi.useFakeTimers()
    const page = await render(<Probe />)
    const start = page.getByTestId('start').element().textContent
    vi.advanceTimersByTime(30_000)
    expect(page.getByTestId('start').element().textContent).toBe(start)
    expect(HISTORY_REFRESH_MS).toBe(5 * 60 * 1000)

    await userEvent.click(page.getByRole('button', { name: 'switch' }))
    await expect
      .poll(() => page.getByTestId('range').element().textContent)
      .toBe('6h')
    expect(page.getByTestId('start').element().textContent).not.toBe(start)
  })
})
