import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from 'react'
import {
  Check,
  Clipboard,
  Inbox,
  Loader2,
  MoreHorizontal,
  RotateCcw,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api, type ApiError } from '@/lib/api'
import { copyText } from '@/lib/clipboard'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import { Separator } from '@/components/ui/separator'
import { ConfirmDialog } from '@/components/confirm-dialog'

export function StatusBadge({ status }: { status: string }) {
  const { i18n } = useTranslation()
  const key = status.toLowerCase().replace(/[-\s]/g, '_')
  const variant = [
    'online',
    'healthy',
    'enabled',
    'success',
    'completed',
    'synced',
  ].includes(key)
    ? 'secondary'
    : [
          'offline',
          'unhealthy',
          'failed',
          'error',
          'timed_out',
          'timeout',
        ].includes(key)
      ? 'destructive'
      : ['running', 'busy', 'info'].includes(key)
        ? 'default'
        : ['degraded', 'blocked', 'outdated'].includes(key)
          ? 'secondary'
          : 'outline'
  const labels: Record<string, { zh: string; en: string }> = {
    online: { zh: '在线', en: 'Online' },
    offline: { zh: '离线', en: 'Offline' },
    busy: { zh: '忙碌', en: 'Busy' },
    degraded: { zh: '降级', en: 'Degraded' },
    healthy: { zh: '健康', en: 'Healthy' },
    unhealthy: { zh: '不健康', en: 'Unhealthy' },
    enabled: { zh: '已启用', en: 'Enabled' },
    disabled: { zh: '已禁用', en: 'Disabled' },
    stopped: { zh: '已停止', en: 'Stopped' },
    pending: { zh: '等待中', en: 'Pending' },
    running: { zh: '运行中', en: 'Running' },
    success: { zh: '成功', en: 'Success' },
    completed: { zh: '已完成', en: 'Completed' },
    failed: { zh: '失败', en: 'Failed' },
    error: { zh: '错误', en: 'Error' },
    timed_out: { zh: '超时', en: 'Timed out' },
    timeout: { zh: '超时', en: 'Timeout' },
    cancelled: { zh: '已取消', en: 'Cancelled' },
    canceled: { zh: '已取消', en: 'Canceled' },
    skipped: { zh: '已跳过', en: 'Skipped' },
    blocked: { zh: '已阻断', en: 'Blocked' },
    synced: { zh: '已同步', en: 'Synced' },
    outdated: { zh: '已过期', en: 'Outdated' },
    unknown: { zh: '未知', en: 'Unknown' },
  }
  const label =
    labels[key]?.[i18n.language === 'en' ? 'en' : 'zh'] ||
    status.replace(/_/g, ' ')
  const colorClass = [
    'online',
    'healthy',
    'enabled',
    'success',
    'completed',
    'synced',
  ].includes(key)
    ? 'border-emerald-600 bg-emerald-600 text-white dark:border-emerald-500 dark:bg-emerald-500 dark:text-white'
    : [
          'offline',
          'unhealthy',
          'failed',
          'error',
          'timed_out',
          'timeout',
        ].includes(key)
      ? 'border-red-600 bg-red-600 text-white dark:border-red-500 dark:bg-red-500 dark:text-white'
      : ['running', 'busy', 'info'].includes(key)
        ? 'border-blue-600 bg-blue-600 text-white dark:border-blue-500 dark:bg-blue-500 dark:text-white'
        : ['degraded', 'blocked', 'outdated'].includes(key)
          ? 'border-amber-700 bg-amber-700 text-white dark:border-amber-600 dark:bg-amber-600 dark:text-white'
          : ''
  return (
    <Badge variant={variant} className={colorClass}>
      {label}
    </Badge>
  )
}

export function LoadingState() {
  const { t } = useTranslation()
  return (
    <div className='flex min-h-32 items-center justify-center gap-2 text-sm text-muted-foreground'>
      <Loader2 className='size-4 animate-spin' />
      {t('common.loading')}
    </div>
  )
}

