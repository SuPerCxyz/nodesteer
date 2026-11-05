/** 用户可编辑的节点显示名；空值回退 hostname → node id → 通用未知文案。 */
export type NodeNameLike = {
  display_name?: string
  hostname?: string
  id?: string
}

export function nodeDisplayName(
  node?: NodeNameLike | null,
  unknownLabel = 'Unknown node'
): string {
  const displayName = node?.display_name?.trim()
  if (displayName) return displayName
  const hostname = node?.hostname?.trim()
  if (hostname) return hostname
  const id = node?.id?.trim()
  if (id) return id
  return unknownLabel
}
