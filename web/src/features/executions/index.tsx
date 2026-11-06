import { useEffect, useMemo, useRef, useState } from 'react'
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { getRouteApi } from '@tanstack/react-router'
import type { ColumnDef, Table as ReactTable } from '@tanstack/react-table'
import { Clipboard, Loader2, StopCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  api,
  type Execution,
  type Node,
  type Schedule,
  type Task,
} from '@/lib/api'
import { copyText } from '@/lib/clipboard'
import { executionLogView } from '@/lib/execution-logs'
import { nodeDisplayName } from '@/lib/node-display-name'
import { useCanRun } from '@/lib/permissions'
import { readTablePageSize } from '@/lib/table-view'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { DataTableViewOptions } from '@/components/data-table'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { DataTable } from '@/features/shared/data-table'
import {
  DetailField,
  DetailGrid,
  DurationValue,
  ErrorPage,
  ErrorState,
  LoadingState,
  SectionCard,
  SectionGrid,
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
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(() =>
    readTablePageSize('executions', 10)
  )
  // 搜索或筛选变化时重置页码，避免查询旧页造成空结果。
  const filters = JSON.stringify([
    search.status,
    search.q,
    search.trigger,
    search.node_id,
    search.schedule_id,
  ])
  const [previousFilters, setPreviousFilters] = useState(filters)
  if (previousFilters !== filters) {
    setPreviousFilters(filters)
    setPage(1)
  }
  const query = useQuery({
    queryKey: [
      'executions',
      search.status,
      search.q,
      search.trigger,
      search.node_id,
      search.schedule_id,
      page,
      pageSize,
    ],
    queryFn: () =>
      api.getPage<Execution>(
        `/executions?limit=${pageSize}&offset=${(page - 1) * pageSize}${search.status ? `&status=${encodeURIComponent(search.status)}` : ''}${search.q ? `&q=${encodeURIComponent(search.q)}` : ''}${search.trigger ? `&trigger_type=${encodeURIComponent(search.trigger)}` : ''}${search.node_id ? `&node_id=${encodeURIComponent(search.node_id)}` : ''}${search.schedule_id ? `&schedule_id=${encodeURIComponent(search.schedule_id)}` : ''}`
      ),
    placeholderData: keepPreviousData,
  })
  const tasks = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<Task[]>('/tasks'),
  })
  const nodes = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: () => api.get<Schedule[]>('/schedules'),
  })
  const taskNames = useMemo(
    () => new Map((tasks.data || []).map((task) => [task.id, task.name])),
    [tasks.data]
  )
  const nodeNames = useMemo(
    () =>
      new Map(
        (nodes.data || []).map((node) => [
          node.id,
          nodeDisplayName(node, t('common.unknownNode')),
        ])
      ),
    [nodes.data, t]
  )
  const scheduleNames = useMemo(
    () =>
      new Map(
        (schedules.data || []).map((item) => [item.id, item.name || item.id])
      ),
    [schedules.data]
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
  const visible = query.data?.items || []
  const triggers = Array.from(
    new Set(
      [
        // 一期仅 manual/schedule 能产出执行（系统触发不在 PRODUCT.md 一期范围），
        // 枚举只预置真实可达类型；已存在的其他触发值仍按观测数据展示（OBS-20261016-P17P18-04）
        { trigger_type: 'manual' },
        { trigger_type: 'schedule' },
        ...(query.data?.items || []),
      ]
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
        size: 96,
        minSize: 88,
        cell: ({ row }) => <StatusBadge status={row.original.status} />,
      },
      {
        id: 'task',
        accessorFn: (row) =>
          `${taskNames.get(row.task_id) || ''} ${row.task_id}`,
        header: t('dashboard.task'),
        size: 290,
        minSize: 215,
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
        size: 190,
        minSize: 150,
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
        size: 115,
        minSize: 108,
        cell: ({ row }) => {
          const trigger =
            labels[row.original.trigger_type as keyof typeof labels] ||
            row.original.trigger_type
          return (
            <span className='block truncate' title={trigger}>
              {trigger}
            </span>
          )
        },
      },
      {
        id: 'schedule',
        accessorFn: (row) =>
          `${scheduleNames.get(row.schedule_id || '') || ''} ${row.schedule_id || ''}`,
        header: t('executions.sourceSchedule'),
        size: 180,
        minSize: 140,
        cell: ({ row }) => {
          const scheduleId = row.original.schedule_id
          if (!scheduleId)
            return (
              <span className='block truncate text-sm text-muted-foreground'>
                {t('executions.historicalSource')}
              </span>
            )
          const name = scheduleNames.get(scheduleId)
          return name ? (
            <a
              href={`/schedules/${scheduleId}`}
              className='block truncate hover:underline'
              title={name}
            >
              {name}
            </a>
          ) : (
            <span className='block truncate text-sm text-muted-foreground'>
              {t('executions.unknownSource')}
            </span>
          )
        },
      },
      {
        accessorKey: 'start_time',
        header: t('executions.start'),
        size: 175,
        minSize: 160,
        cell: ({ row }) => <TimeValue value={row.original.start_time} />,
      },
      {
        id: 'duration',
        header: t('executions.duration'),
        size: 88,
        minSize: 80,
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
        size: 92,
        minSize: 92,
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
        size: 96,
        minSize: 88,
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
    [labels, nodeNames, scheduleNames, t, taskNames]
  )

  const renderToolbar = (table: ReactTable<Execution>) => (
    <div className='flex min-w-0 items-start gap-2'>
      <div className='flex min-w-0 flex-1 flex-wrap items-center gap-2'>
        <div className='relative w-full sm:w-64'>
          <Input
            className='h-8'
            value={search.q || ''}
            onChange={(event) => update('q', event.target.value)}
            placeholder={t('executions.searchPlaceholder')}
            aria-label={t('executions.searchPlaceholder')}
          />
        </div>
        <Select
          value={search.status || 'all'}
          onValueChange={(value) =>
            update('status', value === 'all' ? '' : value)
          }
        >
          <SelectTrigger
            className='h-8 w-[150px]'
            aria-label={t('common.status')}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='all'>{t('executions.all')}</SelectItem>
            {executionStatuses.map((status) => (
              <SelectItem key={status} value={status}>
                {
                  executionStatusLabels[status][
                    i18n.language === 'en' ? 'en' : 'zh'
                  ]
                }
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <Select
          value={search.trigger || 'all'}
          onValueChange={(value) =>
            update('trigger', value === 'all' ? '' : value)
          }
        >
          <SelectTrigger
            className='h-8 w-[160px]'
            aria-label={t('executions.trigger')}
          >
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value='all'>{t('executions.allTriggers')}</SelectItem>
            {triggers.map((trigger) => (
              <SelectItem key={trigger} value={trigger}>
                {labels[trigger as keyof typeof labels] || trigger}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>
      <DataTableViewOptions table={table} />
    </div>
  )

  return (
    <>
      <NodeSteerHeader
        title={t('executions.title')}
        description={t('executions.description')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <Card data-testid='executions-workspace' className='min-w-0'>
          <CardContent className='space-y-4 px-4 sm:px-6'>
            {query.isError ? (
              <ErrorState error={query.error} onRetry={() => query.refetch()} />
            ) : query.isLoading ? (
              <LoadingState />
            ) : (
              <>
                {(search.node_id || search.schedule_id) && (
                  <div className='flex flex-wrap items-center gap-2 rounded-md border p-2 text-sm'>
                    <span>
                      {search.node_id
                        ? `${t('nav.nodes')}: ${nodeNames.get(search.node_id) || search.node_id}`
                        : ''}{' '}
                      {search.schedule_id
                        ? `${t('nav.schedules')}: ${scheduleNames.get(search.schedule_id) || search.schedule_id}`
                        : ''}
                    </span>
                    <Button
                      variant='ghost'
                      size='sm'
                      onClick={() =>
                        navigate({
                          search: (previous) => ({
                            ...previous,
                            node_id: undefined,
                            schedule_id: undefined,
                          }),
                        })
                      }
                    >
                      {t('webFix.clearScope')}
                    </Button>
                  </div>
                )}
                <DataTable
                  data={visible}
                  columns={columns}
                  hideSearch
                  storageKey='executions'
                  variant='rack'
                  rackPrimaryColumnId='task'
                  manualPagination
                  total={query.data?.total ?? 0}
                  page={page}
                  pageSize={pageSize}
                  onPageChange={setPage}
                  onPageSizeChange={setPageSize}
                  searchPlaceholder={t('executions.searchPlaceholder')}
                  renderToolbar={renderToolbar}
                  emptyMessage={
                    search.q || search.status || search.trigger
                      ? t('executions.noMatching')
                      : t('executions.noExecs')
                  }
                />
              </>
            )}
          </CardContent>
        </Card>
      </Main>
    </>
  )
}

export function ExecutionDetail() {
  const { t, i18n } = useTranslation()
  const canRun = useCanRun()
  const id = window.location.pathname.split('/').pop() || ''
  const client = useQueryClient()
  const [stopOpen, setStopOpen] = useState(false)
  const execution = useQuery({
    queryKey: ['execution', id],
    queryFn: () => api.get<Execution>(`/executions/${id}`),
    refetchInterval: (query) =>
      ['PENDING', 'RUNNING'].includes(query.state.data?.status || '')
        ? 3000
        : false,
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
  // 来源调度仅用于展示；解析失败不能影响执行详情本身
  const schedule = useQuery({
    queryKey: ['schedule', execution.data?.schedule_id],
    queryFn: () =>
      api.get<Schedule>(`/schedules/${execution.data?.schedule_id}`),
    enabled: Boolean(execution.data?.schedule_id),
    retry: false,
  })
  const logs = useQuery({
    queryKey: ['execution-logs', id],
    queryFn: () =>
      api.get<{ stream: string; seq: number; chunk: string }[]>(
        `/executions/${id}/logs`
      ),
    refetchInterval: ['PENDING', 'RUNNING'].includes(
      execution.data?.status || ''
    )
      ? 3000
      : false,
  })
  useEffect(() => {
    if (
      execution.data?.status &&
      !['PENDING', 'RUNNING'].includes(execution.data.status)
    ) {
      void client.invalidateQueries({ queryKey: ['execution-logs', id] })
    }
  }, [execution.data?.status, client, id])
  const stop = useMutation({
    mutationFn: () => api.post(`/executions/${id}/cancel`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['execution', id] })
      toast.success(t('executions.stopped'))
    },
    onError: (error) => toast.error(error.message),
  })
  if (execution.isError && !execution.data)
    return (
      <ErrorPage
        title={t('executions.executionTitle')}
        error={execution.error}
        onRetry={() => execution.refetch()}
        backTo='/executions'
      />
    )
  if (!execution.data)
    return (
      <>
        <NodeSteerHeader title={t('executions.executionTitle')} />
        <Main>
          <LoadingState />
        </Main>
      </>
    )
  const exec = execution.data
  const taskName = task.data?.name || t('common.unknownTask')
  const nodeName = node.data
    ? nodeDisplayName(node.data, t('common.unknownNode'))
    : t('common.unknownNode')
  const exitCode = exec.exit_code ?? '-'
  const inProgress = ['PENDING', 'RUNNING'].includes(exec.status)
  const sourceSchedule =
    exec.schedule_id && schedule.data?.id === exec.schedule_id
      ? schedule.data
      : undefined
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
      <NodeSteerHeader
        title={taskName}
        description={`${nodeName} · ${trigger}`}
        backTo='/executions'
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
        <SectionGrid>
          <SectionCard title={t('executions.process')}>
            <div className='grid gap-4'>
              <ProcessTimeline
                createdAt={exec.created_at}
                startedAt={exec.start_time}
                finishedAt={exec.end_time}
              />
              <div className='flex flex-wrap items-center gap-3 text-sm'>
                <StatusBadge status={exec.status} />
                {inProgress ? (
                  <span className='text-muted-foreground'>
                    {t('executions.inProgress')}
                  </span>
                ) : null}
              </div>
              {exec.block_reason ? (
                <div className='rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm'>
                  <span className='font-medium'>
                    {t('executions.blockReason')}:
                  </span>{' '}
                  {exec.block_reason}
                </div>
              ) : null}
            </div>
          </SectionCard>
          <SectionCard title={t('executions.metadata')}>
            <DetailGrid>
              <DetailField label={t('dashboard.task')}>
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
              </DetailField>
              <DetailField label={t('executions.taskRevision')}>
                <span className='font-mono text-xs'>
                  r{exec.task_revision || '-'}
                </span>
              </DetailField>
              {exec.script_id ? (
                <DetailField label={t('executions.script')}>
                  <a
                    className='font-mono text-xs hover:underline'
                    href={`/scripts/${exec.script_id}`}
                    title={exec.script_id}
                  >
                    {exec.script_id}
                  </a>
                  <span className='ms-2 font-mono text-xs'>
                    r{exec.script_revision || '-'}
                  </span>
                </DetailField>
              ) : null}
              <DetailField label={t('executions.sourceSchedule')}>
                {sourceSchedule ? (
                  <a
                    className='hover:underline'
                    href={`/schedules/${sourceSchedule.id}`}
                  >
                    {sourceSchedule.name || sourceSchedule.id}
                  </a>
                ) : exec.schedule_id ? (
                  <span>{t('executions.unknownSource')}</span>
                ) : (
                  <span>{t('executions.historicalSource')}</span>
                )}
              </DetailField>
              {exec.schedule_revision ? (
                <DetailField label={t('executions.scheduleRevision')}>
                  <span className='font-mono text-xs'>
                    r{exec.schedule_revision}
                  </span>
                </DetailField>
              ) : null}
              <DetailField label={t('dashboard.trigger')}>
                {trigger}
              </DetailField>
              <DetailField label={t('executions.offline')}>
                {exec.offline ? t('executions.yes') : t('executions.no')}
              </DetailField>
              <DetailField label={t('executions.synced')}>
                {exec.synced ? t('executions.yes') : t('executions.no')}
              </DetailField>
            </DetailGrid>
            <div className='mt-4 flex flex-wrap items-center gap-x-3 gap-y-1 border-t pt-3 font-mono text-xs text-muted-foreground'>
              <span>
                {t('executions.id')}: {exec.id}
              </span>
              <span>r{exec.task_revision || '-'}</span>
              <TimeValue value={exec.start_time} absolute />
            </div>
          </SectionCard>
        </SectionGrid>
        <SectionCard title={t('executions.result')}>
          <div className='grid gap-4'>
            <DetailGrid className='lg:grid-cols-3'>
              <DetailField label={t('common.status')}>
                <StatusBadge status={exec.status} />
              </DetailField>
              <DetailField label={t('executions.duration')}>
                <DurationValue
                  start={exec.start_time}
                  end={exec.end_time}
                  running={exec.status === 'RUNNING'}
                />
              </DetailField>
              <DetailField label={t('executions.exitCode')}>
                <span className='font-mono text-xs'>
                  {inProgress ? '-' : exitCode}
                </span>
              </DetailField>
            </DetailGrid>
            <OutputSummary
              stream='stdout'
              content={exec.stdout}
              truncated={exec.stdout_truncated}
            />
            <OutputSummary
              stream='stderr'
              content={exec.stderr}
              truncated={exec.stderr_truncated}
            />
            <Button
              variant='outline'
              size='sm'
              className='w-fit'
              onClick={() =>
                document
                  .getElementById('execution-logs')
                  ?.scrollIntoView({ behavior: 'smooth', block: 'start' })
              }
            >
              <Clipboard className='me-1 size-3.5' />
              {t('executions.openLogs')}
            </Button>
          </div>
        </SectionCard>
        <div id='execution-logs' className='scroll-mt-4'>
          <SectionCard title={t('executions.logsTab')}>
            <LogViewer
              stdout={exec.stdout}
              stderr={exec.stderr}
              chunks={logs.data || []}
              terminal={!['PENDING', 'RUNNING'].includes(exec.status)}
              stdoutTruncated={exec.stdout_truncated}
              stderrTruncated={exec.stderr_truncated}
            />
          </SectionCard>
        </div>
      </Main>
    </>
  )
}

function ProcessTimeline({
  createdAt,
  startedAt,
  finishedAt,
}: {
  createdAt?: string
  startedAt?: string
  finishedAt?: string
}) {
  const { t } = useTranslation()
  const items = [
    { key: 'createdAt', label: t('executions.createdAt'), value: createdAt },
    { key: 'startedAt', label: t('executions.startedAt'), value: startedAt },
    { key: 'finishedAt', label: t('executions.finishedAt'), value: finishedAt },
  ]
  return (
    <ol className='ms-1.5 grid gap-4 border-s ps-5'>
      {items.map((item) => (
        <li className='relative' key={item.key}>
          <span
            className='absolute -start-[27px] top-1.5 size-2.5 rounded-full border-2 border-background bg-primary'
            aria-hidden='true'
          />
          <div className='text-xs text-muted-foreground'>{item.label}</div>
          <div className='mt-0.5 text-sm'>
            <TimeValue value={item.value} absolute />
          </div>
        </li>
      ))}
    </ol>
  )
}

/** 执行结果中的 stdout/stderr 摘要：展示截断标记与前 400 字符 */
function OutputSummary({
  stream,
  content,
  truncated,
}: {
  stream: 'stdout' | 'stderr'
  content?: string
  truncated?: boolean
}) {
  const { t } = useTranslation()
  const text = content || ''
  const excerpt = text.length > 400 ? `${text.slice(0, 400)}…` : text
  if (!text && !truncated)
    return (
      <p className='text-xs text-muted-foreground'>
        {stream}: {t('executions.empty')}
      </p>
    )
  return (
    <div className='grid gap-2'>
      <div className='flex flex-wrap items-center gap-2 text-sm'>
        <span className='text-muted-foreground'>{stream}</span>
        <span className='font-mono text-xs'>
          {text.length} {t('executions.characters')}
        </span>
        {truncated ? (
          <Badge variant='outline'>{t('executions.truncated')}</Badge>
        ) : null}
      </div>
      <pre className='max-h-32 overflow-auto rounded-md bg-muted p-3 font-mono text-xs whitespace-pre-wrap'>
        {excerpt || t('executions.empty')}
      </pre>
    </div>
  )
}

function LogViewer({
  stdout,
  stderr,
  chunks,
  terminal,
  stdoutTruncated,
  stderrTruncated,
}: {
  stdout?: string
  stderr?: string
  chunks: { stream: string; seq: number; chunk: string }[]
  terminal: boolean
  stdoutTruncated?: boolean
  stderrTruncated?: boolean
}) {
  const { t } = useTranslation()
  const [search, setSearch] = useState('')
  const [wrap, setWrap] = useState(false)
  const [follow, setFollow] = useState(false)
  const [stream, setStream] = useState<'all' | 'stdout' | 'stderr'>('all')
  const outputRef = useRef<HTMLPreElement>(null)
  const view = executionLogView({
    chunks,
    stream,
    terminal,
    stdout,
    stderr,
    stdoutTruncated,
    stderrTruncated,
  })
  const content = [
    view.content,
    ...view.supplements.map(
      (summary) =>
        `[${summary.stream} · ${t('executions.finalOutput')}${summary.truncated ? t('executions.truncated') : ''}]\n${summary.content}`
    ),
  ]
    .filter(Boolean)
    .join('\n\n')
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
            aria-label={t('common.searchLogs')}
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={t('common.searchLogs')}
          />
          <Select
            value={stream}
            onValueChange={(value) => setStream(value as typeof stream)}
          >
            <SelectTrigger
              className='h-8 w-[130px] text-xs'
              aria-label={t('common.allStreams')}
            >
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='all'>{t('common.allStreams')}</SelectItem>
              <SelectItem value='stdout'>stdout</SelectItem>
              <SelectItem value='stderr'>stderr</SelectItem>
            </SelectContent>
          </Select>
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
          className={`max-h-[560px] min-h-48 overflow-auto bg-code-background p-4 font-mono text-xs leading-6 text-code-foreground ${wrap ? 'break-words whitespace-pre-wrap' : 'whitespace-pre'}`}
        >
          {filtered || t('executions.empty')}
        </pre>
      </CardContent>
    </Card>
  )
}
