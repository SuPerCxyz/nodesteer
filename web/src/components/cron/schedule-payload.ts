import type { Condition, Schedule } from '@/lib/api'
import { normalizeTarget } from '@/lib/targets'

type SchedulePayload = Omit<Partial<Schedule>, 'condition'> & {
  condition?: Condition | null
}

/**
 * 组装 `POST /schedules` / `PUT /schedules/:id` payload。
 * 按类型裁剪触发时间字段，与后端 manual/on_start 校验一致：
 * - `on_start` / `manual`：无任何触发时间字段（expression / interval_sec / run_at 全部剔除）
 * - 非 `one_time`：不发送 run_at（避免零值时间被写入）
 * - 目标按类型裁剪，空名称交由后端生成默认值
 */
export function buildSchedulePayload(form: Partial<Schedule>): Partial<Schedule> {
  const payload: SchedulePayload = { ...form }
  // 显式 null 表示清空远程条件；缺省保留旧值（后端按字段是否存在合并）。
  payload.condition = form.condition ?? null
  if (!payload.name?.trim()) delete payload.name
  if (payload.target) payload.target = normalizeTarget(payload.target)
  if (payload.type !== 'one_time') delete payload.run_at
  if (payload.type === 'on_start' || payload.type === 'manual') {
    delete payload.expression
    delete payload.interval_sec
    delete payload.run_at
  }
  return payload as Partial<Schedule>
}
