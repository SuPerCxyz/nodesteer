import { describe, expect, it } from 'vitest'
import { historyPointValid, historySegments } from './history'

describe('monitoring history', () => {
  it('keeps zero values and breaks missing samples and reported gaps', () => {
    const points = [
      { timestamp: '2026-01-01T00:00:00Z', value: 0, quality: 1 },
      { timestamp: '2026-01-01T00:01:00Z', value: 1, quality: 1 },
      { timestamp: '2026-01-01T00:02:00Z', value: null, quality: 2 },
      { timestamp: '2026-01-01T00:03:00Z', value: 2, quality: 1 },
      { timestamp: '2026-01-01T00:08:00Z', value: 3, quality: 1 },
    ]
    const segments = historySegments(points, [], 60_000_000_000)
    expect(segments.map((part) => part.length)).toEqual([2, 1, 1])
    expect(segments[0][0]).toMatch(/^8,/)
    const explicit = historySegments(
      points.slice(0, 2),
      [{ start: '2026-01-01T00:00:10Z', end: '2026-01-01T00:00:20Z' }],
      60_000_000_000
    )
    expect(explicit.map((part) => part.length)).toEqual([1, 1])
  })
  it('breaks the chart at a non-valid quality point with a numeric value', () => {
    const points = [
      { timestamp: '2026-01-01T00:00:00Z', value: 0, quality: 1 },
      { timestamp: '2026-01-01T00:01:00Z', value: 9, quality: 17 },
      { timestamp: '2026-01-01T00:02:00Z', value: 1, quality: 1 },
    ]
    expect(historySegments(points, [], 60_000_000_000).map((segment) => segment.length)).toEqual([1, 1])
  })
  it('keeps rollup buckets with a positive count in the chart', () => {
    const points = [
      { timestamp: '2026-01-01T00:00:00Z', value: 1, quality: 0, count: 5 },
      { timestamp: '2026-01-01T00:01:00Z', value: 2, quality: 0, count: 5 },
    ]
    expect(
      historySegments(points, [], 60_000_000_000).map((part) => part.length)
    ).toEqual([2])
  })
  it('breaks the chart at an empty rollup bucket', () => {
    const points = [
      { timestamp: '2026-01-01T00:00:00Z', value: 1, quality: 0, count: 5 },
      { timestamp: '2026-01-01T00:01:00Z', value: null, quality: 0, count: 0 },
      { timestamp: '2026-01-01T00:02:00Z', value: 2, quality: 0, count: 5 },
    ]
    expect(
      historySegments(points, [], 60_000_000_000).map((part) => part.length)
    ).toEqual([1, 1])
  })
  it('excludes quality 0 points with no samples even when the value is numeric', () => {
    const points = [
      { timestamp: '2026-01-01T00:00:00Z', value: 1, quality: 0, count: 5 },
      { timestamp: '2026-01-01T00:01:00Z', value: 9, quality: 0, count: 0 },
      { timestamp: '2026-01-01T00:02:00Z', value: 2, quality: 0, count: 5 },
    ]
    expect(
      historySegments(points, [], 60_000_000_000).map((part) => part.length)
    ).toEqual([1, 1])
  })
  it('keeps raw refinement points continuous with rollup buckets', () => {
    const points = [
      { timestamp: '2026-01-01T00:00:00Z', value: 1, quality: 0, count: 5 },
      { timestamp: '2026-01-01T00:01:00Z', value: 2, quality: 1 },
      { timestamp: '2026-01-01T00:02:00Z', value: 3, quality: 0, count: 5 },
    ]
    expect(
      historySegments(points, [], 60_000_000_000).map((part) => part.length)
    ).toEqual([3])
  })
  it('classifies raw, rollup and empty points consistently', () => {
    const timestamp = '2026-01-01T00:00:00Z'
    expect(historyPointValid({ timestamp, value: 1, quality: 1 })).toBe(true)
    expect(
      historyPointValid({ timestamp, value: 1, quality: 0, count: 5 })
    ).toBe(true)
    expect(historyPointValid({ timestamp, value: 1, count: 2 })).toBe(true)
    expect(historyPointValid({ timestamp, value: 1 })).toBe(false)
    expect(
      historyPointValid({ timestamp, value: null, quality: 0, count: 0 })
    ).toBe(false)
    expect(
      historyPointValid({ timestamp, value: 9, quality: 0, count: 0 })
    ).toBe(false)
  })
})
