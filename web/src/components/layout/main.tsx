import { cn } from '@/lib/utils'

type MainProps = React.HTMLAttributes<HTMLElement> & {
  fixed?: boolean
  ref?: React.Ref<HTMLElement>
}

export function Main({ fixed, className, ...props }: MainProps) {
  return (
    <main
      data-layout={fixed ? 'fixed' : 'auto'}
      className={cn(
        'w-full px-4 pt-5 pb-6',

        // 统一内容宽度：占满可用宽度，超宽屏上限 2400px 并居中
        'mx-auto max-w-[2400px]',

        // If layout is fixed, make the main container flex and grow
        fixed && 'flex grow flex-col overflow-hidden',
        className
      )}
      {...props}
    />
  )
}
