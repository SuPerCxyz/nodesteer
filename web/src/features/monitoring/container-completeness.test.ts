import { describe, expect, it } from 'vitest'
import {
  containerCompleteness,
  type LatestMetric,
} from './container-completeness'

const now = '2026-01-01T00:00:00Z'
function triple(
  discovered: number,
  sampled: number,
  limit: number
): LatestMetric[] {
  return ['discovered_count', 'sampled_count', 'limit'].map((name, index) => {
    const metric = `container.collection.${name}`
    return {
      metric,
      series: metric,
      value: [discovered, sampled, limit][index],
      state: 'ready',
      observed_at: now,
    }
  })
}
describe('container completeness', () => {
  it('distinguishes empty, complete and at least 101 with partial sampling', () => {
    expect(containerCompleteness(triple(0, 0, 100))?.partial).toBe(false)
    expect(containerCompleteness(triple(100, 100, 100))?.partial).toBe(false)
    expect(containerCompleteness(triple(101, 99, 100))?.partial).toBe(true)
  })
  it('returns unknown for missing, stale, mismatched, future or invalid counts', () => {
    const missing = triple(1, 1, 100).slice(0, 2)
    const stale = triple(1, 1, 100)
    stale[0].state = 'data_gap'
    const mismatch = triple(1, 1, 100)
    mismatch[1].observed_at = '2026-01-01T00:00:01Z'
    const newer = triple(1, 1, 100)
    newer.push({
      metric: 'cpu.usage',
      series: 'cpu.usage',
      value: 1,
      state: 'ready',
      observed_at: '2026-01-01T00:00:01Z',
    })
    const future = triple(1, 1, 100)
    future.forEach((item) => {
      item.observed_at = '2999-01-01T00:00:00Z'
    })
    for (const values of [
      missing,
      stale,
      mismatch,
      newer,
      future,
      triple(1, 2, 100),
      triple(1, 1, 0),
      triple(1, NaN, 100),
      triple(1.5, 1, 100),
    ])
      expect(containerCompleteness(values)).toBeNull()
  })
})
