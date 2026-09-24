import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import i18n from '@/i18n'
import { beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { render } from 'vitest-browser-react'
import { userEvent } from 'vitest/browser'
import { type Node } from '@/lib/api'
import { NodeBulkActions } from './node-bulk-actions'

const upgradeNodes = vi.hoisted(() => vi.fn())
const post = vi.hoisted(() => vi.fn())

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>()
  return {
    ...actual,
    api: { ...actual.api, upgradeNodes, post },
  }
})

vi.mock('@/lib/permissions', () => ({
  useCanWrite: () => true,
  useCanRun: () => true,
}))

function makeNode(id: string, overrides: Partial<Node> = {}): Node {
  return {
    id,
    agent_id: `agent-${id}`,
    hostname: id,
    ip: '10.0.0.1',
    os: 'linux',
    arch: 'amd64',
    agent_version: 'v1.0.0',
    deployment_mode: 'native',
    host_integration: false,
    status: 'online',
    labels: {},
    capabilities: {},
    global_revision: 1,
    sync_status: 'synced',
    last_seen: '2026-09-24T00:00:00Z',
    first_seen: '2026-09-24T00:00:00Z',
    ...overrides,
  }
}

async function renderBulk(nodes: Node[], onClear = vi.fn()) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  const result = await render(
    <QueryClientProvider client={client}>
      <NodeBulkActions nodes={nodes} onClear={onClear} />
    </QueryClientProvider>
  )
  return { ...result, onClear }
}

describe('NodeBulkActions 批量操作入口', () => {
  beforeAll(async () => {
    await i18n.changeLanguage('zh')
  })

  beforeEach(() => {
    upgradeNodes.mockReset()
    post.mockReset()
    upgradeNodes.mockResolvedValue([])
    post.mockResolvedValue(undefined)
  })

  it('全 docker 集合隐藏升级入口，但保留批量暂停入口', async () => {
    const { getByRole } = await renderBulk([
      makeNode('n1', { deployment_mode: 'docker_run' }),
      makeNode('n2', { deployment_mode: 'docker_compose' }),
    ])

    await expect
      .element(getByRole('button', { name: /升级 Agent/ }))
      .not.toBeInTheDocument()
    await expect
      .element(getByRole('button', { name: '设为维护 (2)' }))
      .toBeVisible()
  })

  it('含 native 节点时展示升级入口并在确认框明示已跳过的 docker 数', async () => {
    const { getByRole, getByText } = await renderBulk([
      makeNode('n1'),
      makeNode('n2', { deployment_mode: 'docker_run' }),
      makeNode('n3'),
    ])

    await expect
      .element(getByRole('button', { name: '升级 Agent (2)' }))
      .toBeVisible()

    await userEvent.click(getByRole('button', { name: '升级 Agent (2)' }))

    await expect
      .element(getByText('将对 2 个 native 节点触发 Agent 升级。'))
      .toBeVisible()
    await expect
      .element(getByText('已跳过 1 个 docker 节点（不支持升级）。'))
      .toBeVisible()
  })

  it('提交升级只带 native 节点 id，docker 节点不进请求体', async () => {
    const { getByRole, onClear } = await renderBulk([
      makeNode('n1'),
      makeNode('n2', { deployment_mode: 'docker_run' }),
      makeNode('n3'),
    ])

    await userEvent.click(getByRole('button', { name: '升级 Agent (2)' }))
    await userEvent.click(getByRole('button', { name: '确认升级' }))

    await vi.waitFor(() => {
      expect(upgradeNodes).toHaveBeenCalledTimes(1)
    })
    expect(upgradeNodes).toHaveBeenCalledWith(['n1', 'n3'])
    await vi.waitFor(() => {
      expect(onClear).toHaveBeenCalled()
    })
  })

  it('暂停节点集合只提供恢复入口', async () => {
    const { getByRole } = await renderBulk([
      makeNode('n1', { status: 'maintenance' }),
      makeNode('n2', { status: 'disabled' }),
    ])

    await expect
      .element(getByRole('button', { name: '恢复 (2)' }))
      .toBeVisible()
    await expect
      .element(getByRole('button', { name: /设为维护/ }))
      .not.toBeInTheDocument()
  })

  it('批量恢复对每个节点独立调用单节点状态接口', async () => {
    const { getByRole, onClear } = await renderBulk([
      makeNode('n1', { status: 'maintenance' }),
      makeNode('n2', { status: 'online' }),
    ])

    await userEvent.click(getByRole('button', { name: '恢复 (1)' }))
    await userEvent.click(getByRole('button', { name: '确认执行' }))

    await vi.waitFor(() => {
      expect(post).toHaveBeenCalledTimes(1)
    })
    expect(post).toHaveBeenCalledWith('/nodes/n1', { status: 'online' })
    await vi.waitFor(() => {
      expect(onClear).toHaveBeenCalled()
    })
  })
})
