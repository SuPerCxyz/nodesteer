import { beforeEach, describe, expect, it } from 'vitest'
import {
  readTablePageSize,
  readTableView,
  tableViewStorageKey,
  writeTableView,
} from './table-view'

describe('table-view 持久化', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  it('未保存时返回空状态与回退每页条数', () => {
    expect(readTableView('unit-test')).toEqual({})
    expect(readTablePageSize('unit-test', 10)).toBe(10)
  })

  it('写入后可读回列可见性与每页条数', () => {
    writeTableView('unit-test', {
      columnVisibility: { ip: false, os: true },
      pageSize: 30,
    })
    expect(readTableView('unit-test')).toEqual({
      columnVisibility: { ip: false, os: true },
      pageSize: 30,
    })
    expect(readTablePageSize('unit-test', 10)).toBe(30)
    expect(localStorage.getItem(tableViewStorageKey('unit-test'))).toBeTruthy()
  })

  it('非法内容被忽略并回退', () => {
    localStorage.setItem(tableViewStorageKey('unit-test'), '{not json')
    expect(readTableView('unit-test')).toEqual({})
    localStorage.setItem(
      tableViewStorageKey('unit-test'),
      JSON.stringify({ pageSize: -1, columnVisibility: { a: 'yes' } })
    )
    const state = readTableView('unit-test')
    expect(state.pageSize).toBeUndefined()
    expect(state.columnVisibility ?? {}).toEqual({})
    expect(readTablePageSize('unit-test', 20)).toBe(20)
  })

  it('不同 storageKey 互不影响', () => {
    writeTableView('a', { pageSize: 20 })
    expect(readTablePageSize('a', 10)).toBe(20)
    expect(readTablePageSize('b', 10)).toBe(10)
  })
})
