import type { VisibilityState } from '@tanstack/react-table'

/** 表格视图持久化状态：列可见性与每页条数 */
export interface TableViewState {
  columnVisibility?: VisibilityState
  pageSize?: number
}

const STORAGE_PREFIX = 'nodesteer.table.'

/** localStorage 键名：nodesteer.table.<storageKey> */
export function tableViewStorageKey(storageKey: string) {
  return `${STORAGE_PREFIX}${storageKey}`
}

const storage = typeof localStorage === 'undefined' ? null : localStorage

/** 读取表格视图状态；未保存、解析失败或内容非法时返回空对象 */
export function readTableView(storageKey: string): TableViewState {
  if (!storage) return {}
  try {
    const raw = storage.getItem(tableViewStorageKey(storageKey))
    if (!raw) return {}
    const parsed: unknown = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return {}
    const { columnVisibility, pageSize } = parsed as TableViewState
    const state: TableViewState = {}
    if (columnVisibility && typeof columnVisibility === 'object') {
      state.columnVisibility = Object.fromEntries(
        Object.entries(columnVisibility).filter(
          ([, visible]) => typeof visible === 'boolean'
        )
      )
    }
    if (
      typeof pageSize === 'number' &&
      Number.isFinite(pageSize) &&
      pageSize > 0
    ) {
      state.pageSize = Math.floor(pageSize)
    }
    return state
  } catch {
    return {}
  }
}

/** 写入表格视图状态；存储不可用（隐私模式等）时静默跳过 */
export function writeTableView(storageKey: string, state: TableViewState) {
  if (!storage) return
  try {
    storage.setItem(tableViewStorageKey(storageKey), JSON.stringify(state))
  } catch {
    /* 存储写入失败不应影响表格使用 */
  }
}

/** 读取已持久化的每页条数，缺失或非法时回退到 fallback */
export function readTablePageSize(storageKey: string, fallback: number) {
  return readTableView(storageKey).pageSize ?? fallback
}
