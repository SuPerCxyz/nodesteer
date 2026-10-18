import {
  ChevronLeftIcon,
  ChevronRightIcon,
  DoubleArrowLeftIcon,
  DoubleArrowRightIcon,
} from '@radix-ui/react-icons'
import { type Table } from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { PAGE_SIZE_OPTIONS } from '@/lib/table-view'
import { cn, getPageNumbers } from '@/lib/utils'
import { Button } from '@/components/ui/button'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

type DataTablePaginationProps<TData> = {
  table: Table<TData>
  className?: string
  /** 手动（服务端）分页时的匹配总条数 */
  totalRows?: number
}

export function DataTablePagination<TData>({
  table,
  className,
  totalRows,
}: DataTablePaginationProps<TData>) {
  const { t } = useTranslation()
  const currentPage = table.getState().pagination.pageIndex + 1
  const totalPages = table.getPageCount()
  const pageNumbers = getPageNumbers(currentPage, totalPages)
  const currentPageSize = table.getState().pagination.pageSize
  // 当前值不在标准选项内（如 URL 传入的越界值）时补一个选项，避免触发器显示空白
  const pageSizeOptions = PAGE_SIZE_OPTIONS.includes(currentPageSize)
    ? PAGE_SIZE_OPTIONS
    : [...PAGE_SIZE_OPTIONS, currentPageSize].sort((a, b) => a - b)

  return (
    <div
      className={cn(
        'flex flex-col gap-3 overflow-clip px-2 sm:flex-row sm:items-center sm:justify-between',
        className
      )}
      style={{ overflowClipMargin: 1 }}
    >
      <div className='flex items-center justify-between gap-3'>
        <div className='flex items-center gap-2'>
          <Select
            value={`${currentPageSize}`}
            onValueChange={(value) => {
              table.setPageSize(Number(value))
            }}
          >
            <SelectTrigger
              className='h-8 w-17.5'
              aria-label={t('common.rowsPerPage')}
            >
              <SelectValue placeholder={currentPageSize} />
            </SelectTrigger>
            <SelectContent side='top'>
              {pageSizeOptions.map((pageSize) => (
                <SelectItem key={pageSize} value={`${pageSize}`}>
                  {pageSize}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <p className='hidden text-sm font-medium sm:block'>
            {t('common.rowsPerPage')}
          </p>
          {totalRows !== undefined ? (
            <p className='hidden text-sm font-medium text-muted-foreground sm:block'>
              {t('common.totalRows', { total: totalRows })}
            </p>
          ) : null}
        </div>
        <div className='text-sm font-medium sm:hidden'>
          {t('common.pageOf', { current: currentPage, total: totalPages })}
        </div>
      </div>

      <div className='flex items-center justify-end gap-3'>
        <div className='hidden text-sm font-medium sm:block'>
          {t('common.pageOf', { current: currentPage, total: totalPages })}
        </div>
        <div className='flex items-center gap-2'>
          <Button
            variant='outline'
            className='size-8 p-0 @max-md/content:hidden'
            onClick={() => table.setPageIndex(0)}
            disabled={!table.getCanPreviousPage()}
          >
            <span className='sr-only'>{t('common.firstPage')}</span>
            <DoubleArrowLeftIcon className='h-4 w-4' />
          </Button>
          <Button
            variant='outline'
            className='size-8 p-0'
            onClick={() => table.previousPage()}
            disabled={!table.getCanPreviousPage()}
          >
            <span className='sr-only'>{t('common.previousPage')}</span>
            <ChevronLeftIcon className='h-4 w-4' />
          </Button>

          {/* Page number buttons */}
          {pageNumbers.map((pageNumber, index) => (
            <div key={`${pageNumber}-${index}`} className='flex items-center'>
              {pageNumber === '...' ? (
                <span className='px-1 text-sm text-muted-foreground'>...</span>
              ) : (
                <Button
                  variant={currentPage === pageNumber ? 'default' : 'outline'}
                  className='h-8 min-w-8 px-2'
                  onClick={() => table.setPageIndex((pageNumber as number) - 1)}
                >
                  <span className='sr-only'>
                    {t('common.goToPage', { page: pageNumber })}
                  </span>
                  {pageNumber}
                </Button>
              )}
            </div>
          ))}

          <Button
            variant='outline'
            className='size-8 p-0'
            onClick={() => table.nextPage()}
            disabled={!table.getCanNextPage()}
          >
            <span className='sr-only'>{t('common.nextPage')}</span>
            <ChevronRightIcon className='h-4 w-4' />
          </Button>
          <Button
            variant='outline'
            className='size-8 p-0 @max-md/content:hidden'
            onClick={() => table.setPageIndex(table.getPageCount() - 1)}
            disabled={!table.getCanNextPage()}
          >
            <span className='sr-only'>{t('common.lastPage')}</span>
            <DoubleArrowRightIcon className='h-4 w-4' />
          </Button>
        </div>
      </div>
    </div>
  )
}
