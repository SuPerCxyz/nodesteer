import { describe, expect, it } from 'vitest'
import { nodeDisplayName } from './node-display-name'

describe('nodeDisplayName', () => {
  it('优先使用非空 display_name', () => {
    expect(
      nodeDisplayName({
        display_name: '生产数据库 01',
        hostname: 'db-01',
        id: 'node-1',
      })
    ).toBe('生产数据库 01')
  })

  it('display_name 为空白时回退 hostname', () => {
    expect(
      nodeDisplayName({ display_name: '   ', hostname: ' db-01 ', id: 'node-1' })
    ).toBe('db-01')
  })

  it('display_name 与 hostname 都为空时回退 node id', () => {
    expect(nodeDisplayName({ display_name: '', hostname: ' ', id: 'node-1' })).toBe(
      'node-1'
    )
  })

  it('全部为空时使用未知节点文案', () => {
    expect(nodeDisplayName({ display_name: '', hostname: '', id: '' })).toBe(
      'Unknown node'
    )
    expect(nodeDisplayName(undefined, '未知节点')).toBe('未知节点')
    expect(nodeDisplayName(null, '未知节点')).toBe('未知节点')
  })
})
