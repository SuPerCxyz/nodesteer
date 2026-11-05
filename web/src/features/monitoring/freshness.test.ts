import { expect, it } from 'vitest'
import { observationIsStale } from './freshness'

it('uses node metric maximum age instead of twice the interval', () => {
  const now = Date.parse('2026-01-01T00:01:00Z')
  const observed = '2026-01-01T00:00:35Z'
  expect(
    observationIsStale(
      { kind: 'node_metric', interval_sec: 30, max_age_sec: 20 },
      observed,
      now
    )
  ).toBe(true)
  expect(
    observationIsStale(
      { kind: 'node_metric', interval_sec: 10, max_age_sec: 180 },
      observed,
      now
    )
  ).toBe(false)
  expect(
    observationIsStale(
      { kind: 'node_metric', interval_sec: 300 },
      '2025-12-31T23:55:59Z',
      now
    )
  ).toBe(true)
  expect(observationIsStale({ interval_sec: 10 }, observed, now)).toBe(true)
  expect(observationIsStale({ interval_sec: 10 }, 'invalid', now)).toBe(true)
  expect(
    observationIsStale({ interval_sec: 10 }, '2026-01-01T00:01:01Z', now)
  ).toBe(true)
})
