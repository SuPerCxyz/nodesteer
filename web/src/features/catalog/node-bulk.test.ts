import { describe, expect, it, vi } from 'vitest'
import type { NodeUpgradeResult } from '@/lib/api'
import {
  applyNodeStatus,
  splitUpgradeTargets,
  summarizeUpgradeResults,
} from './node-bulk'

function node(id: string, deployment_mode: string) {
  return { id, deployment_mode }
}

describe('splitUpgradeTargets docker 过滤', () => {
  it('只保留 native 节点并统计被跳过的 docker 节点数', () => {
    const targets = splitUpgradeTargets([
      node('n1', 'native'),
      node('n2', 'docker_run'),
      node('n3', 'native'),
      node('n4', 'docker_compose'),
    ])
    expect(targets.nodeIds).toEqual(['n1', 'n3'])
    expect(targets.skippedDocker).toBe(2)
  })

  it('全 native 集合不产生跳过数', () => {
    const targets = splitUpgradeTargets([node('n1', 'native')])
    expect(targets).toEqual({ nodeIds: ['n1'], skippedDocker: 0 })
  })

  it('全 docker 集合请求集为空（升级入口据此隐藏）', () => {
    const targets = splitUpgradeTargets([
      node('n1', 'docker_run'),
      node('n2', 'docker_compose'),
    ])
    expect(targets.nodeIds).toHaveLength(0)
    expect(targets.skippedDocker).toBe(2)
  })

  it('未知/空部署形态按非 native 处理，不进升级请求', () => {
    const targets = splitUpgradeTargets([node('n1', ''), node('n2', 'podman')])
    expect(targets.nodeIds).toEqual([])
    expect(targets.skippedDocker).toBe(2)
  })
})

describe('summarizeUpgradeResults 逐节点结果聚合', () => {
  it('按下发/去重/SKIPPED/FAILED 分类计数并收集去重原因', () => {
    const results: NodeUpgradeResult[] = [
      { node_id: 'n1', status: 'PENDING', dispatched: true },
      {
        node_id: 'n2',
        status: 'PENDING',
        execution_id: 'e1',
        deduplicated: true,
      },
      {
        node_id: 'n3',
        status: 'SKIPPED',
        block_reason: 'unsupported deployment mode',
      },
      { node_id: 'n4', status: 'SKIPPED', block_reason: 'node paused' },
      {
        node_id: 'n5',
        status: 'SKIPPED',
        block_reason: 'unsupported deployment mode',
      },
      { node_id: 'n6', status: 'FAILED', block_reason: 'node offline' },
    ]
    expect(summarizeUpgradeResults(results)).toEqual({
      dispatched: 1,
      deduplicated: 1,
      skipped: 3,
      failed: 1,
      reasons: ['unsupported deployment mode', 'node paused', 'node offline'],
    })
  })

  it('空结果返回零值', () => {
    expect(summarizeUpgradeResults([])).toEqual({
      dispatched: 0,
      deduplicated: 0,
      skipped: 0,
      failed: 0,
      reasons: [],
    })
  })
})

describe('applyNodeStatus 批量暂停/恢复循环与聚合', () => {
  it('全部成功时 ok 等于节点数且逐个按顺序提交', async () => {
    const post = vi.fn(() => Promise.resolve())
    const result = await applyNodeStatus(
      ['n1', 'n2', 'n3'],
      'maintenance',
      post
    )

    expect(result).toEqual({ ok: 3, fail: 0, reasons: [] })
    expect(post).toHaveBeenCalledTimes(3)
    expect(post).toHaveBeenNthCalledWith(1, 'n1', 'maintenance')
    expect(post).toHaveBeenNthCalledWith(3, 'n3', 'maintenance')
  })

  it('部分失败不阻断其余节点，失败原因带节点 id', async () => {
    const post = vi.fn((nodeId: string) =>
      nodeId === 'n2'
        ? Promise.reject(new Error('permission denied'))
        : Promise.resolve()
    )
    const result = await applyNodeStatus(['n1', 'n2', 'n3'], 'online', post)

    expect(result.ok).toBe(2)
    expect(result.fail).toBe(1)
    expect(result.reasons).toEqual(['n2: permission denied'])
    // 单节点失败后仍继续处理 n3
    expect(post).toHaveBeenCalledTimes(3)
    expect(post).toHaveBeenNthCalledWith(3, 'n3', 'online')
  })

  it('非 Error 抛出也计入失败原因', async () => {
    const result = await applyNodeStatus(['n1'], 'online', () =>
      Promise.reject('boom')
    )
    expect(result).toEqual({ ok: 0, fail: 1, reasons: ['n1: boom'] })
  })

  it('空选择不发请求', async () => {
    const post = vi.fn()
    const result = await applyNodeStatus([], 'online', post)
    expect(result).toEqual({ ok: 0, fail: 0, reasons: [] })
    expect(post).not.toHaveBeenCalled()
  })
})
