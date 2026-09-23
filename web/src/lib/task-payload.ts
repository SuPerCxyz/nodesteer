import type { Task } from './api'

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

/** 组装任务保存 payload：按类型剔除/归一化 app_operation，避免后端 400（缺陷 FAIL-G-001）。 */
export function buildTaskPayload(form: Partial<Task>): Partial<Task> {
  const { app_operation: _app_operation, ...rest } = form
  const operation = normalizeAppOperation(form.type, form.app_operation)
  return operation === undefined ? rest : { ...rest, app_operation: operation }
}
