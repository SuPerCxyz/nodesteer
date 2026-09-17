import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, getRouteApi } from '@tanstack/react-router'
import type { ColumnDef } from '@tanstack/react-table'
import { ChevronLeft, Clipboard, Loader2, StopCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api, type Execution, type Node, type Task } from '@/lib/api'
import { copyText } from '@/lib/clipboard'
import { useCanRun } from '@/lib/permissions'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { CadentraHeader } from '@/components/layout/cadentra-header'
import { Main } from '@/components/layout/main'
import { DataTable } from '@/features/shared/data-table'
import {
  DurationValue,
  EmptyState,
  ErrorState,
  StatusBadge,
  TimeValue,
} from '@/features/shared/ui'

const executionsRoute = getRouteApi('/_authenticated/executions/')
const executionStatuses = [
  'PENDING',
  'RUNNING',
  'SUCCESS',
  'FAILED',
  'SKIPPED',
  'CANCELED',
  'TIMED_OUT',
  'BLOCKED',
] as const
const executionStatusLabels: Record<
  (typeof executionStatuses)[number],
  { zh: string; en: string }
> = {
  PENDING: { zh: '等待中', en: 'Pending' },
  RUNNING: { zh: '运行中', en: 'Running' },
  SUCCESS: { zh: '成功', en: 'Success' },
  FAILED: { zh: '失败', en: 'Failed' },
  SKIPPED: { zh: '已跳过', en: 'Skipped' },
  CANCELED: { zh: '已取消', en: 'Canceled' },
  TIMED_OUT: { zh: '超时', en: 'Timed out' },
  BLOCKED: { zh: '已阻断', en: 'Blocked' },
}

