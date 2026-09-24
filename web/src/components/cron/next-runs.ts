import { CronExpressionParser } from 'cron-parser'
import { isValidCronExpression, normalizeCronNames } from './cron-fields'
import { isValidTimeZone } from './timezone'

export type ScheduleKind = 'cron' | 'interval' | 'one_time'

/** 预览组件支持的类型：on_start 无触发时刻，仅展示说明文案 */
export type PreviewKind = ScheduleKind | 'on_start'

export const NEXT_RUNS_COUNT = 5

export type NextRunsReason =
  | 'cronMissing'
  | 'cronInvalid'
  | 'intervalInvalid'
  | 'runAtMissing'
  | 'runAtInvalid'
  | 'timezoneInvalid'
  | 'unsupported'

export type NextRunsResult =
  | { ok: true; runs: string[]; timezone: string; once: boolean }
  | { ok: false; reason: NextRunsReason; timezone: string }

export interface NextRunsInput {
  type: ScheduleKind
  expression?: string
  intervalSec?: number
  runAt?: string
  timezone?: string
  /** 注入当前时刻，便于测试 */
  now?: Date
}

/**
 * 计算接下来最多 5 次触发时刻（ISO 字符串）。
 * - cron：cron-parser 支持时区参数；表达式非法/暂不支持时返回原因而非时刻
 * - interval：从 now 起每 intervalSec 秒递推
 * - one_time：仅一个时刻（once=true）
 */
export function nextRuns(input: NextRunsInput): NextRunsResult {
  const timezone = (input.timezone || '').trim() || 'UTC'
  if (!isValidTimeZone(timezone)) {
    return { ok: false, reason: 'timezoneInvalid', timezone }
  }
  const now = input.now ?? new Date()

  if (input.type === 'interval') {
    const seconds = input.intervalSec ?? 0
    if (!Number.isFinite(seconds) || seconds < 1) {
      return { ok: false, reason: 'intervalInvalid', timezone }
    }
    const base = now.getTime()
    const runs = Array.from({ length: NEXT_RUNS_COUNT }, (_, index) =>
      new Date(base + seconds * 1000 * (index + 1)).toISOString()
    )
    return { ok: true, runs, timezone, once: false }
  }

  if (input.type === 'one_time') {
    const runAt = (input.runAt || '').trim()
    if (!runAt) return { ok: false, reason: 'runAtMissing', timezone }
    const date = new Date(runAt)
    if (Number.isNaN(date.getTime())) {
      return { ok: false, reason: 'runAtInvalid', timezone }
    }
    return { ok: true, runs: [date.toISOString()], timezone, once: true }
  }

  const expression = (input.expression || '').trim()
  if (!expression) return { ok: false, reason: 'cronMissing', timezone }
  if (!isValidCronExpression(expression)) {
    return { ok: false, reason: 'cronInvalid', timezone }
  }
  // 名称字段先归一为数字；描述符/时区前缀原样交给 cron-parser，失败则降级提示
  const candidate = normalizeCronNames(expression) ?? expression
  let runs: string[]
  try {
    const interval = CronExpressionParser.parse(candidate, {
      currentDate: now,
      tz: timezone,
    })
    runs = interval
      .take(NEXT_RUNS_COUNT)
      .map((date) => date.toISOString())
      .filter((iso): iso is string => typeof iso === 'string')
  } catch {
    return { ok: false, reason: 'unsupported', timezone }
  }
  if (!runs.length) return { ok: false, reason: 'unsupported', timezone }
  return { ok: true, runs, timezone, once: false }
}
