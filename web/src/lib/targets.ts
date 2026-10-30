import type { Group, Node, Target } from './api'

/** 翻译函数的最小签名，便于在纯函数中复用 i18n 文案 */
type Translate = (key: string, options?: Record<string, unknown>) => string

/**
 * 按目标类型裁剪 target：切换目标类型后不携带旧类型（组/标签/节点）的残留字段，
 * 避免保存出「type=node 但还带着 group_ids/label_*」的混合目标。
 */
export function normalizeTarget(target?: Target): Target {
  switch (target?.type) {
    case 'group':
      return { type: 'group', group_ids: target.group_ids ?? [] }
    case 'label':
      return {
        type: 'label',
        label_key: target.label_key ?? '',
        label_value: target.label_value ?? '',
      }
    case 'node':
    default:
      return { type: 'node', node_ids: target?.node_ids ?? [] }
  }
}

/** 目标是否已选择有效节点/分组/标签；Manual Run 与 Schedule 保存前必须满足 */
export function isTargetComplete(target?: Target): boolean {
  if (!target) return false
  if (target.type === 'node') return Boolean(target.node_ids?.length)
  if (target.type === 'group') return Boolean(target.group_ids?.length)
  // 标签 value 为空表示匹配该 key 的任意值（与后端一致）。
  if (target.type === 'label') return Boolean(target.label_key?.trim())
  return false
}

/** 组是否包含节点：label 组按标签匹配，static 组按成员 ID 匹配 */
export function groupMatchesNode(group: Group, node: Node) {
  if (group.type === 'label') {
    const value = node.labels?.[group.label_key]
    return (
      value !== undefined &&
      (group.label_value === '' || value === group.label_value)
    )
  }
  return group.members?.includes(node.id) || false
}

/** 节点所属分组列表 */
export function groupsForNode(node: Node, groups: Group[]) {
  return groups.filter((group) => groupMatchesNode(group, node))
}

/** 按 Schedule.Target / Run Target 的语义判断目标是否命中节点 */
export function targetMatchesNode(
  target: Target | undefined,
  node: Node | undefined,
  groups: Group[]
) {
  if (!target || !node) return false
  if (target.type === 'node') return target.node_ids?.includes(node.id) || false
  if (target.type === 'label') {
    const value = node.labels?.[target.label_key || '']
    return (
      value !== undefined &&
      (target.label_value === '' || value === target.label_value)
    )
  }
  return (target.group_ids || []).some((groupId) => {
    const group = groups.find((item) => item.id === groupId)
    if (!group) return false
    return groupMatchesNode(group, node)
  })
}

/**
 * 目标摘要：传入了名称表时展开为可读名称，否则显示数量；
 * 标签目标始终显示 key=value。
 */
export function targetSummary(
  target: Target | undefined,
  t: Translate,
  nodeNames?: Map<string, string>,
  groupNames?: Map<string, string>
) {
  if (!target) return t('targets.empty')
  if (target.type === 'node') {
    const ids = target.node_ids || []
    const names = ids.map((id) => nodeNames?.get(id)).filter(Boolean)
    if (nodeNames && names.length === ids.length && names.length)
      return names.join(', ')
    return t('targets.nodeCount', { count: ids.length })
  }
  if (target.type === 'group') {
    const ids = target.group_ids || []
    const names = ids.map((id) => groupNames?.get(id)).filter(Boolean)
    if (groupNames && names.length === ids.length && names.length)
      return names.join(', ')
    return t('targets.groupCount', { count: ids.length })
  }
  if (target.type === 'label') {
    if (!target.label_key) return t('targets.empty')
    return `${target.label_key}=${target.label_value || ''}`
  }
  return target.type
}
