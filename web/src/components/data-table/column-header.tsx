import {
  ArrowDownIcon,
  ArrowUpIcon,
  CaretSortIcon,
} from '@radix-ui/react-icons'
import { type Column } from '@tanstack/react-table'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'
import { Button } from '@/components/ui/button'

type DataTableColumnHeaderProps<TData, TValue> =
  React.HTMLAttributes<HTMLDivElement> & {
    column: Column<TData, TValue>
    title: string
  }

/** 可排序表头：点击在升序 → 降序 → 清除排序之间循环 */
export function DataTableColumnHeader<TData, TValue>({
  column,
  title,
  className,
}: DataTableColumnHeaderProps<TData, TValue>) {
  const { t } = useTranslation()
  if (!column.getCanSort()) {
    return <div className={cn(className)}>{title}</div>
  }
  const sorted = column.getIsSorted()
  const nextAction =
    sorted === 'asc'
      ? t('common.sortDescending')
      : sorted === 'desc'
        ? t('common.clearSorting')
        : t('common.sortAscending')
  return (
    <div className={cn('flex items-center justify-start', className)}>
      <Button
        variant='ghost'
        size='sm'
        className='h-8 max-w-full data-[state=open]:bg-accent'
        onClick={() => column.toggleSorting()}
        title={`${title}: ${nextAction}`}
        aria-label={`${title}: ${nextAction}`}
      >
        <span className='truncate'>{title}</span>
        {sorted === 'desc' ? (
          <ArrowDownIcon className='ms-2 size-4 shrink-0' />
        ) : sorted === 'asc' ? (
          <ArrowUpIcon className='ms-2 size-4 shrink-0' />
        ) : (
          <CaretSortIcon className='ms-2 size-4 shrink-0' />
        )}
      </Button>
    </div>
  )
}
