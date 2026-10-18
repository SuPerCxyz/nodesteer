import type { Task, Target } from './api'

/** app_deploy 类型允许的操作（后端仅接受 deploy | upgrade，缺陷 FAIL-G-001）。 */
export const appDeployOperations = ['deploy', 'upgrade']

/** app_operation 类型允许的操作（后端 start | stop | restart | upgrade）。 */
export const appOperations = ['start', 'stop', 'restart', 'upgrade']

/**
 * 按任务类型归一化 app_operation：
 * - app_deploy：仅 deploy/upgrade，非法值（如 start/stop）回落 deploy；
 * - app_operation：仅操作枚举，非法值回落 start；
 * - 其余类型（command/script）返回 undefined，表示应从 payload 删除该字段。
 */
export function normalizeAppOperation(type?: string, current?: string) {
  if (type === 'app_deploy') {
    return appDeployOperations.includes(current || '') ? current : 'deploy'
  }
  if (type === 'app_operation') {
    return appOperations.includes(current || '') ? current : 'start'
  }
  return undefined
}

/** 任务定义自身字段（不含后端管理字段与类型专属字段）。 */
const taskDefinitionFields = [
  'name',
  'type',
  'parameters',
  'param_values',
  'timeout',
  'retry',
  'offline_policy',
  'run_user',
  'enabled',
  'condition',
] as const

/**
 * 按目标类型裁剪 target：切换目标类型后不携带旧类型（组/标签/节点）的残留字段，
 * 避免保存出「type=node 但还带着 group_ids/label_*」的混合目标。
 */
function normalizeTarget(target?: Target): Target | undefined {
  if (!target) return undefined
  switch (target.type) {
    case 'node':
      return { type: 'node', node_ids: target.node_ids ?? [] }
    case 'group':
      return { type: 'group', group_ids: target.group_ids ?? [] }
    case 'label':
      return {
        type: 'label',
        label_key: target.label_key ?? '',
        label_value: target.label_value ?? '',
      }
    default:
      return target
  }
}

/**
 * 组装任务保存 payload：只提交任务定义自身字段（缺陷 OBS-20261016-G5-STALE-PAYLOAD-FIELDS-CARRIED）。
 *
 * 后端 PUT 为整体替换语义，因此这里按当前类型/目标白名单组装：
 * - 后端管理字段（id/revision/created_at/updated_at）与陈旧类型字段一律剔除；
 * - app_operation 按类型归一化（缺陷 FAIL-G-001），非应用类型不提交该字段；
 * - command/script/application 字段只提交当前类型使用的一个，切换类型后的旧值不再误提交。
 */
export function buildTaskPayload(form: Partial<Task>): Partial<Task> {
  const payload: Partial<Task> = {}
  const record = payload as Record<string, unknown>
  for (const field of taskDefinitionFields) {
    if (form[field] !== undefined) record[field] = form[field]
  }
  payload.target = normalizeTarget(form.target)
  switch (form.type) {
    case 'command':
      if (form.command !== undefined) payload.command = form.command
      break
    case 'script':
      if (form.script_id !== undefined) payload.script_id = form.script_id
      break
    case 'app_deploy':
    case 'app_operation':
      if (form.application_id !== undefined)
        payload.application_id = form.application_id
      payload.app_operation = normalizeAppOperation(
        form.type,
        form.app_operation
      )
      break
  }
  return payload
}