export function Executions() {
  const { t, i18n } = useTranslation()
  const search = executionsRoute.useSearch()
  const navigate = executionsRoute.useNavigate()
  const query = useQuery({
    queryKey: ['executions', search.status],
    queryFn: () =>
      api.get<Execution[]>(
        `/executions?limit=100${search.status ? `&status=${encodeURIComponent(search.status)}` : ''}`
      ),
  })
  const tasks = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<Task[]>('/tasks'),
  })
  const nodes = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const taskNames = useMemo(
    () => new Map((tasks.data || []).map((task) => [task.id, task.name])),
    [tasks.data]
  )
  const nodeNames = useMemo(
    () => new Map((nodes.data || []).map((node) => [node.id, node.hostname])),
    [nodes.data]
  )
  const update = (key: 'q' | 'status' | 'trigger', value: string) =>
    navigate({
      search: (previous) => {
        const next = { ...previous }
        if (value) next[key] = value
        else delete next[key]
        return next
      },
    })
  const visible = useMemo(
    () =>
      (query.data || []).filter((execution) => {
        const q = (search.q || '').toLowerCase()
        return (
          (!q ||
            [
              execution.id,
              execution.task_id,
              taskNames.get(execution.task_id) || '',
              execution.node_id,
            ].some((value) => value.toLowerCase().includes(q))) &&
          (!search.trigger || execution.trigger_type === search.trigger)
        )
      }),
    [query.data, search.q, search.trigger, taskNames]
  )
  const triggers = Array.from(
    new Set(
      (query.data || [])
        .map((execution) => execution.trigger_type)
        .filter(Boolean)
    )
  )
  const labels = useMemo(
    () =>
      i18n.language === 'en'
        ? { schedule: 'Schedule', manual: 'Manual', system: 'System' }
        : { schedule: '调度', manual: '手动', system: '系统' },
    [i18n.language]
  )
  const columns = useMemo<ColumnDef<Execution>[]>(
    () => [
      {
        accessorKey: 'status',
        header: t('common.status'),
        size: 104,
        minSize: 96,
        meta: { align: 'center' },
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        id: 'task',
        accessorFn: (row) =>
          `${taskNames.get(row.task_id) || ''} ${row.task_id}`,
        header: t('dashboard.task'),
        size: 300,
        minSize: 220,
        maxSize: 380,
        cell: ({ row }) => {
          const name = taskNames.get(row.original.task_id)
          return (
            <div className='min-w-0'>
              {name ? (
                <a
                  href={`/tasks/${row.original.task_id}`}
                  className='block truncate font-medium hover:underline'
                  title={name}
                >
                  {name}
                </a>
              ) : (
                <span className='block truncate font-medium'>
                  {t('common.unknownTask')}
                </span>
              )}
            </div>
          )
        },
      },
      {
        id: 'node',
        accessorFn: (row) =>
          `${nodeNames.get(row.node_id) || ''} ${row.node_id}`,
        header: t('dashboard.node'),
        size: 150,
        minSize: 120,
        cell: ({ row }) => {
          const name = nodeNames.get(row.original.node_id)
          return name ? (
            <a
              href={`/agents/${row.original.node_id}`}
              className='block truncate hover:underline'
              title={name}
            >
              {name}
            </a>
          ) : (
            <span className='block truncate'>{t('common.unknownNode')}</span>
          )
        },
      },
      {
        accessorKey: 'trigger_type',
        header: t('dashboard.trigger'),
        size: 130,
        minSize: 110,
        cell: ({ row }) =>
          labels[row.original.trigger_type as keyof typeof labels] ||
          row.original.trigger_type,
      },
      {
        accessorKey: 'start_time',
        header: t('executions.start'),
        size: 180,
        minSize: 160,
        cell: ({ row }) => <TimeValue value={row.original.start_time} />,
      },
      {
        id: 'duration',
        header: t('executions.duration'),
        size: 100,
        minSize: 88,
        meta: { align: 'end' },
        cell: ({ row }) => (
          <DurationValue
            start={row.original.start_time}
            end={row.original.end_time}
            running={row.original.status === 'RUNNING'}
          />
        ),
      },
      {
        accessorKey: 'exit_code',
        header: t('dashboard.exit'),
        size: 88,
        minSize: 76,
        meta: { align: 'end' },
        cell: ({ row }) => (
          <span className='font-mono text-xs'>
            {row.original.exit_code ?? '-'}
          </span>
        ),
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        size: 100,
        minSize: 88,
        meta: { align: 'end' },
        cell: ({ row }) => (
          <a
            className='text-sm text-primary hover:underline'
            href={`/executions/${row.original.id}`}
          >
            {t('common.viewDetails')}
          </a>
        ),
      },
    ],
    [labels, nodeNames, t, taskNames]
  )

  return (
    <>
      <CadentraHeader
        title={t('executions.title')}
        description={t('executions.description')}
      />
      <Main fluid className='flex flex-1 flex-col gap-6'>
        {query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : query.isLoading ? (
          <div className='flex min-h-32 items-center justify-center'>
            <Loader2 className='size-4 animate-spin text-muted-foreground' />
          </div>
        ) : (
          <>
            <div className='flex flex-wrap items-center gap-2'>
              <div className='relative w-full sm:w-64'>
                <Input
                  className='h-8'
                  value={search.q || ''}
                  onChange={(event) => update('q', event.target.value)}
                  placeholder={t('executions.searchPlaceholder')}
                  aria-label={t('executions.searchPlaceholder')}
                />
              </div>
              <select
                className='h-8 rounded-md border bg-background px-2 text-sm'
                value={search.status || ''}
                onChange={(event) => update('status', event.target.value)}
                aria-label={t('common.status')}
              >
                <option value=''>{t('executions.all')}</option>
                {executionStatuses.map((status) => (
                  <option key={status} value={status}>
                    {
                      executionStatusLabels[status][
                        i18n.language === 'en' ? 'en' : 'zh'
                      ]
                    }
                  </option>
                ))}
              </select>
              <select
                className='h-8 rounded-md border bg-background px-2 text-sm'
                value={search.trigger || ''}
                onChange={(event) => update('trigger', event.target.value)}
                aria-label={t('executions.trigger')}
              >
                <option value=''>{t('executions.allTriggers')}</option>
                {triggers.map((trigger) => (
                  <option key={trigger} value={trigger}>
                    {labels[trigger as keyof typeof labels] || trigger}
                  </option>
                ))}
              </select>
            </div>
            {visible.length === 0 ? (
              <Card>
                <EmptyState message={t('executions.noMatching')} />
              </Card>
            ) : (
              <DataTable
                data={visible}
                columns={columns}
                hideSearch
                searchPlaceholder={t('executions.searchPlaceholder')}
              />
            )}
          </>
        )}
      </Main>
    </>
  )
}

