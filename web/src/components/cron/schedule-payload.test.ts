import { describe, expect, it } from 'vitest'
import { buildSchedulePayload } from './schedule-payload'

describe('buildSchedulePayload 按类型裁剪触发时间字段', () => {
  it('on_start 不下发 expression / interval_sec / run_at', () => {
    const payload = buildSchedulePayload({
      task_id: 'task-1',
      type: 'on_start',
      expression: '0 9 * * *',
      interval_sec: 60,
      run_at: '2026-10-01T02:30:00.000Z',
      timezone: 'Asia/Shanghai',
      execution_owner: 'agent',
      offline_policy: 'allow_offline',
      misfire_policy: 'run_once',
      enabled: true,
    })

    expect(payload.type).toBe('on_start')
    expect('expression' in payload).toBe(false)
    expect('interval_sec' in payload).toBe(false)
    expect('run_at' in payload).toBe(false)
    // 后端仍无条件校验这些常规字段
    expect(payload.task_id).toBe('task-1')
    expect(payload.timezone).toBe('Asia/Shanghai')
    expect(payload.execution_owner).toBe('agent')
    expect(payload.offline_policy).toBe('allow_offline')
    expect(payload.misfire_policy).toBe('run_once')
    expect(payload.enabled).toBe(true)
  })

  it('one_time 保留 run_at', () => {
    const payload = buildSchedulePayload({
      task_id: 'task-1',
      type: 'one_time',
      expression: '',
      interval_sec: 0,
      run_at: '2026-10-01T02:30:00.000Z',
    })
    expect(payload.run_at).toBe('2026-10-01T02:30:00.000Z')
  })

  it('cron / interval 保留各自时间字段且不下发 run_at', () => {
    const cron = buildSchedulePayload({
      type: 'cron',
      expression: '0 9 * * *',
      run_at: 'x',
    })
    expect(cron.expression).toBe('0 9 * * *')
    expect('run_at' in cron).toBe(false)

    const interval = buildSchedulePayload({
      type: 'interval',
      interval_sec: 60,
      run_at: 'x',
    })
    expect(interval.interval_sec).toBe(60)
    expect('run_at' in interval).toBe(false)
  })

  it('不修改入参对象', () => {
    const form = { type: 'on_start', expression: '0 9 * * *' }
    buildSchedulePayload(form)
    expect(form.expression).toBe('0 9 * * *')
  })
})
