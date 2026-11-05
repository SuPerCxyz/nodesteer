import { useEffect, useMemo, useState } from 'react'

export type PresetHistoryRange = '1h' | '3h' | '6h' | '12h' | '24h' | '7d'
export type HistoryRange = PresetHistoryRange | 'custom'
export type HistoryRangeError = 'missing' | 'invalid' | 'max'

export const HISTORY_RANGE_MS: Record<PresetHistoryRange, number> = {
  '1h': 60 * 60 * 1000,
  '3h': 3 * 60 * 60 * 1000,
  '6h': 6 * 60 * 60 * 1000,
  '12h': 12 * 60 * 60 * 1000,
  '24h': 24 * 60 * 60 * 1000,
  '7d': 7 * 24 * 60 * 60 * 1000,
}

export const MAX_CUSTOM_RANGE_MS = 30 * 24 * 60 * 60 * 1000

export type HistoryRangeWindow = {
  start: Date | null
  end: Date | null
  valid: boolean
  error?: HistoryRangeError
}

export function toDatetimeLocal(date: Date) {
  const offset = date.getTimezoneOffset() * 60000
  return new Date(date.getTime() - offset).toISOString().slice(0, 16)
}

function parseDatetimeLocal(value: string) {
  if (!value) return null
  const date = new Date(value)
  return Number.isFinite(date.getTime()) ? date : null
}

export function resolveHistoryWindow(
  range: HistoryRange,
  customStart: string,
  customEnd: string,
  now = new Date()
): HistoryRangeWindow {
  if (range !== 'custom') {
    return {
      start: new Date(now.getTime() - HISTORY_RANGE_MS[range]),
      end: now,
      valid: true,
    }
  }
  const start = parseDatetimeLocal(customStart)
  const end = parseDatetimeLocal(customEnd)
  if (!start || !end) {
    return { start, end, valid: false, error: 'missing' }
  }
  if (start.getTime() >= end.getTime()) {
    return { start, end, valid: false, error: 'invalid' }
  }
  if (end.getTime() - start.getTime() > MAX_CUSTOM_RANGE_MS) {
    return { start, end, valid: false, error: 'max' }
  }
  return { start, end, valid: true }
}

export function useHistoryRange() {
  const [range, setRangeState] = useState<HistoryRange>('1h')
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    if (range === 'custom') return
    const timer = setInterval(() => setNow(Date.now()), 30_000)
    return () => clearInterval(timer)
  }, [range])
  const [customStart, setCustomStart] = useState(() => {
    const end = new Date()
    return toDatetimeLocal(new Date(end.getTime() - HISTORY_RANGE_MS['1h']))
  })
  const [customEnd, setCustomEnd] = useState(() => toDatetimeLocal(new Date()))
  const setRange = (next: HistoryRange) => {
    setNow(Date.now())
    setRangeState(next)
  }
  const window = useMemo(
    () => resolveHistoryWindow(range, customStart, customEnd, new Date(now)),
    [range, customStart, customEnd, now]
  )
  return {
    range,
    setRange,
    customStart,
    setCustomStart,
    customEnd,
    setCustomEnd,
    window,
  }
}
