import type { Node } from '@/lib/api'

/**
 * 入站数据面仅在“当前会话确为入站”且 Agent 未声明 inbound_data_plane 时不可用。
 * 出站会话、离线节点或普通出站节点不应误报。
 */
export function inboundDataPlaneBlocked(node?: Node | null): boolean {
  return Boolean(
    node && node.connection_mode === 'inbound' && !node.inbound?.data_plane
  )
}

/** 从节点集合中筛出当前会阻断数据面操作的节点（保持输入顺序、去重）。 */
export function blockedInboundNodes(nodes: Array<Node | undefined>): Node[] {
  const seen = new Set<string>()
  const out: Node[] = []
  for (const node of nodes) {
    if (!node || seen.has(node.id) || !inboundDataPlaneBlocked(node)) continue
    seen.add(node.id)
    out.push(node)
  }
  return out
}

/** 把后端稳定错误码转换为可读数据面原因；未命中返回 null。 */
export function inboundDataPlaneError(error: unknown): string | null {
  if (!(error instanceof Error)) return null
  const text = `${error.message} ${'rawMessage' in error ? String(error.rawMessage) : ''}`
  if (text.includes('inbound_data_plane_unavailable')) {
    return 'inbound_data_plane_unavailable'
  }
  if (text.includes('inbound_stage_unavailable')) {
    return 'inbound_stage_unavailable'
  }
  return null
}
