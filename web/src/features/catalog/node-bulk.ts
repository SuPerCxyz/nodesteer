import type { Node, NodeUpgradeResult } from '@/lib/api'

/** native 部署形态；其余形态（docker_run / docker_compose 等）不支持 Agent 升级 */
export const NATIVE_DEPLOYMENT_MODE = 'native'

export interface UpgradeTargets {
  /** 实际提交升级的 native 节点 id（保持输入顺序） */
  nodeIds: string[]
  /** 被前端过滤掉、不进请求体的非 native 节点数（与后端 SKIPPED 双保险） */
  skippedDocker: number
}

/**
 * 按部署形态拆分升级请求集。
 * 勾选不限制形态（批量暂停也需要选 docker 节点），因此升级入口在此过滤。
 */
export function splitUpgradeTargets(
  nodes: Pick<Node, 'id' | 'deployment_mode'>[]
): UpgradeTargets {
  const nodeIds: string[] = []
  let skippedDocker = 0
  for (const node of nodes) {
    if (node.deployment_mode === NATIVE_DEPLOYMENT_MODE) nodeIds.push(node.id)
    else skippedDocker += 1
  }
  return { nodeIds, skippedDocker }
}

export interface UpgradeSummary {
  /** 新建并下发的升级执行数 */
  dispatched: number
  /** 命中在途升级去重、返回既有记录的节点数 */
  deduplicated: number
  /** 后端 SKIPPED（docker 不支持 / 节点暂停等）节点数 */
  skipped: number
  failed: number
  /** SKIPPED / FAILED 的原因（去重、保持出现顺序） */
  reasons: string[]
}

/** 聚合 `POST /nodes/upgrade` 的逐节点结果为可展示的汇总 */
export function summarizeUpgradeResults(
  results: NodeUpgradeResult[]
): UpgradeSummary {
  const summary: UpgradeSummary = {
    dispatched: 0,
    deduplicated: 0,
    skipped: 0,
    failed: 0,
    reasons: [],
  }
  const seenReasons = new Set<string>()
  const pushReason = (reason?: string) => {
    const value = (reason || '').trim()
    if (!value || seenReasons.has(value)) return
    seenReasons.add(value)
    summary.reasons.push(value)
  }
  for (const result of results) {
    if (result.deduplicated) {
      summary.deduplicated += 1
      continue
    }
    if (result.status === 'SKIPPED') {
      summary.skipped += 1
      pushReason(result.block_reason)
      continue
    }
    if (result.status === 'FAILED') {
      summary.failed += 1
      pushReason(result.block_reason)
      continue
    }
    summary.dispatched += 1
  }
  return summary
}

export interface StatusBatchResult {
  ok: number
  fail: number
  /** 失败节点：<nodeId>: <原因>，保持调用顺序 */
  reasons: string[]
}

/**
 * 循环单节点 `POST /nodes/:id {status}` 实现批量暂停/恢复。
 * 逐节点独立 try/catch：单个失败不阻断其余节点。
 */
export async function applyNodeStatus(
  nodeIds: string[],
  status: string,
  post: (nodeId: string, status: string) => Promise<unknown>
): Promise<StatusBatchResult> {
  const result: StatusBatchResult = { ok: 0, fail: 0, reasons: [] }
  for (const nodeId of nodeIds) {
    try {
      await post(nodeId, status)
      result.ok += 1
    } catch (error) {
      result.fail += 1
      const message = error instanceof Error ? error.message : String(error)
      result.reasons.push(message ? `${nodeId}: ${message}` : nodeId)
    }
  }
  return result
}
