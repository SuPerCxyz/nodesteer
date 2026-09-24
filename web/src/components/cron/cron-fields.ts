/**
 * 轻量 5 段 Cron 字段语法（分 时 日 月 周）。
 *
 * 边界与取值口径对齐后端 `github.com/robfig/cron/v3` 的 `cron.ParseStandard`
 * （实测 robfig v3.0.1）：
 * - 分 0-59、时 0-23、日 1-31、月 1-12、周 0-6（robfig 明确拒绝 7）
 * - 支持 `*`、`?`、单值、列表 `1,15`、区间 `1-5`、步进（`*` 后接 `/N`）
 * - 月/周名称（jan-dec、sun-sat）
 * - 可选 `TZ=` / `CRON_TZ=` 前缀与 `@yearly` 等描述符
 */

export interface CronFieldSpec {
  key: 'minute' | 'hour' | 'dayOfMonth' | 'month' | 'dayOfWeek'
  min: number
  max: number
  /** robfig 解析器接受的名称（小写），仅月份与星期字段存在 */
  names?: Record<string, number>
}

const MONTH_NAMES: Record<string, number> = {
  jan: 1,
  feb: 2,
  mar: 3,
  apr: 4,
  may: 5,
  jun: 6,
  jul: 7,
  aug: 8,
  sep: 9,
  oct: 10,
  nov: 11,
  dec: 12,
}

const DOW_NAMES: Record<string, number> = {
  sun: 0,
  mon: 1,
  tue: 2,
  wed: 3,
  thu: 4,
  fri: 5,
  sat: 6,
}

export const CRON_FIELD_SPECS: readonly CronFieldSpec[] = [
  { key: 'minute', min: 0, max: 59 },
  { key: 'hour', min: 0, max: 23 },
  { key: 'dayOfMonth', min: 1, max: 31 },
  { key: 'month', min: 1, max: 12, names: MONTH_NAMES },
  { key: 'dayOfWeek', min: 0, max: 6, names: DOW_NAMES },
]

/** robfig `ParseStandard` 支持的固定描述符（不含 `@reboot`） */
const DESCRIPTORS = [
  '@yearly',
  '@annually',
  '@monthly',
  '@weekly',
  '@daily',
  '@midnight',
  '@hourly',
]

const EVERY_UNIT = /^(?:\d+(?:\.\d+)?(?:ns|us|µs|ms|s|m|h))+$/

/** robfig 支持的 `TZ=` / `CRON_TZ=` 时区前缀 */
const TZ_PREFIX = /^(?:CRON_TZ|TZ)=\S+\s+/

/** 常用预设；id 同时用作 `schedules.cronEdit.presets.<id>` 文案键 */
export const CRON_PRESETS: readonly { id: string; expression: string }[] = [
  { id: 'hourly', expression: '0 * * * *' },
  { id: 'every15min', expression: '*/15 * * * *' },
  { id: 'daily', expression: '0 0 * * *' },
  { id: 'noon', expression: '0 12 * * *' },
  { id: 'weekday9', expression: '0 9 * * 1-5' },
  { id: 'monday9', expression: '0 9 * * 1' },
  { id: 'monthlyFirst', expression: '0 0 1 * *' },
  { id: 'monthly15', expression: '0 6 15 * *' },
]

/** 把名称或纯数字文本解析为字段内取值；越界或非法返回 null */
function resolveValue(token: string, spec: CronFieldSpec): number | null {
  const text = token.toLowerCase()
  if (!text) return null
  if (spec.names && text in spec.names) return spec.names[text]
  if (!/^\d+$/.test(text)) return null
  const value = Number(text)
  return value >= spec.min && value <= spec.max ? value : null
}