export function ExecutionDetail() {
  const { t, i18n } = useTranslation()
  const canRun = useCanRun()
  const id = window.location.pathname.split('/').pop() || ''
  const client = useQueryClient()
  const [tab, setTab] = useState('overview')
  const [stopOpen, setStopOpen] = useState(false)
  const execution = useQuery({
    queryKey: ['execution', id],
    queryFn: () => api.get<Execution>(`/executions/${id}`),
    refetchInterval: (query) =>
      query.state.data?.status === 'RUNNING' ? 3000 : false,
  })
  const task = useQuery({
    queryKey: ['task', execution.data?.task_id],
    queryFn: () => api.get<Task>(`/tasks/${execution.data?.task_id}`),
    enabled: Boolean(execution.data?.task_id),
  })
  const node = useQuery({
    queryKey: ['node', execution.data?.node_id],
    queryFn: () => api.get<Node>(`/nodes/${execution.data?.node_id}`),
    enabled: Boolean(execution.data?.node_id),
  })
  const logs = useQuery({
    queryKey: ['execution-logs', id],
    queryFn: () =>
      api.get<{ stream: string; seq: number; chunk: string }[]>(
        `/executions/${id}/logs`
      ),
    enabled: tab === 'logs',
  })
  const stop = useMutation({
    mutationFn: () => api.post(`/executions/${id}/cancel`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['execution', id] })
      toast.success(t('executions.stopped'))
    },
    onError: (error) => toast.error(error.message),
  })
  if (execution.isLoading)
    return (
      <>
        <CadentraHeader title={t('executions.executionTitle')} />
        <Main>
          <div className='flex min-h-32 items-center justify-center'>
            <Loader2 className='size-4 animate-spin text-muted-foreground' />
          </div>
        </Main>
      </>
    )
  if (execution.isError || !execution.data)
    return (
      <>
        <CadentraHeader title={t('executions.executionTitle')} />
        <Main>
          <ErrorState
            error={execution.error}
            onRetry={() => execution.refetch()}
          />
        </Main>
      </>
    )
  const exec = execution.data
  const taskName = task.data?.name || t('common.unknownTask')
  const nodeName = node.data?.hostname || t('common.unknownNode')
  const exitCode = exec.exit_code ?? '-'
  const trigger =
    i18n.language === 'en'
      ? { schedule: 'Schedule', manual: 'Manual', system: 'System' }[
          exec.trigger_type
        ] || exec.trigger_type
      : { schedule: '调度', manual: '手动', system: '系统' }[
          exec.trigger_type
        ] || exec.trigger_type
  return (
    <>
      <CadentraHeader
        title={taskName}
        description={`${nodeName} · ${trigger}`}
        action={
          exec.status === 'RUNNING' && canRun ? (
            <Button
              variant='destructive'
              onClick={() => setStopOpen(true)}
              disabled={stop.isPending}
            >
              {stop.isPending ? (
                <Loader2 className='animate-spin' />
              ) : (
                <StopCircle />
              )}
              {t('executions.stop')}
            </Button>
          ) : undefined
        }
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <Button asChild variant='ghost' className='w-fit px-0'>
          <Link to='/executions'>
            <ChevronLeft className='me-1 size-4' />
            {t('common.back')}
          </Link>
        </Button>
        <ConfirmDialog
          open={stopOpen}
          onOpenChange={setStopOpen}
          title={`${t('executions.stop')}: ${taskName}`}
          desc={t('common.confirmAction')}
          cancelBtnText={t('common.cancel')}
          confirmText={t('executions.stop')}
          destructive
          isLoading={stop.isPending}
          handleConfirm={() => {
            stop.mutate()
            setStopOpen(false)
          }}
        />
        <div className='flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-xs text-muted-foreground'>
          <span>
            {t('executions.id')}: {exec.id}
          </span>
          <span>r{exec.task_revision || '-'}</span>
          <TimeValue value={exec.start_time} absolute />
        </div>
        <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-5'>
          {[
            ['status', <StatusBadge status={exec.status} />],
            [
              'node',
              node.data ? (
                <a
                  className='truncate hover:underline'
                  href={`/agents/${exec.node_id}`}
                  title={node.data.hostname}
                >
                  {nodeName}
                </a>
              ) : (
                <span>{nodeName}</span>
              ),
            ],
            ['start', <TimeValue value={exec.start_time} absolute />],
            [
              'duration',
              <DurationValue
                start={exec.start_time}
                end={exec.end_time}
                running={exec.status === 'RUNNING'}
              />,
            ],
            [
              'exit',
              <span className='font-mono text-xs'>
                {exec.status === 'RUNNING' ? '-' : exitCode}
              </span>,
            ],
          ].map(([label, value]) => (
            <Card key={label as string}>
              <CardHeader className='pb-2'>
                <CardTitle className='text-xs font-medium text-muted-foreground'>
                  {t(`executions.${label}`) || label}
                </CardTitle>
              </CardHeader>
              <CardContent>{value}</CardContent>
            </Card>
          ))}
        </div>
        <Tabs value={tab} onValueChange={setTab}>
          <TabsList>
            <TabsTrigger value='overview'>
              {t('executions.overviewTab')}
            </TabsTrigger>
            <TabsTrigger value='logs'>{t('executions.logsTab')}</TabsTrigger>
          </TabsList>
          <TabsContent
            value='overview'
            className='mt-4 grid gap-4 lg:grid-cols-2'
          >
            <Card>
              <CardHeader>
                <CardTitle className='text-sm'>
                  {t('executions.metadata')}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <dl className='grid grid-cols-[minmax(120px,160px)_1fr] gap-x-4 gap-y-3 text-sm'>
                  <dt className='text-muted-foreground'>
                    {t('dashboard.task')}
                  </dt>
                  <dd>
                    {task.data ? (
                      <a
                        className='hover:underline'
                        href={`/tasks/${exec.task_id}`}
                      >
                        {taskName}
                      </a>
                    ) : (
                      <span>{taskName}</span>
                    )}
                  </dd>
                  <dt className='text-muted-foreground'>
                    {t('executions.taskRevision')}
                  </dt>
                  <dd className='font-mono text-xs'>
                    r{exec.task_revision || '-'}
                  </dd>
                  <dt className='text-muted-foreground'>
                    {t('dashboard.trigger')}
                  </dt>
                  <dd>{trigger}</dd>
                  <dt className='text-muted-foreground'>
                    {t('executions.offline')}
                  </dt>
                  <dd>
                    {exec.offline ? t('executions.yes') : t('executions.no')}
                  </dd>
                  <dt className='text-muted-foreground'>
                    {t('executions.synced')}
                  </dt>
                  <dd>
                    {exec.synced ? t('executions.yes') : t('executions.no')}
                  </dd>
                </dl>
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle className='text-sm'>
                  {t('executions.outputSummary')}
                </CardTitle>
              </CardHeader>
              <CardContent className='space-y-3 text-sm'>
                <div className='flex items-center justify-between'>
                  <span className='text-muted-foreground'>stdout</span>
                  <span className='font-mono text-xs'>
                    {exec.stdout?.length || 0} {t('executions.characters')}
                  </span>
                </div>
                <div className='flex items-center justify-between'>
                  <span className='text-muted-foreground'>stderr</span>
                  <span className='font-mono text-xs'>
                    {exec.stderr?.length || 0} {t('executions.characters')}
                  </span>
                </div>
                <Button
                  variant='outline'
                  size='sm'
                  onClick={() => setTab('logs')}
                >
                  <Clipboard className='me-1 size-3.5' />
                  {t('executions.openLogs')}
                </Button>
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value='logs' className='mt-4'>
            <LogViewer
              stdout={exec.stdout}
              stderr={exec.stderr}
              chunks={logs.data || []}
            />
          </TabsContent>
        </Tabs>
      </Main>
    </>
  )
}

