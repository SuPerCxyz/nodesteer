import { describe, expect, it } from 'vitest'
import type { Task } from './api'
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

describe('buildTaskPayload 剔除陈旧字段（OBS-20261016-G5-STALE-PAYLOAD-FIELDS-CARRIED）', () => {
  const staleForm: Partial<Task> = {
    id: 'task-1',
    revision: 7,
    created_at: '2026-10-16T08:59:33Z',
    updated_at: '2026-10-16T09:00:00Z',
    name: 'qa-task',
    type: 'script',
    command: '/bin/echo stale',
    script_id: 'sc-1',
    application_id: 'app-1',
    app_operation: 'stop',
    target: {
      type: 'node',
      node_ids: ['n1'],
      group_ids: ['g1'],
      label_key: 'env',
      label_value: 'prod',
    },
    parameters: [],
    param_values: {},
    timeout: 300,
    retry: 0,
    offline_policy: 'hub_online_required',
    enabled: true,
  }

  it('不提交后端管理字段与类型切换后的陈旧字段', () => {
    const payload = buildTaskPayload(staleForm)
    expect('id' in payload).toBe(false)
    expect('revision' in payload).toBe(false)
    expect('created_at' in payload).toBe(false)
    expect('updated_at' in payload).toBe(false)
    expect('command' in payload).toBe(false)
    expect('application_id' in payload).toBe(false)
    expect('app_operation' in payload).toBe(false)
    expect(payload.script_id).toBe('sc-1')
  })

  it('目标按类型裁剪：node 只保留 node_ids', () => {
    expect(buildTaskPayload(staleForm).target).toEqual({
      type: 'node',
      node_ids: ['n1'],
    })
  })

  it('目标按类型裁剪：group/label 只保留各自字段', () => {
    expect(
      buildTaskPayload({
        type: 'command',
        target: {
          type: 'group',
          node_ids: ['n1'],
          group_ids: ['g1'],
          label_key: 'env',
        },
      }).target
    ).toEqual({ type: 'group', group_ids: ['g1'] })
    expect(
      buildTaskPayload({
        type: 'command',
        target: {
          type: 'label',
          node_ids: ['n1'],
          group_ids: ['g1'],
          label_key: 'env',
          label_value: 'prod',
        },
      }).target
    ).toEqual({ type: 'label', label_key: 'env', label_value: 'prod' })
  })

  it('command 类型只提交 command，app 类型只提交 application_id/app_operation', () => {
    const commandPayload = buildTaskPayload({
      type: 'command',
      command: 'true',
      script_id: 'sc-1',
      application_id: 'app-1',
    })
    expect(commandPayload.command).toBe('true')
    expect('script_id' in commandPayload).toBe(false)
    expect('application_id' in commandPayload).toBe(false)

    const appPayload = buildTaskPayload({
      type: 'app_operation',
      command: 'true',
      script_id: 'sc-1',
      application_id: 'app-1',
      app_operation: 'restart',
    })
    expect(appPayload.application_id).toBe('app-1')
    expect(appPayload.app_operation).toBe('restart')
    expect('command' in appPayload).toBe(false)
    expect('script_id' in appPayload).toBe(false)
  })

  it('保留任务定义自身字段（名称/超时/启用等）', () => {
    const payload = buildTaskPayload(staleForm)
    expect(payload.name).toBe('qa-task')
    expect(payload.type).toBe('script')
    expect(payload.timeout).toBe(300)
    expect(payload.enabled).toBe(true)
    expect(payload.offline_policy).toBe('hub_online_required')
  })
})
