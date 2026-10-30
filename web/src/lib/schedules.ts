import type { Schedule } from './api'

type Translate = (key: string, options?: Record<string, unknown>) => string

/** 调度触发器的人类可读文本；manual 明确显示「仅手动」 */
export function scheduleTriggerText(schedule: Schedule, t: Translate) {
  if (schedule.type === 'manual') return t('schedules.manualOnly')
  if (schedule.type === 'interval')
    return t('schedules.everySeconds', { count: schedule.interval_sec })
  if (schedule.type === 'one_time') return schedule.run_at || '-'
  if (schedule.type === 'on_start') return t('schedules.onStart')
  return schedule.expression || '-'
}

/** 调度名称；历史或后端未生成名称时退回触发器摘要 */
export function scheduleDisplayName(schedule: Schedule, t: Translate) {
  return schedule.name?.trim() || scheduleTriggerText(schedule, t)
}