export function EmptyState({
  message,
  action,
  className,
}: {
  message: string
  action?: React.ReactNode
  className?: string
}) {
  return (
    <div
      className={cn(
        'flex min-h-28 flex-col items-center justify-center gap-2 py-8 text-center text-sm text-muted-foreground',
        className
      )}
    >
      <Inbox className='size-5' />
      <span>{message}</span>
      {action}
    </div>
  )
}

export function ErrorState({
  error,
  onRetry,
}: {
  error: unknown
  onRetry?: () => void
}) {
  const { t } = useTranslation()
  const message = error instanceof Error ? error.message : t('common.noData')
  return (
    <div
      className='flex items-center justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive'
      role='alert'
    >
      <span>{message}</span>
      {onRetry && (
        <Button variant='ghost' size='sm' onClick={onRetry}>
          <RotateCcw className='me-1 size-3.5' />
          {t('common.retry')}
        </Button>
      )}
    </div>
  )
}

export function SearchToolbar({
  value,
  onChange,
  placeholder,
  children,
}: {
  value: string
  onChange: (value: string) => void
  placeholder: string
  children?: React.ReactNode
}) {
  return (
    <div className='flex flex-wrap items-center gap-2'>
      <Input
        className='h-8 w-full sm:w-64'
        value={value}
        onChange={(event) => onChange(event.target.value)}
        placeholder={placeholder}
      />
      {children}
    </div>
  )
}

export function TableCard({ children }: { children: React.ReactNode }) {
  return (
    <div className='overflow-hidden border'>
      <div className='overflow-x-auto'>{children}</div>
    </div>
  )
}

