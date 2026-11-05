import {
  Children,
  createContext,
  isValidElement,
  useContext,
  type CSSProperties,
  type ReactNode,
} from 'react'
import { cn } from '@/lib/utils'
import { Checkbox } from '@/components/ui/checkbox'

export type RackTone = 'success' | 'warning' | 'danger' | 'neutral'

const barTone: Record<RackTone, string> = {
  success: 'bg-emerald-600',
  warning: 'bg-amber-500',
  danger: 'bg-red-600',
  neutral: 'bg-muted-foreground/50',
}

export type RackField = {
  label: string
  value: ReactNode
  tone?: RackTone
  mono?: boolean
  testId?: string
  bar?: {
    value?: number | null
    tone?: RackTone
    testId?: string
  }
}

export type RackTag = {
  label: string
  value: ReactNode
  icon?: ReactNode
  title?: string
}

function clampPercent(value?: number | null) {
  if (typeof value !== 'number' || !Number.isFinite(value)) return 0
  return Math.min(100, Math.max(0, value))
}

/**
 * 同一 RackList 内只要存在操作按钮，就给所有行保留同一操作列。
 * 这样条件操作（如仅部分告警事件可确认）不会让其他行字段宽度漂移；
 * 整个列表都没有操作时不保留空列，避免浪费横向空间。
 */
const RackActionsContext = createContext(false)

function hasRowActions(children: ReactNode) {
  return Children.toArray(children).some(
    (child) =>
      isValidElement<{ actions?: ReactNode }>(child) &&
      Boolean(child.props.actions)
  )
}

export function RackList({
  children,
  className,
  testId,
  style,
}: {
  children: ReactNode
  className?: string
  testId?: string
  /** 行内 CSS 变量（如标签列宽/子列模板）向所有 RackRow 继承。 */
  style?: CSSProperties
}) {
  return (
    <RackActionsContext.Provider value={hasRowActions(children)}>
      <div
        role='list'
        data-testid={testId}
        style={style}
        className={cn('divide-y divide-border/60', className)}
      >
        {children}
      </div>
    </RackActionsContext.Provider>
  )
}

export function RackField({
  label,
  value,
  tone = 'neutral',
  mono,
  testId,
  bar,
}: RackField) {
  return (
    <div
      data-slot='row-field'
      className='flex min-h-10 min-w-0 flex-col justify-center'
      data-testid={testId}
      data-tone={tone}
    >
      <div className='flex min-w-0 items-center gap-1.5'>
        <span
          data-slot='row-label'
          className='max-w-[50%] shrink-0 text-xs leading-4 font-medium [overflow-wrap:anywhere] text-muted-foreground'
        >
          {label}
        </span>
        <span
          className={cn(
            'min-w-0 font-medium [overflow-wrap:anywhere] break-words text-foreground tabular-nums [&_.truncate]:overflow-visible [&_.truncate]:[overflow-wrap:anywhere] [&_.truncate]:text-clip [&_.truncate]:whitespace-normal',
            bar || mono ? 'text-xs leading-4' : 'text-sm leading-5',
            mono && 'font-mono'
          )}
        >
          {value == null || value === '' ? '—' : value}
        </span>
      </div>
      {bar && (
        <div className='mt-2 h-2 w-full overflow-hidden rounded-full bg-muted-foreground/20 ring-1 ring-border/80 ring-inset dark:bg-muted-foreground/30 dark:ring-border'>
          <div
            data-testid={bar.testId}
            className={cn('h-full rounded-full', barTone[bar.tone ?? tone])}
            style={{ width: `${clampPercent(bar.value)}%` }}
          />
        </div>
      )}
    </div>
  )
}

export function RackFields({
  fields,
  className,
}: {
  fields: RackField[]
  className?: string
}) {
  if (!fields.length) return null
  // 列数只由字段数量决定（同一 rack 内字段数固定），不随字段值宽度变化。
  const columns =
    fields.length >= 5
      ? 'grid-cols-2 sm:grid-cols-3 xl:grid-cols-4'
      : fields.length === 4
        ? 'grid-cols-2 sm:grid-cols-3 xl:grid-cols-4'
        : fields.length === 3
          ? 'grid-cols-2 sm:grid-cols-3'
          : fields.length === 2
            ? 'grid-cols-2'
            : 'grid-cols-1'
  return (
    <div
      className={cn(
        'grid min-w-0 items-center gap-x-4 gap-y-2',
        columns,
        className
      )}
    >
      {fields.map((field) => (
        <RackField key={field.label} {...field} />
      ))}
    </div>
  )
}

export function RackTags({
  items,
  className,
}: {
  items: RackTag[]
  className?: string
}) {
  if (!items.length) return null
  return (
    <div className={cn('flex flex-wrap items-center gap-1.5', className)}>
      {items.map((item) => (
        <span
          key={`${item.label}-${String(item.value)}`}
          title={item.title}
          className='inline-flex max-w-full items-center gap-1 text-[11px] whitespace-nowrap text-muted-foreground'
        >
          {item.icon}
          <span className='shrink-0'>{item.label}</span>
          <span className='truncate font-medium text-foreground tabular-nums'>
            {item.value}
          </span>
        </span>
      ))}
    </div>
  )
}

