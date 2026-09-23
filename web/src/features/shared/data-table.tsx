import { useEffect, useState } from 'react'
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type PaginationState,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import {
  readTablePageSize,
  readTableView,
  writeTableView,
} from '@/lib/table-view'
import { cn } from '@/lib/utils'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  DataTableColumnHeader,
  DataTablePagination,
  DataTableToolbar,
} from '@/components/data-table'

export function DataTable<TData>({
  data,
  columns,
  searchPlaceholder = 'Filter...',
  className = '',
  hideSearch = false,
  manualPagination = false,
  total,
  page = 1,
  pageSize = 10,
  onPageChange,
  onPageSizeChange,
  storageKey,
}: {
  data: TData[]
  columns: ColumnDef<TData>[]
  searchPlaceholder?: string
  className?: string
  hideSearch?: boolean
  /** 服务端分页：data 仅为当前页，分页状态由 page/pageSize 受控传入 */
  manualPagination?: boolean
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
}) {
  const { t } = useTranslation()
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>(
    () =>
      (storageKey ? readTableView(storageKey).columnVisibility : undefined) ??
      {}
  )
  const [globalFilter, setGlobalFilter] = useState('')
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
  const table = useReactTable({
    data,
    columns,
    state: { sorting, columnVisibility, globalFilter, pagination },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    onGlobalFilterChange: setGlobalFilter,
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
  const totalSize = table.getTotalSize()
  const columnWidth = (size: number) => `${(size / totalSize) * 100}%`

  return (
    <div className={`flex flex-1 flex-col gap-4 ${className}`}>
      <DataTableToolbar
        table={table}
        searchPlaceholder={searchPlaceholder}
        hideSearch={hideSearch}
      />
      <div className='overflow-hidden rounded-md border'>
        <Table className='table-fixed' style={{ minWidth: `${totalSize}px` }}>
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
                        width: columnWidth(header.column.getSize()),
                        minWidth: `${header.column.columnDef.minSize || 60}px`,
                      }}
                      className={cn(
                        header.column.columnDef.meta?.thClassName,
                        header.column.columnDef.meta?.className,
                        header.column.columnDef.meta?.align === 'center' &&
                          'text-center',
                        header.column.columnDef.meta?.align === 'end' &&
                          'text-end',
                        header.column.id === 'actions' &&
                          'sticky end-0 z-10 bg-background shadow-sm'
                      )}
                    >
                      {header.isPlaceholder ? null : sortable ? (
                        <DataTableColumnHeader
                          column={header.column}
                          title={headerDef.header as string}
                          className={cn(
                            headerDef.meta?.align === 'center' &&
                              'justify-center',
                            headerDef.meta?.align === 'end' && 'justify-end'
                          )}
                        />
                      ) : (
                        flexRender(headerDef.header, header.getContext())
                      )}
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
                        width: columnWidth(cell.column.getSize()),
                        minWidth: `${cell.column.columnDef.minSize || 60}px`,
                      }}
                      className={cn(
                        cell.column.columnDef.meta?.tdClassName,
                        cell.column.columnDef.meta?.className,
                        cell.column.columnDef.meta?.align === 'center' &&
                          'text-center',
                        cell.column.columnDef.meta?.align === 'end' &&
                          'text-end',
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
            ) : (
              <TableRow>
                <TableCell
                  colSpan={table.getVisibleLeafColumns().length}
                  className='h-24 text-center'
                >
                  {t('common.noResults')}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      <DataTablePagination
        table={table}
        totalRows={manualPagination ? total : undefined}
      />
    </div>
  )
}
