import i18n from '@/i18n'
import { beforeAll, expect, it } from 'vitest'
import { render } from 'vitest-browser-react'
import { ObservationState } from './observation-state'

beforeAll(async () => {
  await i18n.changeLanguage('zh')
})
it('renders node metrics using the configured max age in both directions', async () => {
  const now = Date.parse('2026-01-01T00:01:00Z')
  const observation = {
    observed_at: '2026-01-01T00:00:35Z',
    status_code: 0,
    value: 95,
  }
  const page = await render(
    <>
      <ObservationState
        target={{
          kind: 'node_metric',
          interval_sec: 30,
          max_age_sec: 20,
          enabled: true,
        }}
        observation={observation}
        error={false}
        now={now}
      />
      <ObservationState
        target={{
          kind: 'node_metric',
          interval_sec: 10,
          max_age_sec: 180,
          enabled: true,
        }}
        observation={observation}
        error={false}
        now={now}
      />
    </>
  )
  await expect.element(page.getByText('数据已过期')).toBeVisible()
  await expect.element(page.getByText('95')).toBeVisible()
})
