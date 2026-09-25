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
    <div className={cn('flex min-w-0 items-center justify-start', className)}>
      <Button
        variant='ghost'
        size='sm'
        // 负外边距抵消 th 的 px-2，ps-2 复位后标签与单元格文本左对齐；
        // has-[>svg]:px-2.5 特异性更高，这里用内联样式固定内边距。
        style={{
          marginInlineStart: '-0.5rem',
          paddingInlineStart: '0.5rem',
          paddingInlineEnd: '0.25rem',
        }}
        className='h-8 w-[calc(100%+0.5rem)] shrink-0 justify-start gap-1 rounded-md data-[state=open]:bg-accent'
        onClick={() => column.toggleSorting()}
        title={`${title}: ${nextAction}`}
        aria-label={`${title}: ${nextAction}`}
      >
        <span className='truncate'>{title}</span>
        {sorted === 'desc' ? (
          <ArrowDownIcon className='size-4 shrink-0' />
        ) : sorted === 'asc' ? (
          <ArrowUpIcon className='size-4 shrink-0' />
        ) : (
          <CaretSortIcon className='size-4 shrink-0' />
        )}
      </Button>
    </div>
  )
}