export function SectionCard({
  title,
  children,
  action,
}: {
  title: string
  children: React.ReactNode
  action?: React.ReactNode
}) {
  return (
    <Card>
      <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-3'>
        <CardTitle className='text-sm'>{title}</CardTitle>
        {action}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

export function MoreMenu({ children }: { children: React.ReactNode }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [confirmation, setConfirmation] = useState<{
    title?: string
    description?: string
    onSelect: () => void
  } | null>(null)
  const requestConfirmation = useCallback(
    (next: NonNullable<typeof confirmation>) => {
      setOpen(false)
      // Let Radix finish closing the menu before mounting the alert dialog.
      window.setTimeout(() => setConfirmation(next), 0)
    },
    []
  )
  const confirm = () => {
    const action = confirmation?.onSelect
    setConfirmation(null)
    action?.()
  }
  return (
    <MoreMenuContext.Provider value={{ requestConfirmation }}>
      <DropdownMenu open={open} onOpenChange={setOpen}>
        <DropdownMenuTrigger asChild>
          <Button
            variant='ghost'
            size='icon'
            className='size-8'
            aria-label={t('common.actions')}
          >
            <MoreHorizontal className='size-4' />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align='end'>{children}</DropdownMenuContent>
      </DropdownMenu>
      <ConfirmDialog
        open={confirmation !== null}
        onOpenChange={(nextOpen) => {
          if (!nextOpen) setConfirmation(null)
        }}
        title={confirmation?.title || t('common.delete')}
        desc={confirmation?.description || t('common.confirmAction')}
        cancelBtnText={t('common.cancel')}
        confirmText={t('common.delete')}
        destructive
        handleConfirm={confirm}
      />
    </MoreMenuContext.Provider>
  )
}

const MoreMenuContext = createContext<{
  requestConfirmation: (confirmation: {
    title?: string
    description?: string
    onSelect: () => void
  }) => void
} | null>(null)

export function DangerMenuItem({
  onSelect,
  title,
  description,
}: {
  onSelect: () => void
  title?: string
  description?: string
}) {
  const { t } = useTranslation()
  const moreMenu = useContext(MoreMenuContext)
  const [open, setOpen] = useState(false)
  return (
    <>
      <DropdownMenuItem
        variant='destructive'
        onSelect={(event) => {
          event.preventDefault()
          if (moreMenu) {
            moreMenu.requestConfirmation({ title, description, onSelect })
          } else {
            setOpen(true)
          }
        }}
      >
        {t('common.delete')}
      </DropdownMenuItem>
      {!moreMenu ? (
        <ConfirmDialog
          open={open}
          onOpenChange={setOpen}
          title={title || t('common.delete')}
          desc={description || t('common.confirmAction')}
          cancelBtnText={t('common.cancel')}
          confirmText={t('common.delete')}
          destructive
          handleConfirm={() => {
            onSelect()
            setOpen(false)
          }}
        />
      ) : null}
    </>
  )
}

export { DropdownMenuItem, DropdownMenuSeparator }

export function Copyable({
  value,
  length = 10,
}: {
  value?: string
  length?: number
}) {
  const { t } = useTranslation()
  const [copied, setCopied] = useState(false)
  if (!value) return <span className='text-muted-foreground'>-</span>
  const copy = async () => {
    try {
      const copied = await copyText(value)
      if (copied) {
        setCopied(true)
        toast.success(t('common.copied'))
        window.setTimeout(() => setCopied(false), 1200)
      } else {
        toast.info(t('common.copyManual'))
      }
    } catch {
      toast.error(t('common.copyFailed'))
    }
  }
  return (
    <span className='inline-flex max-w-full items-center gap-1 font-mono text-xs'>
      <span className='truncate' title={value}>
        {value.slice(0, length)}
        {value.length > length ? '…' : ''}
      </span>
      <Button
        variant='ghost'
        size='icon'
        className='size-6 shrink-0'
        onClick={() => void copy()}
        aria-label={t('common.copy')}
      >
        {copied ? (
          <Check className='size-3' />
        ) : (
          <Clipboard className='size-3' />
        )}
      </Button>
    </span>
  )
}

export function TimeValue({
  value,
  absolute = false,
}: {
  value?: string
  absolute?: boolean
}) {
  const { i18n } = useTranslation()
  if (!value) return <span className='text-muted-foreground'>-</span>
  const date = new Date(value)
  const locale = i18n.language === 'en' ? 'en-US' : 'zh-CN'
  const full = date.toLocaleString(locale)
  const relative = relativeTime(date, i18n.language === 'en')
  return Number.isNaN(date.getTime()) || date.getUTCFullYear() === 1 ? (
    <span className='text-muted-foreground'>-</span>
  ) : (
    <time title={full}>{absolute ? full : relative}</time>
  )
}

function relativeTime(date: Date, english: boolean) {
  const seconds = Math.round((Date.now() - date.getTime()) / 1000)
  const future = seconds < 0
  const amount = Math.abs(seconds)
  const unit = (value: number, en: string, zh: string) =>
    english
      ? future
        ? `in ${value}${en}`
        : `${value}${en} ago`
      : `${value}${zh}${future ? '后' : '前'}`
  if (amount < 60)
    return english ? (future ? 'in a moment' : 'just now') : '刚刚'
  if (amount < 3600) return unit(Math.floor(amount / 60), 'm', '分钟')
  if (amount < 86400) return unit(Math.floor(amount / 3600), 'h', '小时')
  if (amount < 172800)
    return english
      ? future
        ? 'tomorrow'
        : 'yesterday'
      : future
        ? '明天'
        : '昨天'
  if (amount < 604800) return unit(Math.floor(amount / 86400), 'd', '天')
  return date.toLocaleDateString(english ? 'en-US' : 'zh-CN')
}

export function DurationValue({
  start,
  end,
  running = false,
}: {
  start?: string
  end?: string
  running?: boolean
}) {
  const [now, setNow] = useState(0)
  useEffect(() => {
    if (!running) return undefined
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [running])
  if (!start) return <span className='text-muted-foreground'>-</span>
  const from = new Date(start).getTime()
  const to = end ? new Date(end).getTime() : running && now > from ? now : from
  if (Number.isNaN(from) || Number.isNaN(to) || to < from)
    return <span className='text-muted-foreground'>-</span>
  const seconds = Math.floor((to - from) / 1000)
  return (
    <span className='font-mono text-xs'>
      {seconds < 60
        ? `${seconds}s`
        : `${Math.floor(seconds / 60)}m ${seconds % 60}s`}
    </span>
  )
}

export async function deleteResource(path: string, message: string) {
  if (!window.confirm(message)) return false
  await api.del(path)
  return true
}

export function apiMessage(error: unknown) {
  return (
    (error as ApiError)?.message ||
    (error instanceof Error ? error.message : 'Request failed')
  )
}

export { Separator }