function LogViewer({
  stdout,
  stderr,
  chunks,
}: {
  stdout?: string
  stderr?: string
  chunks: { stream: string; seq: number; chunk: string }[]
}) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [wrap, setWrap] = useState(false)
  const [follow, setFollow] = useState(false)
  const [stream, setStream] = useState<'all' | 'stdout' | 'stderr'>('all')
  const outputRef = useRef<HTMLPreElement>(null)
  const content = chunks.length
    ? chunks
        .slice()
        .sort((a, b) => a.seq - b.seq)
        .filter((chunk) => stream === 'all' || chunk.stream === stream)
        .map((chunk) => chunk.chunk)
        .join('')
    : stream === 'stdout'
      ? stdout || ''
      : stream === 'stderr'
        ? stderr || ''
        : [stdout, stderr].filter(Boolean).join('\n')
  const filtered = search
    ? content
        .split('\n')
        .filter((line) => line.toLowerCase().includes(search.toLowerCase()))
        .join('\n')
    : content
  useEffect(() => {
    if (follow && outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight
    }
  }, [filtered, follow])
  const copy = async () => {
    try {
      const copied = await copyText(filtered)
      toast[copied ? 'success' : 'info'](
        copied ? t('common.copied') : t('common.copyManual')
      )
    } catch {
      toast.error(t('common.copyFailed'))
    }
  }
  return (
    <Card>
      <CardHeader className='border-b py-3'>
        <div className='flex flex-wrap items-center gap-2'>
          <Input
            className='h-8 max-w-sm'
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t('common.searchLogs')}
          />
          <select
            className='h-8 rounded-md border bg-background px-2 text-xs'
            value={stream}
            onChange={(event) => setStream(event.target.value as typeof stream)}
            aria-label={t('common.allStreams')}
          >
            <option value='all'>{t('common.allStreams')}</option>
            <option value='stdout'>stdout</option>
            <option value='stderr'>stderr</option>
          </select>
          <Button
            variant={wrap ? 'secondary' : 'outline'}
            size='sm'
            onClick={() => setWrap(!wrap)}
          >
            {t('common.wrap')}
          </Button>
          <Button
            variant={follow ? 'secondary' : 'outline'}
            size='sm'
            onClick={() => setFollow(!follow)}
          >
            {t('common.follow')}
          </Button>
          <Button variant='outline' size='sm' onClick={() => void copy()}>
            <Clipboard className='me-1 size-3.5' />
            {t('common.copyLogs')}
          </Button>
        </div>
      </CardHeader>
      <CardContent className='p-0'>
        <pre
          ref={outputRef}
          className={`max-h-[560px] min-h-48 overflow-auto bg-slate-950 p-4 font-mono text-xs leading-6 text-slate-200 ${wrap ? 'break-words whitespace-pre-wrap' : 'whitespace-pre'}`}
        >
          {filtered || t('executions.empty')}
        </pre>
      </CardContent>
    </Card>
  )
}
