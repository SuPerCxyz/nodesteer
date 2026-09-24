import * as React from 'react'
import { ChevronsLeftRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { cn } from '@/lib/utils'

function Table({ className, ...props }: React.ComponentProps<'table'>) {
  const { t } = useTranslation()
  const containerRef = React.useRef<HTMLDivElement>(null)
  const [showScrollHint, setShowScrollHint] = React.useState(false)

  React.useEffect(() => {
    const container = containerRef.current
    if (!container) return

    const updateScrollHint = () => {
      const maxScroll = container.scrollWidth - container.clientWidth
      setShowScrollHint(maxScroll > 1 && container.scrollLeft < maxScroll - 1)
    }

    updateScrollHint()
    container.addEventListener('scroll', updateScrollHint, { passive: true })
    window.addEventListener('resize', updateScrollHint)
    const observer =
      typeof ResizeObserver === 'undefined'
        ? null
        : new ResizeObserver(updateScrollHint)
    observer?.observe(container)
    if (container.firstElementChild) {
      observer?.observe(container.firstElementChild)
    }

    return () => {
      container.removeEventListener('scroll', updateScrollHint)
      window.removeEventListener('resize', updateScrollHint)
      observer?.disconnect()
    }
  }, [])

  return (
    <div
      ref={containerRef}
      data-slot='table-container'
      className='relative w-full overflow-x-auto'
    >
      {showScrollHint && (
        <div
          className='pointer-events-none absolute end-1 bottom-1 z-10 inline-flex items-center gap-1 rounded-md border bg-background/95 px-1.5 py-1 text-[10px] text-muted-foreground shadow-sm'
          aria-hidden='true'
        >
          <ChevronsLeftRight className='size-3.5' />
          {t('common.scrollTable')}
        </div>
      )}
      <table
        data-slot='table'
        className={cn('w-full caption-bottom text-sm', className)}
        {...props}
      />
    </div>
  )
}

function TableHeader({ className, ...props }: React.ComponentProps<'thead'>) {
  return (
    <thead
      data-slot='table-header'
      className={cn('[&_tr]:border-b', className)}
      {...props}
    />
  )
}

function TableBody({ className, ...props }: React.ComponentProps<'tbody'>) {
  return (
    <tbody
      data-slot='table-body'
      className={cn('[&_tr:last-child]:border-0', className)}
      {...props}
    />
  )
}

function TableFooter({ className, ...props }: React.ComponentProps<'tfoot'>) {
  return (
    <tfoot
      data-slot='table-footer'
      className={cn(
        'border-t bg-muted/50 font-medium [&>tr]:last:border-b-0',
        className
      )}
      {...props}
    />
  )
}

function TableRow({ className, ...props }: React.ComponentProps<'tr'>) {
  return (
    <tr
      data-slot='table-row'
      className={cn(
        'border-b transition-colors hover:bg-muted/50 data-[state=selected]:bg-muted',
        className
      )}
      {...props}
    />
  )
}

function TableHead({ className, ...props }: React.ComponentProps<'th'>) {
  return (
    <th
      data-slot='table-head'
      className={cn(
        'h-10 overflow-hidden px-2 text-start align-middle font-medium text-ellipsis whitespace-nowrap text-foreground *:[[role=checkbox]]:translate-y-0.5',
        className
      )}
      {...props}
    />
  )
}

function TableCell({ className, ...props }: React.ComponentProps<'td'>) {
  return (
    <td
      data-slot='table-cell'
      className={cn(
        'overflow-hidden p-2 align-middle text-ellipsis whitespace-nowrap *:[[role=checkbox]]:translate-y-0.5',
        className
      )}
      {...props}
    />
  )
}

function TableCaption({
  className,
  ...props
}: React.ComponentProps<'caption'>) {
  return (
    <caption
      data-slot='table-caption'
      className={cn('mt-4 text-sm text-muted-foreground', className)}
      {...props}
    />
  )
}

export {
  Table,
  TableHeader,
  TableBody,
  TableFooter,
  TableHead,
  TableRow,
  TableCell,
  TableCaption,
}
