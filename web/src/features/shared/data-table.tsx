import { useEffect, useMemo, useState } from 'react'
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type Row,
  type RowSelectionState,
  type SortingState,
  type Table as ReactTable,
  type VisibilityState,
} from '@tanstack/react-table'
import { Loader2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  readTablePageSize,
  readTableView,
  writeTableView,
} from '@/lib/table-view'
import { cn } from '@/lib/utils'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  DataTableBulkActions,
  DataTableColumnHeader,
  DataTablePagination,
  DataTableToolbar,
  type DataTableToolbarProps,
} from '@/components/data-table'
import { RackList, RackRow } from './rack-list'
import { EmptyState } from './ui'

/** 前置多选列：独立于业务列，注入后不改变业务列的顺序与相对宽度权重 */
function selectColumn<TData>(): ColumnDef<TData> {
  return {
    id: 'select',
    enableSorting: false,
    enableHiding: false,
    size: 44,
    minSize: 40,
    maxSize: 44,
    header: SelectAllHeader,
    cell: SelectRowCell,
  }
}

function SelectAllHeader<TData>({
  table,
}: {
  table: ReactTable<TData>
}): React.ReactNode {
  const { t } = useTranslation()
  return (
    <Checkbox
      checked={
        table.getIsAllRowsSelected() ||
        (table.getIsSomeRowsSelected() && 'indeterminate')
      }
      onCheckedChange={(value) => table.toggleAllRowsSelected(Boolean(value))}
      aria-label={t('common.selectAllRows')}
    />
  )
}

function SelectRowCell<TData>({ row }: { row: Row<TData> }): React.ReactNode {
  const { t } = useTranslation()
  return (
    <Checkbox
      checked={row.getIsSelected()}
      onCheckedChange={(value) => row.toggleSelected(Boolean(value))}
      aria-label={t('common.selectRow')}
    />
  )
}

/**
 * 无稳定 id 字段时的兜底：按对象身份分配一次性键。
 * 同一对象实例跨分页/排序/重渲染保持同一个键，避免退回行索引。
 */
const identityKeys = new WeakMap<object, string>()
let identitySeq = 0

/** 行选择键：优先取行对象的稳定 id（字符串/数字），否则按对象身份兜底 */
function resolveRowId(row: unknown, index: number): string {
  if (row && typeof row === 'object') {
    const candidate = (row as { id?: unknown }).id
    if (typeof candidate === 'string' && candidate) return candidate
    if (typeof candidate === 'number' && Number.isFinite(candidate))
      return String(candidate)
    const existing = identityKeys.get(row)
    if (existing) return existing
    const generated = `identity-${++identitySeq}`
    identityKeys.set(row, generated)
    return generated
  }
  return `index-${index}`
}

