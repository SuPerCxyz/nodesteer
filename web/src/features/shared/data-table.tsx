import { useState } from 'react'
import {
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
  type VisibilityState,
} from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { DataTablePagination, DataTableToolbar } from '@/components/data-table'

export function DataTable<TData>({
  data,
  columns,
  searchPlaceholder = 'Filter...',
  className = '',
  hideSearch = false,
}: {
  data: TData[]
  columns: ColumnDef<TData>[]
  searchPlaceholder?: string
  className?: string
  hideSearch?: boolean
}) {
  const { t } = useTranslation()
  const [sorting, setSorting] = useState<SortingState>([])
  const [columnVisibility, setColumnVisibility] = useState<VisibilityState>({})
  const [globalFilter, setGlobalFilter] = useState('')
  const table = useReactTable({
    data,
    columns,
    state: { sorting, columnVisibility, globalFilter },
    onSortingChange: setSorting,
    onColumnVisibilityChange: setColumnVisibility,
    onGlobalFilterChange: setGlobalFilter,
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
                {group.headers.map((header) => (
                  <TableHead
                    key={header.id}
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
                    {header.isPlaceholder
                      ? null
                      : flexRender(
                          header.column.columnDef.header,
                          header.getContext()
                        )}
                  </TableHead>
                ))}
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
      <DataTablePagination table={table} />
    </div>
  )
}
