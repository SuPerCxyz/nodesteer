import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  CRON_FIELD_SPECS,
  CRON_PRESETS,
  isValidCronExpression,
  joinFields,
  matchPreset,
  parseCronToFields,
  validateCronField,
} from './cron-fields'

const CUSTOM_PRESET = 'custom'

/**
 * 图形化 5 段 Cron 编辑器：字段 ↔ 表达式双向同步。
 * - 手改表达式：合法则回填字段；非法则标红且不回填、不清空用户输入
 * - 改字段：立即重新生成表达式
 * - 有效但无法图形化（描述符 / TZ 前缀）时禁用字段，避免重新生成丢掉前缀
 */
export function CronEditor({
  value,
  onChange,
}: {
  value: string
  onChange: (expression: string) => void
}) {
  const { t } = useTranslation()
  const [fields, setFields] = useState<string[]>(() => {
    const parsed = parseCronToFields(value)
    return parsed || CRON_FIELD_SPECS.map(() => '')
  })

  // 外部 value 变化（初始化 / 预设 / 其他来源）时回填字段；
  // 非法表达式保持既有字段，不清空用户在表达式框里的输入。
  useEffect(() => {
    const parsed = parseCronToFields(value)
    if (parsed)
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setFields(parsed)
  }, [value])

  const valid = isValidCronExpression(value)
  const graphical = parseCronToFields(value) !== null
  const nonGraphical = valid && !graphical
  const hasContent =
    value.trim() !== '' || fields.some((field) => field.trim() !== '')
  const showFieldErrors = hasContent && !nonGraphical
  const presetId = matchPreset(value) || CUSTOM_PRESET

  const setField = (index: number, next: string) => {
    const updated = fields.map((field, i) => (i === index ? next : field))
    setFields(updated)
    onChange(joinFields(updated))
  }

  const applyPreset = (id: string) => {
    const preset = CRON_PRESETS.find((item) => item.id === id)
    if (!preset) return
    const parsed = parseCronToFields(preset.expression)
    if (parsed) setFields(parsed)
    onChange(preset.expression)
  }

  return (
    <div className='grid gap-3'>
      {/* 不用 <label> 包裹 Radix Select：包一层会产生与字段重名的可访问标签 */}
      <div className='grid gap-2 text-sm sm:max-w-xs'>
        <span className='font-medium'>{t('schedules.cronEdit.preset')}</span>
        <Select value={presetId} onValueChange={applyPreset}>
          <SelectTrigger
            className='w-full'
            aria-label={t('schedules.cronEdit.preset')}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={CUSTOM_PRESET}>
              {t('schedules.cronEdit.custom')}
            </SelectItem>
            {CRON_PRESETS.map((preset) => (
              <SelectItem key={preset.id} value={preset.id}>
                {t(`schedules.cronEdit.presets.${preset.id}`)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div className='grid gap-3 sm:grid-cols-5'>
        {CRON_FIELD_SPECS.map((spec, index) => {
          const field = fields[index] ?? ''
          const fieldValid = validateCronField(field, spec)
          return (
            <label className='grid gap-1.5 text-sm font-medium' key={spec.key}>
              {t(`schedules.cronEdit.fields.${spec.key}`)}
              <Input
                className='font-mono text-xs'
                value={field}
                disabled={nonGraphical}
                aria-label={t(`schedules.cronEdit.fields.${spec.key}`)}
                aria-invalid={(showFieldErrors && !fieldValid) || undefined}
                onChange={(event) => setField(index, event.target.value)}
              />
              <span className='font-mono text-[11px] font-normal text-muted-foreground'>
                {spec.min}-{spec.max}
              </span>
            </label>
          )
        })}
      </div>

      <label className='grid gap-2 text-sm font-medium'>
        {t('schedules.cronExpression')}
        <Input
          className='font-mono text-xs'
          value={value}
          aria-label={t('schedules.cronExpression')}
          aria-invalid={!valid || undefined}
          onChange={(event) => onChange(event.target.value)}
        />
      </label>

      {!valid ? (
        <p className='text-xs text-destructive' role='alert'>
          {t('schedules.cronEdit.invalid')}
        </p>
      ) : nonGraphical ? (
        <p className='text-xs text-muted-foreground'>
          {t('schedules.cronEdit.notGraphical')}
        </p>
      ) : null}
      <p className='text-xs text-muted-foreground'>
        {t('schedules.cronEdit.hint')}
      </p>
    </div>
  )
}
