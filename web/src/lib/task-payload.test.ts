import { describe, expect, it } from 'vitest'
import { buildTaskPayload, normalizeAppOperation } from './task-payload'

describe('buildTaskPayload 归一化 app_operation', () => {
  it('app_deploy 仅允许 deploy/upgrade，非法值回落 deploy', () => {
    expect(
      buildTaskPayload({ type: 'app_deploy', app_operation: 'start' })
        .app_operation
    ).toBe('deploy')
    expect(
      buildTaskPayload({ type: 'app_deploy', app_operation: 'upgrade' })
        .app_operation
    ).toBe('upgrade')
    expect(buildTaskPayload({ type: 'app_deploy' }).app_operation).toBe(
      'deploy'
    )
  })

  it('app_operation 缺省 start 并保留合法选择', () => {
    expect(buildTaskPayload({ type: 'app_operation' }).app_operation).toBe(
      'start'
    )
    expect(
      buildTaskPayload({ type: 'app_operation', app_operation: 'stop' })
        .app_operation
    ).toBe('stop')
    expect(
      buildTaskPayload({ type: 'app_operation', app_operation: 'deploy' })
        .app_operation
    ).toBe('start')
  })

  it('非应用类型剔除 app_operation 字段', () => {
    const commandPayload = buildTaskPayload({
      type: 'command',
      app_operation: 'start',
      command: 'true',
    })
    expect('app_operation' in commandPayload).toBe(false)
    expect(commandPayload.command).toBe('true')
    const scriptPayload = buildTaskPayload({
      type: 'script',
      app_operation: 'restart',
    })
    expect('app_operation' in scriptPayload).toBe(false)
  })
})

describe('normalizeAppOperation 类型切换', () => {
  it('切到应用类型给出合法值，切回命令类型返回删除标记', () => {
    expect(normalizeAppOperation('app_deploy', 'restart')).toBe('deploy')
    expect(normalizeAppOperation('app_operation', 'restart')).toBe('restart')
    expect(normalizeAppOperation('command', 'restart')).toBeUndefined()
    expect(normalizeAppOperation('script', 'deploy')).toBeUndefined()
  })
})