/** 校验逗号分隔后的单个片段：`*` / `?` / 数值 / 区间 / 步进 */
function validPart(part: string, spec: CronFieldSpec): boolean {
  const text = part.trim()
  if (!text || /\s/.test(text)) return false

  const slash = text.split('/')
  if (slash.length > 2) return false
  if (slash.length === 2) {
    const step = slash[1].trim()
    if (!/^\d+$/.test(step) || Number(step) < 1) return false
  }

  const base = slash[0].trim()
  if (base === '*' || base === '?') return true

  const bounds = base.split('-')
  if (bounds.length > 2) return false
  const start = resolveValue(bounds[0], spec)
  if (start === null) return false
  if (bounds.length === 1) return true
  const end = resolveValue(bounds[1], spec)
  if (end === null) return false
  return start <= end
}

/** 单个字段是否合法（空字段、越界、步进 ≤ 0、区间倒置均非法） */
export function validateCronField(raw: string, spec: CronFieldSpec): boolean {
  const text = raw.trim()
  if (!text || /\s/.test(text)) return false
  return text.split(',').every((part) => validPart(part, spec))
}

function isDescriptor(text: string): boolean {
  if (DESCRIPTORS.includes(text)) return true
  if (!text.startsWith('@every')) return false
  const rest = text.slice('@every'.length).trim()
  if (!EVERY_UNIT.test(rest)) return false
  return /[1-9]/.test(rest)
}

function tokensOf(expression: string): string[] {
  return expression.trim().replace(TZ_PREFIX, '').trim().split(/\s+/)
}

/**
 * 表达式是否可提交：5 段字段全部合法，或为描述符 / 带时区前缀的合法表达式。
 * 空表达式、4 段、6 段、步进为 0、越界值一律非法。
 */
export function isValidCronExpression(expression: string): boolean {
  const trimmed = expression.trim()
  if (!trimmed) return false
  if (trimmed.startsWith('@')) return isDescriptor(trimmed)
  const tokens = tokensOf(trimmed)
  if (tokens.length !== 5 || tokens[0] === '') return false
  return tokens.every((token, index) =>
    validateCronField(token, CRON_FIELD_SPECS[index])
  )
}

/**
 * 表达式 → 5 个字段原始文本；不可图形化编辑时返回 null。
 * 说明：含 `TZ=` / `CRON_TZ=` 前缀或描述符时返回 null，避免重新生成表达式时丢掉前缀。
 */
export function parseCronToFields(expression: string): string[] | null {
  const trimmed = expression.trim()
  if (!trimmed || TZ_PREFIX.test(trimmed)) return null
  const tokens = trimmed.split(/\s+/)
  if (tokens.length !== 5) return null
  const ok = tokens.every((token, index) =>
    validateCronField(token, CRON_FIELD_SPECS[index])
  )
  return ok ? tokens : null
}

/** 5 个字段 → 表达式（单一空格分隔） */
export function joinFields(fields: readonly string[]): string {
  return fields.map((field) => field.trim()).join(' ')
}

/** 与当前表达式等价的预设 id；无匹配返回 null */
export function matchPreset(expression: string): string | null {
  const normalized = expression.trim().replace(/\s+/g, ' ')
  return (
    CRON_PRESETS.find((preset) => preset.expression === normalized)?.id ?? null
  )
}

/**
 * 把月/周名称替换为数字，便于 cron-parser 计算下次触发；
 * 非 5 段表达式（描述符、时区前缀）原样返回 null，由调用方决定回退。
 */
export function normalizeCronNames(expression: string): string | null {
  const trimmed = expression.trim()
  if (!trimmed || TZ_PREFIX.test(trimmed)) return null
  const tokens = trimmed.split(/\s+/)
  if (tokens.length !== 5) return null
  return tokens
    .map((token, index) => {
      const names = CRON_FIELD_SPECS[index].names
      if (!names) return token
      return token.replace(/[a-z]+/gi, (word) => {
        const value = names[word.toLowerCase()]
        return value === undefined ? word : String(value)
      })
    })
    .join(' ')
}
