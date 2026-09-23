import i18n from '@/i18n'

/**
 * 后端错误可读化映射。
 *
 * API 会返回英文原文（且常带裸 UUID），这里把已知模式转换为本地化文案；
 * 未识别的错误原样返回，不丢失信息（ApiError.rawMessage 仍保留原文）。
 *
 * 已覆盖模式：
 * - 引用阻断：`artifact <uuid> is referenced by application <uuid>`（script/task/group/node/application/schedule 同理）
 * - Cron 字段数错误：`invalid cron expression: expected exactly 5 fields, found 6: ...`
 * - Cron 其他解析错误：`invalid cron expression: <detail>`
 * - 时区错误：`invalid timezone: unknown time zone <zone>`
 * - 节点仍有活动传输：`node <id> has active file transfer <id>`
 * - 唯一约束（重复用户名等）：`UNIQUE constraint failed: users.username`
 * - 资源不存在：`node not found` / `task not found`（带资源类型）
 * - 通用 404/403：`not found`、`permission denied`
 */

const UUID =
  '[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}'

const REFERENCE_PATTERN = new RegExp(
  `^([a-z_]+)\\s+(${UUID})\\s+is\\s+referenced\\s+by\\s+([a-z_]+)\\s+(${UUID})`,
  'i'
)
const ACTIVE_TRANSFER_PATTERN = new RegExp(
  `^node\\s+(\\S+)\\s+has\\s+active\\s+file\\s+transfer\\s+(\\S+)`,
  'i'
)
const CRON_FIELDS_PATTERN =
  /^invalid cron expression:\s*expected exactly (\d+) fields?,?\s*found (\d+)/i
const CRON_INVALID_PATTERN = /^invalid cron expression:\s*(.+)$/i
const TIMEZONE_PATTERN = /^invalid timezone:\s*(?:unknown time zone\s*)?(.+)$/i
const UNIQUE_PATTERN = /^unique constraint failed:\s*([\w.]+)/i
const DUPLICATE_NAME_PATTERN = /^([a-z_]+) name already exists:\s*(.+)$/i
const PARAMETER_NAME_REQUIRED_PATTERN = /^parameter name is required$/i
const DUPLICATE_PARAMETER_PATTERN = /^duplicate parameter name:\s*(.+)$/i
const NAME_REQUIRED_PATTERN = /^([a-z_]+) name is required$/i
const RESOURCE_NOT_FOUND_PATTERN = /^([a-z_]+) not found$/i
const NOT_FOUND_PATTERN = /^not found$/i
const PERMISSION_DENIED_PATTERN = /^permission denied$/i

function translate(key: string, options?: Record<string, unknown>): string {
  const value = i18n.t(key, options)
  return typeof value === 'string' ? value : key
}

/** 资源类型本地化名称；未知类型回退为原始英文单词。 */
function typeLabel(type: string): string {
  return translate(`errors.types.${type.toLowerCase()}`, {
    defaultValue: type,
  })
}

function localize(raw: string, status?: number): string | null {
  const reference = REFERENCE_PATTERN.exec(raw)
  if (reference) {
    return translate('errors.referenced', {
      resource: typeLabel(reference[1]),
      reference: translate('errors.referenceLabel', {
        type: typeLabel(reference[3]),
        id: reference[4],
      }),
    })
  }
  const transfer = ACTIVE_TRANSFER_PATTERN.exec(raw)
  if (transfer) {
    return translate('errors.activeTransfer', {
      node: transfer[1],
      transfer: transfer[2],
    })
  }
  const cronFields = CRON_FIELDS_PATTERN.exec(raw)
  if (cronFields) {
    return translate('errors.cronFields', {
      expected: cronFields[1],
      found: cronFields[2],
    })
  }
  const cron = CRON_INVALID_PATTERN.exec(raw)
  if (cron) {
    return translate('errors.cronInvalid', { detail: cron[1] })
  }
  const timezone = TIMEZONE_PATTERN.exec(raw)
  if (timezone) {
    return translate('errors.invalidTimezone', { detail: timezone[1] })
  }
  const unique = UNIQUE_PATTERN.exec(raw)
  if (unique) {
    const field = unique[1].toLowerCase()
    if (field === 'users.username' || field === 'username') {
      return translate('errors.duplicateUsername')
    }
    return translate('errors.uniqueConstraint', { field: unique[1] })
  }
  if (status === 404 && NOT_FOUND_PATTERN.test(raw)) {
    return translate('errors.notFound')
  }
  const duplicateName = DUPLICATE_NAME_PATTERN.exec(raw)
  if (duplicateName) {
    return translate('errors.duplicateName', {
      type: typeLabel(duplicateName[1]),
      name: duplicateName[2],
    })
  }
  if (PARAMETER_NAME_REQUIRED_PATTERN.test(raw)) {
    return translate('errors.parameterNameRequired')
  }
  const duplicateParameter = DUPLICATE_PARAMETER_PATTERN.exec(raw)
  if (duplicateParameter) {
    return translate('errors.duplicateParameter', {
      name: duplicateParameter[1],
    })
  }
  const nameRequired = NAME_REQUIRED_PATTERN.exec(raw)
  if (nameRequired) {
    return translate('errors.nameRequired', {
      type: typeLabel(nameRequired[1]),
    })
  }
  const resourceNotFound = RESOURCE_NOT_FOUND_PATTERN.exec(raw)
  if (resourceNotFound) {
    return translate('errors.notFoundResource', {
      resource: typeLabel(resourceNotFound[1]),
    })
  }
  if (status === 403 && PERMISSION_DENIED_PATTERN.test(raw)) {
    return translate('errors.permissionDenied')
  }
  return null
}

function errorText(error: unknown): string {
  if (typeof error === 'string') return error
  if (error instanceof Error) return error.message
  if (error && typeof error === 'object') {
    const record = error as Record<string, unknown>
    if (typeof record.error === 'string') return record.error
    if (typeof record.message === 'string') return record.message
    if (typeof record.detail === 'string') return record.detail
  }
  return ''
}

function errorStatus(error: unknown): number | undefined {
  if (!error || typeof error !== 'object') return undefined
  const status = (error as { status?: unknown }).status
  return typeof status === 'number' ? status : undefined
}

/**
 * 将后端错误信息转换为可读文案。
 * 命中已知模式时返回本地化消息；否则返回原始文本（可能是空字符串）。
 */
export function formatApiError(error: unknown, status?: number): string {
  const text = errorText(error).trim()
  if (!text) return ''
  const httpStatus = status ?? errorStatus(error)
  return localize(text, httpStatus) ?? errorText(error)
}
