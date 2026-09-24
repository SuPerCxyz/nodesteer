import { useTranslation } from 'react-i18next'
import { type NextRunsReason, type PreviewKind, nextRuns } from './next-runs'

const REASON_KEYS: Record<NextRunsReason, string> = {
  cronMissing: 'schedules.preview.cronMissing',
  cronInvalid: 'schedules.preview.cronInvalid',
  intervalInvalid: 'schedules.preview.intervalInvalid',
  runAtMissing: 'schedules.preview.runAtMissing',
  runAtInvalid: 'schedules.preview.runAtInvalid',
  timezoneInvalid: 'schedules.preview.timezoneInvalid',
  unsupported: 'schedules.preview.unsupported',
}

function formatRun(iso: string, locale: string, timezone: string) {
  return new Date(iso).toLocaleString(locale, {
    timeZone: timezone,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
    hourCycle: 'h23',
  })
}

/** 接下来 5 次触发时刻预览：本地化展示并明确标注时区；非法输入显示错误文案 */
export function NextRunsPreview({
  type,
  expression,
  intervalSec,
  runAt,
  timezone,
}: {
  type: PreviewKind
  expression?: string
  intervalSec?: number
  runAt?: string
  timezone?: string
}) {
  const { t, i18n } = useTranslation()
  // on_start 由目标节点 Agent 启动触发，没有可计算的时刻，也无需时区
  if (type === 'on_start') {
    return (
      <div className='grid gap-2 rounded-md border bg-muted/30 p-3'>
        <span className='text-sm font-medium'>
          {t('schedules.preview.title')}
        </span>
        <p className='text-xs text-muted-foreground'>
          {t('schedules.preview.onStart')}
        </p>
      </div>
    )
  }
  const result = nextRuns({ type, expression, intervalSec, runAt, timezone })
  const locale = i18n.language === 'en' ? 'en-US' : 'zh-CN'
  return (
    <div className='grid gap-2 rounded-md border bg-muted/30 p-3'>
      <div className='flex flex-wrap items-baseline justify-between gap-2'>
        <span className='text-sm font-medium'>
          {t('schedules.preview.title')}
        </span>
        <span className='font-mono text-xs text-muted-foreground'>
          {t('schedules.preview.timezone', { tz: result.timezone })}
        </span>
      </div>
      {result.ok ? (
        <ul className='grid gap-1'>
          {result.runs.map((iso) => (
            <li className='font-mono text-xs' key={iso}>
              {formatRun(iso, locale, result.timezone)}
            </li>
          ))}
          {result.once ? (
            <li className='text-xs text-muted-foreground'>
              {t('schedules.preview.once')}
            </li>
          ) : null}
        </ul>
      ) : (
        <p className='text-xs text-destructive' role='alert'>
          {t(REASON_KEYS[result.reason], { tz: result.timezone })}
        </p>
      )}
    </div>
  )
}
