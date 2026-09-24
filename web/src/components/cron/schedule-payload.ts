import type { Schedule } from '@/lib/api'

/**
 * 组装 `POST /schedules` / `PUT /schedules/:id` payload。
 * 按类型裁剪触发时间字段，与后端 `validateSchedule` 一致：
 * - `on_start`：无任何触发时间字段（expression / interval_sec / run_at 全部剔除）
 * - 非 `one_time`：不发送 run_at（避免零值时间被写入）
 */
export function buildSchedulePayload(
  form: Partial<Schedule>
): Partial<Schedule> {
  const payload = { ...form }
  if (payload.type !== 'one_time') delete payload.run_at
  if (payload.type === 'on_start') {
    delete payload.expression
    delete payload.interval_sec
    delete payload.run_at
  }
  return payload
}
