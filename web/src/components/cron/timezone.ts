/** 浏览器时区（IANA）；不可用时回落 UTC */
export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

/**
 * 时区是否可用于 Intl 与后端 `time.LoadLocation`。
 * 空串返回 true：Go 的 `time.LoadLocation("")` 等价 UTC。
 */
export function isValidTimeZone(timezone: string): boolean {
  const value = timezone.trim()
  if (!value) return true
  try {
    new Intl.DateTimeFormat('en-CA', { timeZone: value })
    return true
  } catch {
    return false
  }
}

/** 时刻 ISO 字符串 → 指定时区的 `datetime-local` 值；非法输入返回空串 */
export function zonedDateTimeValue(value: string, timezone: string) {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  let parts: Intl.DateTimeFormatPart[]
  try {
    parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone || 'UTC',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(date)
  } catch {
    return ''
  }
  const get = (type: string) =>
    parts.find((part) => part.type === type)?.value || ''
  return `${get('year')}-${get('month')}-${get('day')}T${get('hour')}:${get('minute')}`
}

/** `datetime-local` 值 + 时区 → 时刻 ISO 字符串 */
export function zonedDateTimeToISO(value: string, timezone: string) {
  const [datePart, timePart] = value.split('T')
  const [year, month, day] = datePart.split('-').map(Number)
  const [hour, minute] = timePart.split(':').map(Number)
  const guess = new Date(Date.UTC(year, month - 1, day, hour, minute))
  let parts: Intl.DateTimeFormatPart[]
  try {
    parts = new Intl.DateTimeFormat('en-CA', {
      timeZone: timezone || 'UTC',
      year: 'numeric',
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(guess)
  } catch {
    return guess.toISOString()
  }
  const get = (type: string) =>
    Number(parts.find((part) => part.type === type)?.value || 0)
  const asZonedUTC = Date.UTC(
    get('year'),
    get('month') - 1,
    get('day'),
    get('hour'),
    get('minute'),
    get('second')
  )
  return new Date(
    guess.getTime() - (asZonedUTC - guess.getTime())
  ).toISOString()
}