/** 常规数据行主副文案共用层级，内容可自然增高。 */
export function RowText({
  primary,
  secondary,
}: {
  primary: ReactNode
  secondary?: ReactNode
}) {
  return (
    <div className='min-w-0 space-y-0.5 [&_.truncate]:overflow-visible [&_.truncate]:[overflow-wrap:anywhere] [&_.truncate]:text-clip [&_.truncate]:whitespace-normal'>
      <div
        data-slot='row-primary'
        className='min-w-0 text-sm leading-5 font-medium [overflow-wrap:anywhere] break-words'
      >
        {primary}
      </div>
      {secondary != null && secondary !== '' && (
        <div
          data-slot='row-secondary'
          className='flex min-w-0 flex-wrap items-center gap-1 text-xs leading-4 [overflow-wrap:anywhere] break-words text-muted-foreground'
        >
          {secondary}
        </div>
      )}
    </div>
  )
}

export function RackRow({
  leading,
  title,
  subtitle,
  fields,
  tags,
  tagsContent,
  actions,
  selected,
  onSelectedChange,
  selectLabel = '选择该行',
  layout = 'inline',
  testId,
  className,
}: {
  leading?: ReactNode
  title: ReactNode
  subtitle?: ReactNode
  fields?: RackField[]
  tags?: RackTag[]
  /** 自定义标签区内容（用于需要固定内部子列的场景），优先于 tags。 */
  tagsContent?: ReactNode
  actions?: ReactNode
  selected?: boolean
  onSelectedChange?: (selected: boolean) => void
  selectLabel?: string
  /** inline：单行流式；node：宽屏单行、窄屏标签换到第二行。 */
  layout?: 'inline' | 'node'
  testId?: string
  className?: string
}) {
  const rackHasActions = useContext(RackActionsContext)
  const identity = (
    <div
      data-slot='row-identity'
      className='flex min-h-10 min-w-0 items-center gap-2'
    >
      {onSelectedChange && (
        <Checkbox
          checked={Boolean(selected)}
          onCheckedChange={(value) => onSelectedChange(Boolean(value))}
          aria-label={selectLabel}
          className='shrink-0'
        />
      )}
      {leading}
      <RowText primary={title} secondary={subtitle} />
    </div>
  )

  if (layout === 'node') {
    return (
      <div
        role='listitem'
        data-testid={testId}
        data-selected={selected ? 'true' : undefined}
        className={cn(
          // 除操作列外全部使用固定值或 fr 比例：每行独立成 grid，
          // 任何 auto 内容列（尤其是宽度随标签值变化的列）都会让左侧字段重新分配宽度。
          'group grid grid-cols-[minmax(0,1fr)_104px] items-center gap-x-4 gap-y-2 rounded-md px-1 py-2.5 transition-colors hover:bg-muted/40',
          'xl:grid-cols-[minmax(150px,0.7fr)_minmax(0,2.3fr)_104px]',
          '2xl:grid-cols-[minmax(170px,0.6fr)_minmax(0,1.9fr)_var(--rack-tags-width,28rem)_104px]',
          selected && 'bg-muted/30',
          className
        )}
      >
        <div className='col-start-1 row-start-1 min-w-0'>{identity}</div>
        {fields?.length ? (
          <RackFields
            fields={fields}
            className='col-span-2 row-start-2 w-full grid-cols-3 sm:grid-cols-3 xl:col-span-1 xl:col-start-2 xl:row-start-1 xl:grid-cols-3'
          />
        ) : null}
        {tagsContent || tags?.length ? (
          <div className='col-span-2 row-start-3 min-w-0 xl:col-span-3 xl:row-start-2 2xl:col-span-1 2xl:col-start-3 2xl:row-start-1'>
            {tagsContent ?? <RackTags items={tags || []} />}
          </div>
        ) : null}
        {actions ? (
          <div
            data-slot='row-actions'
            className='col-span-1 col-start-2 row-start-1 flex min-h-10 min-w-0 flex-wrap items-center justify-end gap-1.5 xl:col-start-3 2xl:col-start-4'
          >
            {actions}
          </div>
        ) : null}
      </div>
    )
  }

  const reserveActions = rackHasActions || Boolean(actions)
  return (
    <div
      role='listitem'
      data-testid={testId}
      data-selected={selected ? 'true' : undefined}
      className={cn(
        // 固定列基准：标题、字段、操作三列在同一断点下宽度一致，
        // 不用内容驱动的 auto 列，避免每行字段起点漂移；窄屏单列堆叠避免横向滚动。
        'group grid grid-cols-1 items-center gap-x-4 gap-y-2 rounded-md px-1 py-2.5 transition-colors hover:bg-muted/40',
        reserveActions
          ? 'sm:grid-cols-[minmax(140px,0.7fr)_minmax(0,2.6fr)_minmax(160px,0.85fr)]'
          : 'sm:grid-cols-[minmax(140px,0.7fr)_minmax(0,2.6fr)]',
        selected && 'bg-muted/30',
        className
      )}
    >
      <div className='col-start-1 min-w-0 sm:row-start-1'>{identity}</div>
      {fields?.length ? (
        <RackFields
          fields={fields}
          className='w-full sm:col-span-1 sm:col-start-2 sm:row-start-1'
        />
      ) : null}
      {tags?.length ? (
        <div className='min-w-0 sm:col-span-1 sm:col-start-2 sm:row-start-2'>
          <RackTags items={tags} />
        </div>
      ) : null}
      {actions && (
        <div
          data-slot='row-actions'
          className='flex min-h-10 min-w-0 flex-wrap items-center justify-end gap-1.5 sm:col-start-3 sm:row-start-1'
        >
          {actions}
        </div>
      )}
    </div>
  )
}
