import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center justify-center rounded-md border px-2 py-0.5 text-center text-xs leading-4 font-medium w-fit whitespace-nowrap shrink-0 [&>svg]:size-3 gap-1 [&>svg]:pointer-events-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px] aria-invalid:ring-destructive/20 dark:aria-invalid:ring-destructive/40 aria-invalid:border-destructive transition-[color,box-shadow] overflow-hidden',
  {
    variants: {
      variant: {
        default:
          'border-blue-600 bg-blue-600 text-white dark:border-blue-600 dark:bg-blue-600 dark:text-white [a&]:hover:bg-blue-700',
        secondary:
          'border-slate-600 bg-slate-600 text-white [a&]:hover:bg-slate-700 dark:border-slate-500 dark:bg-slate-500 dark:text-white [a&]:hover:bg-slate-600',
        destructive:
          'border-red-600 bg-red-600 text-white [a&]:hover:bg-red-700 focus-visible:ring-destructive/20 dark:border-red-600 dark:bg-red-600 dark:text-white [a&]:hover:bg-red-700 dark:focus-visible:ring-destructive/40',
        outline:
          'border-slate-600 bg-slate-600 text-white [a&]:hover:bg-slate-700 dark:border-slate-500 dark:bg-slate-500 dark:text-white [a&]:hover:bg-slate-600',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  }
)

function Badge({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<'span'> &
  VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : 'span'

  return (
    <Comp
      data-slot='badge'
      className={cn(badgeVariants({ variant }), className)}
      {...props}
    />
  )
}

export { Badge, badgeVariants }