export function DataTable<TData>({
  data,
  columns,
  searchPlaceholder = 'Filter...',
  className = '',
  hideSearch = false,
  manualPagination = false,
  manualFiltering = false,
  isLoading = false,
  filter,
  onFilterChange,
  total,
  page = 1,
  pageSize = 10,
  onPageChange,
  onPageSizeChange,
  storageKey,
  enableRowSelection = false,
  getRowId,
  renderBulkActions,
  bulkEntityName = '',
  variant = 'table',
  rackPrimaryColumnId,
  rackKind,
  filters,
  toolbarActions,
  renderToolbar,
  emptyMessage,
  emptyAction,
  inset = false,
}: {
  data: TData[]
  columns: ColumnDef<TData>[]
  searchPlaceholder?: string
  className?: string
  hideSearch?: boolean
  /** 列表形态：table 保留原表格；rack 使用紧凑机架行（无表头/列线）。 */
  variant?: 'table' | 'rack'
  /** rack 形态下作为标题的列 id；缺省取第一个非选择/操作列。 */
  rackPrimaryColumnId?: string
  /** Rack 页面语义类型，用于专用列宽样式。 */
  rackKind?: string
  /** 服务端分页：data 仅为当前页，分页状态由 page/pageSize 受控传入 */
  manualPagination?: boolean
  /** 服务端搜索时禁用当前页本地过滤，并受控同步搜索框。 */
  manualFiltering?: boolean
  /** 首次加载中：尚无数据时不显示「没有结果」，改显示加载占位（避免 Loading 与 No Results 混淆） */
  isLoading?: boolean
  filter?: string
  onFilterChange?: (value: string) => void
  /** 服务端匹配总条数（manualPagination 时使用） */
  total?: number
  /** 当前页码，1-based（manualPagination 时使用） */
  page?: number
  /** 每页条数（manualPagination 时使用） */
  pageSize?: number
  onPageChange?: (page: number) => void
  onPageSizeChange?: (pageSize: number) => void
  /** 提供时把列可见性与每页条数持久化到 localStorage */
  storageKey?: string
  /** 启用行多选（注入前置选择列）；选择按行 id 跨分页/排序保留 */
  enableRowSelection?: boolean
  /** 行选择键；缺省取行对象的 id 字段（无 id 时按对象身份兜底），不再用行索引 */
  getRowId?: (row: TData, index: number) => string
  /** 选中后浮出的批量操作工具条内容；未启用多选时不渲染 */
  renderBulkActions?: (table: ReactTable<TData>) => React.ReactNode
  /** 工具条读屏文案中的实体名（如「节点」） */
  bulkEntityName?: string
  /** 表格工具栏筛选器；真实接入列过滤，顺序位于搜索之后。 */
  filters?: DataTableToolbarProps<TData>['filters']
  /** 工具栏右侧次要操作（批量/导出等），位于视图选项之前。 */
  toolbarActions?: React.ReactNode
  /** 完全替换默认工具栏；用于接入服务端搜索/筛选并保留视图选项。 */
  renderToolbar?: (table: ReactTable<TData>) => React.ReactNode
  emptyMessage?: string
  emptyAction?: React.ReactNode
  /** 嵌入外部 Card 时启用统一内边距，并去掉表格自带边框避免双层。 */
  inset?: boolean
}) {
  const { t } = useTranslation()
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(
    () =>
      (storageKey ? readTableView(storageKey).columnVisibility : undefined) ??
      {}
  )
  const [globalFilter, setGlobalFilter] = useState('')
  const [rowSelection, setRowSelection] = useState<RowSelectionState>({})
  const [clientPagination, setClientPagination] = useState<PaginationState>(
    () => ({
      pageIndex: 0,
      pageSize: storageKey ? readTablePageSize(storageKey, 10) : 10,
    })
  )
  const pagination = manualPagination
    ? { pageIndex: Math.max(0, page - 1), pageSize }
    : clientPagination
  useEffect(() => {
    if (!storageKey) return
    writeTableView(storageKey, {
      columnVisibility,
      pageSize: pagination.pageSize,
    })
  }, [storageKey, columnVisibility, pagination.pageSize])
  const tableColumns = useMemo(
    () => (enableRowSelection ? [selectColumn<TData>(), ...columns] : columns),
    [columns, enableRowSelection]
  )
  const table = useReactTable({
    data,
    columns: tableColumns,
    state: {
      sorting,
      columnVisibility,
      globalFilter: filter ?? globalFilter,
      pagination,
      rowSelection,
    },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    onGlobalFilterChange: (updater) => {
      const value =
        typeof updater === 'function'
          ? updater(filter ?? globalFilter)
          : updater
      if (onFilterChange) onFilterChange(value)
      else setGlobalFilter(value)
    },
    onRowSelectionChange: setRowSelection,
    enableRowSelection,
    // 显式提供 getRowId：TanStack 默认按行索引生成 selection 键，跨页/排序会漂移到别的行
    getRowId: getRowId ?? resolveRowId,
    onPaginationChange: (updater) => {
      const next = typeof updater === 'function' ? updater(pagination) : updater
      if (manualPagination) {
        if (next.pageSize !== pagination.pageSize)
          onPageSizeChange?.(next.pageSize)
        if (next.pageIndex !== pagination.pageIndex)
          onPageChange?.(next.pageIndex + 1)
      } else {
        setClientPagination(next)
      }
    },
    manualPagination,
    manualFiltering,
    pageCount: manualPagination
      ? Math.max(1, Math.ceil((total ?? 0) / (pageSize || 1)))
      : undefined,
    rowCount: manualPagination ? total : undefined,
    getCoreRowModel: getCoreRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    defaultColumn: {
      minSize: 60,
      size: 150,
      maxSize: 640,
    },
  })
  // 列宽基准取各列 minSize（最小可读宽）：固定布局下它是列的基准宽度，
  // 表格在容器内拉伸时多余空间分配给各列；容器小于最小宽之和时才横向滚动。
  const minTotalSize = table
    .getVisibleLeafColumns()
    .reduce((sum, column) => sum + (column.columnDef.minSize ?? 60), 0)
  const columnWidth = (minSize?: number) => `${minSize ?? 60}px`

  if (variant === 'rack') {
    const rows = table.getRowModel().rows
    const showPagination =
      rows.length > 0 || (manualPagination && (total ?? 0) > 0)
    return (
      <div
        className={`flex flex-1 flex-col ${inset ? 'gap-0' : 'gap-3'} ${className}`}
      >
        <div className={inset ? 'px-4 pt-4 pb-3 sm:px-6' : undefined}>
          {renderToolbar ? (
            renderToolbar(table)
          ) : (
            <DataTableToolbar
              table={table}
              searchPlaceholder={searchPlaceholder}
              hideSearch={hideSearch}
              filters={filters}
              actions={toolbarActions}
            />
          )}
        </div>
        <RackList
          testId='data-table-rack'
          kind={rackKind}
          className={inset ? 'px-4 sm:px-6' : undefined}
        >
          {rows.length ? (
            rows.map((row) => {
              const cells = row.getVisibleCells()
              const actionsCell = cells.find(
                (cell) => cell.column.id === 'actions'
              )
              const businessCells = cells.filter(
                (cell) =>
                  cell.column.id !== 'select' &&
                  cell.column.id !== 'actions' &&
                  !cell.column.columnDef.meta?.rackHidden
              )
              const titleCell =
                businessCells.find(
                  (cell) => cell.column.id === rackPrimaryColumnId
                ) ?? businessCells[0]
              const fieldCells = businessCells.filter(
                (cell) => cell !== titleCell
              )
              return (
                <RackRow
                  key={row.id}
                  testId='data-table-rack-row'
                  selected={
                    enableRowSelection ? row.getIsSelected() : undefined
                  }
                  onSelectedChange={
                    enableRowSelection
                      ? (selected) => row.toggleSelected(selected)
                      : undefined
                  }
                  selectLabel={t('common.selectRow')}
                  title={
                    titleCell
                      ? flexRender(
                          titleCell.column.columnDef.cell,
                          titleCell.getContext()
                        )
                      : null
                  }
                  fields={fieldCells.map((cell) => {
                    const header = cell.column.columnDef.header
                    return {
                      label:
                        typeof header === 'string' && header
                          ? header
                          : cell.column.id,
                      value: flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext()
                      ),
                      span: cell.column.columnDef.meta?.rackSpan,
                      width: cell.column.columnDef.meta?.rackWidth,
                      multiline: cell.column.columnDef.meta?.rackMultiline,
                    }
                  })}
                  actions={
                    actionsCell
                      ? flexRender(
                          actionsCell.column.columnDef.cell,
                          actionsCell.getContext()
                        )
                      : undefined
                  }
                />
              )
            })
          ) : (
            <div
              className='flex h-24 items-center justify-center text-sm text-muted-foreground'
              aria-busy={isLoading || undefined}
            >
              {isLoading ? (
                <span className='inline-flex items-center gap-2'>
                  <Loader2 className='size-4 animate-spin' />
                  {t('common.loading')}
                </span>
              ) : (
                <EmptyState
                  message={emptyMessage || t('common.noResults')}
                  action={emptyAction}
                />
              )}
            </div>
          )}
        </RackList>
        {enableRowSelection && renderBulkActions ? (
          <DataTableBulkActions table={table} entityName={bulkEntityName}>
            {renderBulkActions(table)}
          </DataTableBulkActions>
        ) : null}
        {showPagination ? (
          <DataTablePagination
            table={table}
            totalRows={manualPagination ? total : undefined}
            className={cn('border-t', inset ? 'px-4 py-3 sm:px-6' : 'pt-3')}
          />
        ) : null}
      </div>
    )
  }

  return (
    <div className={`flex flex-1 flex-col gap-4 ${className}`}>
      <div className={inset ? 'px-4 pt-4 sm:px-6' : undefined}>
        {renderToolbar ? (
          renderToolbar(table)
        ) : (
          <DataTableToolbar
            table={table}
            searchPlaceholder={searchPlaceholder}
            hideSearch={hideSearch}
            filters={filters}
            actions={toolbarActions}
          />
        )}
      </div>
      <div className={cn('overflow-hidden', !inset && 'rounded-md border')}>
        <Table
          className='table-fixed'
          style={{ minWidth: `${minTotalSize}px` }}
        >
          <TableHeader>
            {table.getHeaderGroups().map((group) => (
              <TableRow key={group.id}>
                {group.headers.map((header) => {
                  const headerDef = header.column.columnDef
                  const sorted = header.column.getIsSorted()
                  const sortable =
                    headerDef.enableSorting !== false &&
                    typeof headerDef.header === 'string' &&
                    headerDef.header.length > 0
                  return (
                    <TableHead
                      key={header.id}
                      aria-sort={
                        header.column.getCanSort() && sorted
                          ? sorted === 'asc'
                            ? 'ascending'
                            : 'descending'
                          : undefined
                      }
                      style={{
                        width: columnWidth(header.column.columnDef.minSize),
                      }}
                      className={cn(
                        header.column.columnDef.meta?.thClassName,
                        header.column.columnDef.meta?.className,
                        header.column.id === 'actions' &&
                          'sticky end-0 z-10 bg-background shadow-sm'
                      )}
                    >
                      {header.isPlaceholder ? null : sortable ? (
                        <DataTableColumnHeader
                          column={header.column}
                          title={headerDef.header as string}
                        />
                      ) : headerDef.header ? (
                        flexRender(headerDef.header, header.getContext())
                      ) : header.column.id === 'actions' ? (
                        // 操作列无可见表头，补读屏文案，避免空表头（WCAG 1.3.1）
                        <span className='sr-only'>{t('common.actions')}</span>
                      ) : null}
                    </TableHead>
                  )
                })}
              </TableRow>
            ))}
          </TableHeader>
          <TableBody>
            {table.getRowModel().rows.length ? (
              table.getRowModel().rows.map((row) => (
                <TableRow key={row.id}>
                  {row.getVisibleCells().map((cell) => (
                    <TableCell
                      key={cell.id}
                      style={{
                        width: columnWidth(cell.column.columnDef.minSize),
                      }}
                      className={cn(
                        cell.column.columnDef.meta?.tdClassName,
                        cell.column.columnDef.meta?.className,
                        cell.column.id === 'actions' &&
                          'sticky end-0 z-10 bg-background shadow-sm'
                      )}
                    >
                      {flexRender(
                        cell.column.columnDef.cell,
                        cell.getContext()
                      )}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            ) : isLoading ? (
              <TableRow>
                <TableCell
                  colSpan={table.getVisibleLeafColumns().length}
                  className='h-24 text-center'
                  aria-busy='true'
                >
                  <span className='inline-flex items-center gap-2 text-sm text-muted-foreground'>
                    <Loader2 className='size-4 animate-spin' />
                    {t('common.loading')}
                  </span>
                </TableCell>
              </TableRow>
            ) : (
              <TableRow>
                <TableCell
                  colSpan={table.getVisibleLeafColumns().length}
                  className='h-24 text-center'
                >
                  <EmptyState
                    message={emptyMessage || t('common.noResults')}
                    action={emptyAction}
                  />
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      {enableRowSelection && renderBulkActions ? (
        <DataTableBulkActions table={table} entityName={bulkEntityName}>
          {renderBulkActions(table)}
        </DataTableBulkActions>
      ) : null}
      <DataTablePagination
        table={table}
        totalRows={manualPagination ? total : undefined}
        className={cn('border-t', inset ? 'px-4 py-3 sm:px-6' : 'pt-3')}
      />
    </div>
  )
}
