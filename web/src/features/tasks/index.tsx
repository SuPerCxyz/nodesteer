import { useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { ColumnDef } from '@tanstack/react-table'
import { Loader2, Play, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  api,
  type Application,
  type Condition,
  type Group,
  type Node,
  type Parameter,
  type Script,
  type Schedule,
  type Execution,
  type Task,
  type Target,
} from '@/lib/api'
import { useCanRun, useCanWrite } from '@/lib/permissions'
import { scheduleDisplayName, scheduleTriggerText } from '@/lib/schedules'
import { isTargetComplete, normalizeTarget, targetSummary } from '@/lib/targets'
import {
  appDeployOperations,
  appOperations,
  buildTaskPayload,
  normalizeAppOperation,
} from '@/lib/task-payload'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { DropdownMenuItem } from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { DataTable } from '@/features/shared/data-table'
import {
  ScheduleStrategyFields,
  type ExecutionStrategy,
} from '@/features/shared/schedule-strategy-fields'
import { TargetEditor } from '@/features/shared/target-editor'
import {
  DetailField,
  DetailGrid,
  DangerMenuItem,
  EmptyState,
  ErrorPage,
  ErrorState,
  LoadingState,
  MoreMenu,
  SectionCard,
  SectionFull,
  SectionGrid,
  StatusBadge,
  TableCard,
  TimeValue,
} from '@/features/shared/ui'

/** 条件项的可读文本；AND 组按 AND 连接（缺陷 OBS-20261016-G5-AND-CONDITION-NO-SUMMARY-NO-CLEAR） */
function conditionItemText(
  condition: Condition,
  nodeName: (id: string) => string
): string {
  if (condition.type === 'local' && condition.local) {
    const local = condition.local
    const extras = [local.path, local.command].filter(Boolean).join(' ')
    return `${local.metric} ${local.operator} ${local.value}${extras ? ` (${extras})` : ''}`
  }
  if (condition.type === 'remote' && condition.remote) {
    const remote = condition.remote
    return `${nodeName(remote.node_id)}.${remote.property} ${remote.operator} ${remote.value}`
  }
  return condition.type
}

/** 整组条件的可读摘要：单条件原样展示，AND 组按 AND 连接 */
function conditionSummaryText(
  condition: Condition,
  nodeName: (id: string) => string
): string {
  const items = condition.type === 'and' ? condition.and || [] : [condition]
  return items.map((item) => conditionItemText(item, nodeName)).join(' AND ')
}

export function Tasks() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const canRun = useCanRun()
  const canWrite = useCanWrite()
  const query = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<Task[]>('/tasks'),
  })
  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: () => api.get<Schedule[]>('/schedules'),
  })
  const scheduleCounts = useMemo(() => {
    const counts = new Map<string, number>()
    for (const schedule of schedules.data || []) {
      counts.set(schedule.task_id, (counts.get(schedule.task_id) || 0) + 1)
    }
    return counts
  }, [schedules.data])
  const { mutate: toggleTask } = useMutation({
    mutationFn: (task: Task) =>
      api.put<Task>(
        `/tasks/${task.id}`,
        buildTaskPayload({ ...task, enabled: !task.enabled })
      ),
    onSuccess: () => client.invalidateQueries({ queryKey: ['tasks'] }),
    onError: (error) => toast.error(error.message),
  })
  const { mutate: removeTask } = useMutation({
    mutationFn: (id: string) => api.del(`/tasks/${id}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['tasks'] })
      toast.success(t('tasks.deleted'))
    },
    onError: (error) => toast.error(error.message),
  })
  const columns = useMemo<ColumnDef<Task>[]>(
    () => [
      {
        id: 'name',
        accessorFn: (row) => `${row.name} ${row.id}`,
        header: t('common.name'),
        size: 270,
        minSize: 215,
        maxSize: 350,
        cell: ({ row }) => (
          <div className='min-w-0'>
            <a
              href={`/tasks/${row.original.id}`}
              className='block truncate font-medium hover:underline'
              title={row.original.name}
            >
              {row.original.name}
            </a>
          </div>
        ),
      },
      {
        accessorKey: 'type',
        header: t('common.type'),
        size: 88,
        minSize: 80,
        cell: ({ row }) => (
          <span className='font-mono text-xs'>
            {t(`tasks.types.${row.original.type}`, {
              defaultValue: row.original.type,
            })}
          </span>
        ),
      },
      {
        id: 'schedules',
        accessorFn: (row) => scheduleCounts.get(row.id) || 0,
        header: t('tasks.relatedSchedules'),
        size: 110,
        minSize: 96,
        cell: ({ row }) => (
          <a
            href={`/tasks/${row.original.id}`}
            className='font-mono text-xs hover:underline'
            title={t('tasks.schedules')}
          >
            {scheduleCounts.get(row.original.id) || 0}
          </a>
        ),
      },
      {
        accessorKey: 'revision',
        header: t('tasks.revision'),
        size: 92,
        minSize: 92,
        cell: ({ row }) => (
          <span className='font-mono text-xs'>r{row.original.revision}</span>
        ),
      },
      {
        id: 'status',
        accessorFn: (row) => (row.enabled ? 'enabled' : 'disabled'),
        header: t('common.status'),
        size: 96,
        minSize: 88,
        cell: ({ row }) => (
          <StatusBadge status={row.original.enabled ? 'enabled' : 'disabled'} />
        ),
      },
      {
        id: 'actions',
        enableHiding: false,
        header: '',
        size: 96,
        minSize: 88,
        cell: ({ row }) => (
          <TaskRowActions
            task={row.original}
            canRun={canRun}
            canWrite={canWrite}
            toggleTask={toggleTask}
            removeTask={removeTask}
          />
        ),
      },
    ],
    [canRun, canWrite, removeTask, scheduleCounts, t, toggleTask]
  )

  return (
    <>
      <NodeSteerHeader
        title={t('tasks.title')}
        description={t('tasks.description')}
        action={
          canWrite ? (
            <Button asChild>
              <Link to='/tasks/new'>{t('tasks.newTask')}</Link>
            </Button>
          ) : null
        }
      />
      <Main className='flex flex-1 flex-col gap-6'>
        {query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : query.isLoading ? (
          <LoadingState />
        ) : query.data?.length ? (
          <DataTable
            data={query.data}
            columns={columns}
            storageKey='tasks'
            searchPlaceholder={t('tasks.searchPlaceholder')}
          />
        ) : (
          <Card>
            <EmptyState message={t('tasks.noTasks')} />
          </Card>
        )}
      </Main>
    </>
  )
}

export function TaskDetail() {
  const { t } = useTranslation()
  const canRun = useCanRun()
  const canWrite = useCanWrite()
  const id = window.location.pathname.split('/')[2] || ''
  const task = useQuery({
    queryKey: ['task', id],
    queryFn: () => api.get<Task>(`/tasks/${id}`),
  })
  const schedules = useQuery({
    queryKey: ['task-schedules', id],
    queryFn: () =>
      api
        .get<Schedule[]>('/schedules')
        .then((items) => items.filter((item) => item.task_id === id)),
  })
  const executions = useQuery({
    queryKey: ['task-executions', id],
    queryFn: () => api.get<Execution[]>(`/executions?task_id=${id}&limit=20`),
  })
  const script = useQuery({
    queryKey: ['task-script', task.data?.script_id],
    queryFn: () => api.get<Script>(`/scripts/${task.data?.script_id}`),
    enabled: Boolean(task.data?.script_id),
  })
  const nodes = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const groups = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.get<Group[]>('/groups'),
  })
  const applications = useQuery({
    queryKey: ['applications'],
    queryFn: () => api.get<Application[]>('/applications'),
  })
  if (task.isError && !task.data)
    return (
      <ErrorPage
        title={t('tasks.title')}
        error={task.error}
        onRetry={() => task.refetch()}
        backTo='/tasks'
      />
    )
  if (!task.data)
    return (
      <>
        <NodeSteerHeader title={t('tasks.title')} />
        <Main>
          <LoadingState />
        </Main>
      </>
    )
  const item = task.data
  return (
    <>
      <NodeSteerHeader
        title={item.name}
        description={`${item.type} · r${item.revision}`}
        action={
          canRun || canWrite ? (
            <div className='flex items-center gap-2'>
              {canRun ? (
                <Button asChild>
                  <a href={`/tasks/${id}/run`}>
                    <Play className='me-1 size-4' />
                    {t('tasks.runNow')}
                  </a>
                </Button>
              ) : null}
              {canWrite ? (
                <Button asChild variant='outline'>
                  <a href={`/tasks/${id}/edit`}>{t('common.edit')}</a>
                </Button>
              ) : null}
            </div>
          ) : null
        }
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <Button asChild variant='ghost' className='w-fit px-0'>
          <Link to='/tasks'>← {t('common.back')}</Link>
        </Button>
        <div className='flex flex-wrap items-center gap-3 text-xs text-muted-foreground'>
          <StatusBadge status={item.enabled ? 'enabled' : 'disabled'} />
          <span>
            {t(`tasks.types.${item.type}`, { defaultValue: item.type })}
          </span>
          <span className='font-mono'>r{item.revision}</span>
        </div>
        <SectionGrid>
          <SectionCard title={t('tasks.overviewTab')}>
            <DetailGrid>
              <DetailField label={t('common.type')}>
                {t(`tasks.types.${item.type}`, { defaultValue: item.type })}
              </DetailField>
              <DetailField label={t('common.status')}>
                <StatusBadge status={item.enabled ? 'enabled' : 'disabled'} />
              </DetailField>
              <DetailField label={t('tasks.revision')}>
                <span className='font-mono text-xs'>r{item.revision}</span>
              </DetailField>
              <DetailField label={t('common.created')}>
                <TimeValue value={item.created_at} absolute />
              </DetailField>
              <DetailField label={t('scripts.updated')}>
                <TimeValue value={item.updated_at} absolute />
              </DetailField>
            </DetailGrid>
          </SectionCard>
          <SectionCard title={t('tasks.definitionTab')}>
            {item.command ? (
              <pre className='overflow-auto rounded-md bg-muted p-4 font-mono text-xs leading-6'>
                {item.command}
              </pre>
            ) : item.script_id ? (
              <div className='grid gap-1 text-sm'>
                <a
                  className='font-medium hover:underline'
                  href={`/scripts/${item.script_id}`}
                >
                  {script.data?.name || t('common.unknownScript')}
                </a>
                <span className='font-mono text-xs text-muted-foreground'>
                  {script.data?.interpreter || 'script'}
                </span>
              </div>
            ) : (
              <div className='text-sm text-muted-foreground'>
                {applications.data?.find(
                  (application) => application.id === item.application_id
                )?.name ||
                  (item.application_id ? t('common.unknownApplication') : '-')}
              </div>
            )}
          </SectionCard>
          <SectionCard title={t('tasks.conditionSection')}>
            {item.condition ? (
              <p className='font-mono text-xs text-muted-foreground'>
                {conditionSummaryText(
                  item.condition,
                  (nodeId) =>
                    (nodes.data || []).find((node) => node.id === nodeId)
                      ?.hostname || t('common.unknownNode')
                )}
              </p>
            ) : (
              <div className='text-sm text-muted-foreground'>
                {t('tasks.noCondition')}
              </div>
            )}
          </SectionCard>
          <div id='task-schedules' className='scroll-mt-4'>
            <SectionCard
              title={t('tasks.scheduleTab')}
              action={
                canWrite ? (
                  <Button asChild variant='outline' size='sm'>
                    <Link to='/schedules/new' search={{ task_id: item.id }}>
                      <Plus className='me-1 size-3.5' />
                      {t('schedules.newSchedule')}
                    </Link>
                  </Button>
                ) : null
              }
            >
              {schedules.data?.length ? (
                <div className='divide-y'>
                  {schedules.data.map((schedule) => (
                    <div
                      className='flex flex-wrap items-center gap-x-3 gap-y-1 py-3 text-sm last:border-0'
                      key={schedule.id}
                    >
                      <a
                        className='min-w-0 truncate font-medium hover:underline'
                        href={`/schedules/${schedule.id}`}
                        title={scheduleDisplayName(schedule, t)}
                      >
                        {scheduleDisplayName(schedule, t)}
                      </a>
                      <span className='font-mono text-xs text-muted-foreground'>
                        {scheduleTriggerText(schedule, t)}
                      </span>
                      <span className='text-xs text-muted-foreground'>
                        {targetSummary(
                          schedule.target,
                          t,
                          new Map(
                            (nodes.data || []).map((node) => [
                              node.id,
                              node.hostname,
                            ])
                          ),
                          new Map(
                            (groups.data || []).map((group) => [
                              group.id,
                              group.name,
                            ])
                          )
                        )}
                      </span>
                      <span className='ms-auto'>
                        <StatusBadge
                          status={schedule.enabled ? 'enabled' : 'disabled'}
                        />
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <div className='text-sm text-muted-foreground'>
                  {t('tasks.noSchedules')}
                </div>
              )}
            </SectionCard>
          </div>
          <SectionFull>
            <SectionCard title={t('tasks.executionsTab')}>
              <ExecutionHistory executions={executions.data || []} />
            </SectionCard>
          </SectionFull>
        </SectionGrid>
      </Main>
    </>
  )
}

function ExecutionHistory({ executions }: { executions: Execution[] }) {
  const { t } = useTranslation()
  if (!executions.length)
    return (
      <div className='text-sm text-muted-foreground'>
        {t('tasks.noExecutions')}
      </div>
    )
  return (
    <TableCard>
      <Table className='min-w-[460px] table-fixed'>
        <colgroup>
          <col className='w-[22%]' />
          <col className='w-[56%]' />
          <col className='w-[22%]' />
        </colgroup>
        <TableHeader>
          <TableRow>
            <TableHead>{t('common.status')}</TableHead>
            <TableHead>{t('executions.start')}</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {executions.map((execution) => (
            <TableRow key={execution.id}>
              <TableCell>
                <StatusBadge status={execution.status} />
              </TableCell>
              <TableCell>
                <TimeValue value={execution.start_time} />
              </TableCell>
              <TableCell>
                <a
                  className='text-sm text-primary hover:underline'
                  href={`/executions/${execution.id}`}
                >
                  {t('common.viewDetails')}
                </a>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </TableCard>
  )
}

export function TaskEditor() {
  const { t } = useTranslation()
  const canWrite = useCanWrite()
  const id = window.location.pathname.split('/')[2] || ''
  const editing = id !== 'new'
  const client = useQueryClient()
  const current = useQuery({
    queryKey: ['task', id],
    queryFn: () => api.get<Task>(`/tasks/${id}`),
    enabled: editing,
  })
  const scripts = useQuery({
    queryKey: ['scripts'],
    queryFn: () => api.get<Script[]>('/scripts'),
  })
  const applications = useQuery({
    queryKey: ['applications'],
    queryFn: () => api.get<Application[]>('/applications'),
  })
  const defaultForm: Partial<Task> = {
    name: '',
    type: 'command',
    command: '',
    script_id: '',
    application_id: '',
    app_operation: 'start',
    parameters: [],
    param_values: {},
    enabled: true,
  }
  const [draft, setDraft] = useState<Partial<Task> | null>(null)
  const form = draft || current.data || defaultForm
  const setForm = (
    next: Partial<Task> | ((previous: Partial<Task>) => Partial<Task>)
  ) =>
    setDraft((previous) => {
      const base = previous || current.data || defaultForm
      return typeof next === 'function' ? next(base) : next
    })
  const [parameter, setParameter] = useState<Parameter>({
    name: '',
    type: 'string',
    required: false,
    default: '',
  })
  const [localCondition, setLocalCondition] = useState({
    metric: 'cpu_usage',
    operator: '>',
    value: '',
    path: '',
    command: '',
  })
  const [andConditions, setAndConditions] = useState(false)
  useEffect(() => {
    if (!current.data || draft) return
    const condition = current.data.condition
    const items =
      condition?.type === 'and'
        ? condition.and || []
        : condition
          ? [condition]
          : []
    const local = items.find((item) => item.local)?.local
    // The editor controls are local draft state hydrated once the async query resolves.
    if (local)
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setLocalCondition({
        metric: local.metric,
        operator: local.operator,
        value: local.value,
        path: local.path || '',
        command: local.command || '',
      })
    setAndConditions(items.length > 1)
  }, [current.data, draft])
  const [saving, setSaving] = useState(false)
  const savingRef = useRef(false)
  const save = async () => {
    if (savingRef.current) return
    if (!form.name) {
      toast.error(t('common.name'))
      return
    }
    savingRef.current = true
    setSaving(true)
    try {
      const payload = buildTaskPayload(form)
      if (editing) await api.put(`/tasks/${id}`, payload)
      else await api.post('/tasks', payload)
      await client.invalidateQueries({ queryKey: ['tasks'] })
      window.location.assign('/tasks')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('common.noData'))
    } finally {
      savingRef.current = false
      setSaving(false)
    }
  }
  // 操作下拉的候选与当前值：按类型归一化，避免 stale 值（如 command 的 start）漏进 app_deploy。
  const currentOperation = form.app_operation || ''
  const appOperationOptions =
    form.type === 'app_deploy' ? appDeployOperations : appOperations
  const selectedAppOperation = appOperationOptions.includes(currentOperation)
    ? currentOperation
    : appOperationOptions[0]
  const addParameter = () => {
    if (!parameter.name) return
    setForm((previous) => ({
      ...previous,
      parameters: [...(previous.parameters || []), parameter],
    }))
    setParameter({ name: '', type: 'string', required: false, default: '' })
  }
  const mergeCondition = (candidate: NonNullable<Task['condition']>) =>
    setForm((previous) => {
      if (!andConditions) return { ...previous, condition: candidate }
      const currentItems =
        previous.condition?.type === 'and'
          ? previous.condition.and || []
          : previous.condition
            ? [previous.condition]
            : []
      // 相同条件不重复追加，避免打开既有 AND 条件后点击保存造成重复。
      if (
        currentItems.some(
          (item) => JSON.stringify(item) === JSON.stringify(candidate)
        )
      ) {
        return previous
      }
      return {
        ...previous,
        condition: { type: 'and', and: [...currentItems, candidate] },
      }
    })
  const setLocal = () =>
    mergeCondition({ type: 'local', local: localCondition })
  const clearCondition = (type: 'local' | 'remote') =>
    setForm((previous) => {
      if (previous.condition?.type !== 'and')
        return { ...previous, condition: undefined }
      const remaining = (previous.condition.and || []).filter(
        (item) => item.type !== type
      )
      return {
        ...previous,
        condition:
          remaining.length === 1
            ? remaining[0]
            : remaining.length
              ? { type: 'and', and: remaining }
              : undefined,
      }
    })
  if (editing && current.isError && !current.data)
    return (
      <ErrorPage
        title={t('tasks.editTitle')}
        error={current.error}
        onRetry={() => current.refetch()}
        backTo='/tasks'
      />
    )
  if (editing && current.isPending && !draft)
    return (
      <>
        <NodeSteerHeader
          title={t('tasks.editTitle')}
          description={t('tasks.formDescription')}
        />
        <Main>
          <LoadingState />
        </Main>
      </>
    )
  return (
    <>
      <NodeSteerHeader
        title={editing ? t('tasks.editTitle') : t('tasks.newTitle')}
        description={t('tasks.formDescription')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <Card className='w-full'>
          <CardHeader>
            <CardTitle className='text-base'>
              {t('tasks.definitionTab')}
            </CardTitle>
          </CardHeader>
          <CardContent className='grid gap-6'>
            <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
              <label className='grid gap-2 text-sm font-medium sm:col-span-2'>
                {t('common.name')}
                <Input
                  required
                  value={form.name || ''}
                  onChange={(event) =>
                    setForm({ ...form, name: event.target.value })
                  }
                />
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('common.type')}
                <Select
                  value={form.type || 'command'}
                  onValueChange={(value) => {
                    const next: Partial<Task> = {
                      ...form,
                      type: value,
                      command: value === 'command' ? form.command : '',
                    }
                    // 类型切换同步归一化 app_operation：应用类型给合法值，其余类型删除字段。
                    const operation = normalizeAppOperation(
                      value,
                      form.app_operation
                    )
                    if (operation === undefined) delete next.app_operation
                    else next.app_operation = operation
                    setForm(next)
                  }}
                >
                  <SelectTrigger className='w-full'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='command'>
                      {t('tasks.command')}
                    </SelectItem>
                    <SelectItem value='script'>{t('tasks.script')}</SelectItem>
                    <SelectItem value='app_deploy'>
                      {t('tasks.appDeploy')}
                    </SelectItem>
                    <SelectItem value='app_operation'>
                      {t('tasks.appOperation')}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <label className='flex items-center gap-2 self-end pb-2 text-sm font-medium'>
                <Checkbox
                  checked={form.enabled !== false}
                  onCheckedChange={(checked) =>
                    setForm({ ...form, enabled: checked === true })
                  }
                />
                {t('common.enabled')}
              </label>
            </div>
            {form.type === 'command' && (
              <label className='grid gap-2 text-sm font-medium'>
                {t('tasks.command')}
                <Textarea
                  className='min-h-40 font-mono text-xs'
                  value={form.command || ''}
                  onChange={(event) =>
                    setForm({ ...form, command: event.target.value })
                  }
                />
              </label>
            )}
            {form.type === 'script' && (
              <label className='grid gap-2 text-sm font-medium'>
                {t('tasks.script')}
                <Select
                  value={form.script_id || ''}
                  onValueChange={(value) =>
                    setForm({ ...form, script_id: value })
                  }
                >
                  <SelectTrigger className='w-full'>
                    <SelectValue placeholder={t('tasks.selectScript')} />
                  </SelectTrigger>
                  <SelectContent>
                    {(scripts.data || []).map((script) => (
                      <SelectItem key={script.id} value={script.id}>
                        {script.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
            )}
            {(form.type === 'app_deploy' || form.type === 'app_operation') && (
              <div className='grid gap-4 sm:grid-cols-2'>
                <label className='grid gap-2 text-sm font-medium'>
                  {t('tasks.application')}
                  <Select
                    value={form.application_id || ''}
                    onValueChange={(value) =>
                      setForm({ ...form, application_id: value })
                    }
                  >
                    <SelectTrigger className='w-full'>
                      <SelectValue placeholder={t('tasks.selectApp')} />
                    </SelectTrigger>
                    <SelectContent>
                      {(applications.data || []).map((application) => (
                        <SelectItem key={application.id} value={application.id}>
                          {application.name}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>
                {['app_operation', 'app_deploy'].includes(form.type || '') && (
                  <label className='grid gap-2 text-sm font-medium'>
                    {t('tasks.operation')}
                    <Select
                      value={selectedAppOperation}
                      onValueChange={(value) =>
                        setForm({ ...form, app_operation: value })
                      }
                    >
                      <SelectTrigger className='w-full'>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {appOperationOptions.map((operation) => (
                          <SelectItem key={operation} value={operation}>
                            {t(`tasks.${operation}`)}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </label>
                )}
              </div>
            )}
          </CardContent>
        </Card>
        <Card className='w-full'>
          <CardHeader>
            <CardTitle className='text-base'>{t('tasks.parameters')}</CardTitle>
          </CardHeader>
          <CardContent className='grid gap-4'>
            <div className='grid gap-3 sm:grid-cols-[1fr_140px_1fr_auto_auto]'>
              <Input
                aria-label={t('tasks.paramName')}
                placeholder={t('tasks.paramName')}
                value={parameter.name}
                onChange={(event) =>
                  setParameter({ ...parameter, name: event.target.value })
                }
              />
              <Select
                value={parameter.type}
                onValueChange={(value) =>
                  setParameter({ ...parameter, type: value })
                }
              >
                <SelectTrigger
                  aria-label={
                    parameter.name
                      ? `${t('tasks.paramType')}: ${parameter.name}`
                      : t('tasks.paramType')
                  }
                >
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {['string', 'number', 'bool', 'secret'].map((type) => (
                    <SelectItem key={type} value={type}>
                      {type}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Input
                type={parameter.type === 'secret' ? 'password' : 'text'}
                aria-label={t('tasks.paramDefault')}
                placeholder={t('tasks.paramDefault')}
                value={parameter.default || ''}
                onChange={(event) =>
                  setParameter({ ...parameter, default: event.target.value })
                }
              />
              <label className='flex items-center gap-2 text-sm font-medium'>
                <Checkbox
                  checked={parameter.required}
                  onCheckedChange={(checked) =>
                    setParameter({ ...parameter, required: checked === true })
                  }
                />
                {t('tasks.required')}
              </label>
              <Button type='button' variant='outline' onClick={addParameter}>
                {t('tasks.addParam')}
              </Button>
            </div>
            {(form.parameters || []).length > 0 && (
              <div className='divide-y border'>
                {(form.parameters || []).map((item, index) => (
                  <div
                    className='flex items-center gap-3 px-3 py-2 text-sm'
                    key={`${item.name}-${index}`}
                  >
                    <span className='font-mono'>{item.name}</span>
                    <span className='text-muted-foreground'>{item.type}</span>
                    {item.required && (
                      <span className='text-destructive'>
                        {t('tasks.required')}
                      </span>
                    )}
                    <span className='text-muted-foreground'>
                      {item.default
                        ? item.type === 'secret'
                          ? '••••••••'
                          : item.default
                        : '-'}
                    </span>
                    <Button
                      className='ms-auto'
                      type='button'
                      variant='ghost'
                      size='sm'
                      onClick={() =>
                        setForm({
                          ...form,
                          parameters: (form.parameters || []).filter(
                            (_, itemIndex) => itemIndex !== index
                          ),
                        })
                      }
                    >
                      {t('common.remove')}
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
        <Card className='w-full'>
          <CardHeader>
            <CardTitle className='text-base'>
              {t('tasks.localCondition')}
            </CardTitle>
          </CardHeader>
          <CardContent className='grid gap-3 sm:grid-cols-[1fr_120px_1fr_auto]'>
            <label className='flex items-center gap-2 text-sm font-medium sm:col-span-4'>
              <Checkbox
                checked={andConditions}
                onCheckedChange={(checked) =>
                  setAndConditions(checked === true)
                }
              />
              {t('tasks.andConditions')}
            </label>
            <Select
              value={localCondition.metric}
              onValueChange={(value) =>
                setLocalCondition({ ...localCondition, metric: value })
              }
            >
              <SelectTrigger aria-label={t('tasks.conditionMetric')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {[
                  'cpu_usage',
                  'memory_usage',
                  'disk_usage',
                  'file_exists',
                  'dir_exists',
                  'process_exists',
                  'port_listening',
                  'command_result',
                ].map((metric) => (
                  <SelectItem key={metric} value={metric}>
                    {metric}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={localCondition.operator}
              onValueChange={(value) =>
                setLocalCondition({ ...localCondition, operator: value })
              }
            >
              <SelectTrigger aria-label={t('tasks.conditionOperator')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {['==', '!=', '>', '<', '>=', '<='].map((operator) => (
                  <SelectItem key={operator} value={operator}>
                    {operator}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Input
              aria-label={t('tasks.localConditionValue')}
              placeholder='value'
              value={localCondition.value}
              onChange={(event) =>
                setLocalCondition({
                  ...localCondition,
                  value: event.target.value,
                })
              }
            />
            {(localCondition.metric === 'disk_usage' ||
              localCondition.metric === 'file_exists' ||
              localCondition.metric === 'dir_exists' ||
              localCondition.metric === 'process_exists' ||
              localCondition.metric === 'port_listening') && (
              <Input
                aria-label={t('tasks.conditionPath')}
                placeholder={t('tasks.conditionPath')}
                value={localCondition.path}
                onChange={(event) =>
                  setLocalCondition({
                    ...localCondition,
                    path: event.target.value,
                  })
                }
              />
            )}
            {localCondition.metric === 'command_result' && (
              <Input
                aria-label={t('tasks.conditionCommand')}
                placeholder={t('tasks.conditionCommand')}
                value={localCondition.command}
                onChange={(event) =>
                  setLocalCondition({
                    ...localCondition,
                    command: event.target.value,
                  })
                }
              />
            )}
            <div className='flex items-center gap-2'>
              <Button type='button' variant='outline' onClick={setLocal}>
                {t('tasks.setCondition')}
              </Button>
              {form.condition?.local && (
                <Button
                  type='button'
                  variant='ghost'
                  onClick={() => clearCondition('local')}
                >
                  {t('tasks.clear')}
                </Button>
              )}
            </div>
            {form.condition?.local && (
              <p className='font-mono text-xs text-muted-foreground sm:col-span-4'>
                {form.condition.local.metric} {form.condition.local.operator}{' '}
                {form.condition.local.value}
              </p>
            )}
          </CardContent>
        </Card>
        {form.condition ? (
          <Card className='w-full'>
            <CardHeader>
              <CardTitle className='text-base'>
                {t('tasks.conditionSummary')}
              </CardTitle>
            </CardHeader>
            <CardContent className='grid gap-3'>
              <p
                className='font-mono text-xs text-muted-foreground'
                data-testid='condition-summary'
              >
                {conditionSummaryText(form.condition, () => '')}
              </p>
              <Button
                type='button'
                variant='outline'
                className='w-fit'
                onClick={() => setForm({ ...form, condition: undefined })}
              >
                {t('tasks.clearConditions')}
              </Button>
            </CardContent>
          </Card>
        ) : null}
        <div className='flex items-center gap-2'>
          {canWrite ? (
            <Button onClick={save} disabled={saving} aria-busy={saving}>
              {saving ? <Loader2 className='animate-spin' /> : null}
              {t('common.save')}
            </Button>
          ) : null}
          <Button asChild variant='outline'>
            <Link to='/tasks'>{t('common.cancel')}</Link>
          </Button>
        </div>
      </Main>
    </>
  )
}

export function RunTask() {
  const { t } = useTranslation()
  const canRun = useCanRun()
  const id = window.location.pathname.split('/')[2] || ''
  const task = useQuery({
    queryKey: ['task', id],
    queryFn: () => api.get<Task>(`/tasks/${id}`),
  })
  const nodes = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const groups = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.get<Group[]>('/groups'),
  })
  const [running, setRunning] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [params, setParams] = useState<Record<string, string>>({})
  const [target, setTarget] = useState<Target>({
    type: 'node',
    node_ids: [],
  })
  const [strategy, setStrategy] = useState<ExecutionStrategy>({
    timeout: 300,
    retry: 0,
    run_user: '',
    offline_policy: 'hub_online_required',
  })
  useEffect(() => {
    if (!task.data) return
    const defaults: Record<string, string> = {}
    for (const parameter of task.data.parameters || []) {
      defaults[parameter.name] = parameter.default || ''
    }
    // Hydrate parameter controls from the task definition after the async query resolves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setParams(defaults)
  }, [task.data])
  if (task.isError && !task.data)
    return (
      <ErrorPage
        title={t('tasks.runNow')}
        error={task.error}
        onRetry={() => task.refetch()}
        backTo='/tasks'
      />
    )
  if (!task.data)
    return (
      <>
        <NodeSteerHeader title={t('tasks.runNow')} />
        <Main>
          <LoadingState />
        </Main>
      </>
    )
  const targetValid = isTargetComplete(target)
  const run = async () => {
    const missing = (task.data?.parameters || []).find(
      (parameter) => parameter.required && !params[parameter.name]
    )
    if (missing) {
      toast.error(`${missing.name} ${t('tasks.required')}`)
      return
    }
    if (!targetValid) {
      toast.error(t('tasks.targetRequired'))
      return
    }
    setRunning(true)
    try {
      await api.post(`/tasks/${id}/run`, {
        target: normalizeTarget(target),
        params,
        timeout: strategy.timeout,
        retry: strategy.retry,
        run_user: strategy.run_user || undefined,
        offline_policy: strategy.offline_policy,
      })
      window.location.assign('/executions')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('common.noData'))
      setRunning(false)
    }
  }
  return (
    <>
      <NodeSteerHeader
        title={`${t('tasks.runTitle')}${task.data?.name || id}`}
        description={t('tasks.runDescription')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <Card className='w-full'>
          <CardHeader>
            <CardTitle className='text-base'>{t('tasks.runTarget')}</CardTitle>
          </CardHeader>
          <CardContent className='grid gap-3'>
            <TargetEditor
              target={target}
              onChange={setTarget}
              nodes={nodes.data || []}
              groups={groups.data || []}
            />
            {!targetValid ? (
              <p className='text-xs text-destructive' role='alert'>
                {t('tasks.targetRequired')}
              </p>
            ) : (
              <p className='text-xs text-muted-foreground'>
                {targetSummary(
                  target,
                  t,
                  new Map(
                    (nodes.data || []).map((node) => [node.id, node.hostname])
                  ),
                  new Map(
                    (groups.data || []).map((group) => [group.id, group.name])
                  )
                )}
              </p>
            )}
          </CardContent>
        </Card>
        {task.data?.condition ? (
          <Card className='w-full'>
            <CardHeader>
              <CardTitle className='text-base'>
                {t('tasks.localCondition')}
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className='font-mono text-xs text-muted-foreground'>
                {conditionSummaryText(task.data.condition, () => '')}
              </p>
            </CardContent>
          </Card>
        ) : null}
        <Card className='w-full'>
          <CardHeader>
            <CardTitle className='text-base'>
              {t('tasks.runStrategy')}
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScheduleStrategyFields value={strategy} onChange={setStrategy} />
          </CardContent>
        </Card>
        {task.data?.parameters?.length ? (
          <Card className='w-full'>
            <CardHeader>
              <CardTitle className='text-base'>
                {t('tasks.runParams')}
              </CardTitle>
            </CardHeader>
            <CardContent className='grid gap-3'>
              {task.data.parameters.map((parameter) => (
                <label
                  key={parameter.name}
                  className='grid gap-2 text-sm font-medium'
                >
                  {parameter.name}
                  <Input
                    type={parameter.type === 'secret' ? 'password' : 'text'}
                    required={parameter.required}
                    value={params[parameter.name] || ''}
                    onChange={(event) =>
                      setParams({
                        ...params,
                        [parameter.name]: event.target.value,
                      })
                    }
                  />
                </label>
              ))}
            </CardContent>
          </Card>
        ) : null}
        <div className='flex items-center gap-2'>
          {canRun ? (
            <Button
              onClick={() => setConfirmOpen(true)}
              disabled={running || !targetValid}
            >
              {running ? t('tasks.running') : t('tasks.runNow')}
            </Button>
          ) : null}
          <Button asChild variant='outline'>
            <a href={`/tasks/${id}`}>{t('common.cancel')}</a>
          </Button>
        </div>
        {canRun ? (
          <ConfirmDialog
            open={confirmOpen}
            onOpenChange={setConfirmOpen}
            title={t('tasks.confirmRun', { name: task.data?.name || id })}
            desc={t('tasks.runDescription')}
            cancelBtnText={t('common.cancel')}
            confirmText={t('tasks.runNow')}
            handleConfirm={run}
            isLoading={running}
          />
        ) : null}
      </Main>
    </>
  )
}

function TaskRowActions({
  task,
  canRun,
  canWrite,
  toggleTask,
  removeTask,
}: {
  task: Task
  canRun: boolean
  canWrite: boolean
  toggleTask: (task: Task) => void
  removeTask: (id: string) => void
}) {
  const { t } = useTranslation()
  return (
    <div className='flex items-center gap-1'>
      {canRun ? (
        <Button
          asChild
          variant='ghost'
          size='icon'
          className='size-8'
          aria-label={t('tasks.runNow')}
        >
          <a href={`/tasks/${task.id}/run`}>
            <Play className='size-4' />
          </a>
        </Button>
      ) : null}
      {canWrite ? (
        <MoreMenu>
          <DropdownMenuItem asChild>
            <a href={`/tasks/${task.id}/edit`}>{t('common.edit')}</a>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => toggleTask(task)}>
            {task.enabled ? t('common.disable') : t('common.enable')}
          </DropdownMenuItem>
          <DangerMenuItem
            title={`${t('common.delete')}: ${task.name}`}
            description={t('tasks.confirmDelete')}
            onSelect={() => removeTask(task.id)}
          />
        </MoreMenu>
      ) : null}
    </div>
  )
}
