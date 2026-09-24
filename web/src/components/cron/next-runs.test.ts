import { describe, expect, it } from 'vitest'
import { NEXT_RUNS_COUNT, nextRuns } from './next-runs'

const NOW = new Date('2026-09-24T00:00:00.000Z')

describe('nextRuns cron', () => {
  it('输出接下来 5 次触发并遵循时区', () => {
    const result = nextRuns({
      type: 'cron',
      expression: '0 9 * * 1-5',
      timezone: 'UTC',
      now: NOW,
    })
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.runs).toHaveLength(NEXT_RUNS_COUNT)
    expect(result.once).toBe(false)
    expect(result.timezone).toBe('UTC')
    expect(result.runs[0]).toBe('2026-09-24T09:00:00.000Z')
    expect(new Date(result.runs[1]).getTime()).toBeGreaterThan(
      new Date(result.runs[0]).getTime()
    )
  })

  it('cron-parser 按 timezone 计算绝对时刻', () => {
    const result = nextRuns({
      type: 'cron',
      expression: '0 9 * * *',
      timezone: 'Asia/Shanghai',
      now: NOW,
    })
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.runs[0]).toBe('2026-09-24T01:00:00.000Z')
  })

  it('支持名称字段与描述符', () => {
    const named = nextRuns({
      type: 'cron',
      expression: '0 9 * * MON-FRI',
      timezone: 'UTC',
      now: NOW,
    })
    expect(named.ok).toBe(true)
    const descriptor = nextRuns({
      type: 'cron',
      expression: '@daily',
      timezone: 'UTC',
      now: NOW,
    })
    expect(descriptor.ok).toBe(true)
    if (descriptor.ok)
      expect(descriptor.runs[0]).toBe('2026-09-25T00:00:00.000Z')
  })

  it('非法表达式返回错误而非时刻', () => {
    const invalid = nextRuns({
      type: 'cron',
      expression: 'foo',
      timezone: 'UTC',
      now: NOW,
    })
    expect(invalid).toMatchObject({ ok: false, reason: 'cronInvalid' })

    const missing = nextRuns({ type: 'cron', timezone: 'UTC', now: NOW })
    expect(missing).toMatchObject({ ok: false, reason: 'cronMissing' })
  })

  it('cron-parser 无法计算的合法表达式降级为 unsupported', () => {
    const result = nextRuns({
      type: 'cron',
      expression: '0 0 30 2 *',
      timezone: 'UTC',
      now: NOW,
    })
    expect(result).toMatchObject({ ok: false, reason: 'unsupported' })
  })
})

describe('nextRuns interval / one_time', () => {
  it('interval 从当前时刻起按秒递推 5 次', () => {
    const result = nextRuns({
      type: 'interval',
      intervalSec: 60,
      timezone: 'UTC',
      now: NOW,
    })
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.runs).toHaveLength(NEXT_RUNS_COUNT)
    expect(result.runs[0]).toBe('2026-09-24T00:01:00.000Z')
    expect(result.runs[4]).toBe('2026-09-24T00:05:00.000Z')
  })

  it('interval < 1 报错', () => {
    expect(
      nextRuns({ type: 'interval', intervalSec: 0, now: NOW })
    ).toMatchObject({ ok: false, reason: 'intervalInvalid' })
    expect(
      nextRuns({ type: 'interval', intervalSec: Number.NaN, now: NOW })
    ).toMatchObject({ ok: false, reason: 'intervalInvalid' })
  })

  it('one_time 只给一个时刻并标记 once', () => {
    const result = nextRuns({
      type: 'one_time',
      runAt: '2026-10-01T02:30:00.000Z',
      timezone: 'UTC',
      now: NOW,
    })
    expect(result.ok).toBe(true)
    if (!result.ok) return
    expect(result.runs).toEqual(['2026-10-01T02:30:00.000Z'])
    expect(result.once).toBe(true)
  })

  it('one_time 未选时刻 / 时刻非法报错', () => {
    expect(
      nextRuns({ type: 'one_time', timezone: 'UTC', now: NOW })
    ).toMatchObject({ ok: false, reason: 'runAtMissing' })
    expect(
      nextRuns({
        type: 'one_time',
        runAt: 'not-a-date',
        timezone: 'UTC',
        now: NOW,
      })
    ).toMatchObject({ ok: false, reason: 'runAtInvalid' })
  })
})

describe('nextRuns 时区', () => {
  it('非法时区报错并回显输入值', () => {
    const result = nextRuns({
      type: 'cron',
      expression: '0 9 * * *',
      timezone: 'Bad/Zone',
      now: NOW,
    })
    expect(result).toMatchObject({
      ok: false,
      reason: 'timezoneInvalid',
      timezone: 'Bad/Zone',
    })
  })

  it('空时区按 UTC 处理（等价 Go time.LoadLocation("")）', () => {
    const result = nextRuns({
      type: 'cron',
      expression: '0 9 * * *',
      timezone: '',
      now: NOW,
    })
    expect(result.ok).toBe(true)
    expect(result.timezone).toBe('UTC')
  })
})
