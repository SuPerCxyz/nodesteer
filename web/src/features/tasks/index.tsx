import { useEffect, useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { ColumnDef } from '@tanstack/react-table'
import { Play, MoreHorizontal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import {
  api,
  type Application,
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
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Textarea } from '@/components/ui/textarea'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { CadentraHeader } from '@/components/layout/cadentra-header'
import { Main } from '@/components/layout/main'
import { DataTable } from '@/features/shared/data-table'
import {
  ErrorState,
  LoadingState,
  StatusBadge,
  TableCard,
  TimeValue,
} from '@/features/shared/ui'

function targetText(task: Task, translate: (key: string) => string) {
  if (task.target.type === 'node')
    return `${task.target.node_ids?.length || 0} ${translate('tasks.nodes')}`
  if (task.target.type === 'group')
    return `${task.target.group_ids?.length || 0} ${translate('tasks.groups')}`
  return task.target.label_key
    ? `${task.target.label_key}=${task.target.label_value || ''}`
    : task.target.type
}

function TargetDetails({
  target,
  nodes,
  groups,
}: {
  target: Target
  nodes: Node[]
  groups: Group[]
}) {
  const { t } = useTranslation()
  const nodeNames = new Map(nodes.map((node) => [node.id, node.hostname]))
  const groupNames = new Map(groups.map((group) => [group.id, group.name]))
  return (
    <dl className='grid gap-x-8 gap-y-3 text-sm sm:grid-cols-[160px_minmax(0,1fr)]'>
      <dt className='text-muted-foreground'>{t('tasks.targetType')}</dt>
      <dd>
        {t(`tasks.targetTypes.${target.type}`, {
          defaultValue: target.type,
        })}
      </dd>
      {target.node_ids?.length ? (
        <>
          <dt className='text-muted-foreground'>{t('tasks.nodes')}</dt>
          <dd className='break-words'>
            {target.node_ids
              .map((id) => nodeNames.get(id) || t('common.unknownNode'))
              .join(', ')}
          </dd>
        </>
      ) : null}
      {target.group_ids?.length ? (
        <>
          <dt className='text-muted-foreground'>{t('tasks.groups')}</dt>
          <dd className='break-words'>
            {target.group_ids
              .map((id) => groupNames.get(id) || t('common.noData'))
              .join(', ')}
          </dd>
        </>
      ) : null}
      {target.label_key ? (
        <>
          <dt className='text-muted-foreground'>{t('tasks.labelKey')}</dt>
          <dd className='font-mono text-xs'>{target.label_key}</dd>
          <dt className='text-muted-foreground'>{t('tasks.labelValue')}</dt>
          <dd className='font-mono text-xs'>{target.label_value || '-'}</dd>
        </>
      ) : null}
    </dl>
  )
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
  const { mutate: toggleTask } = useMutation({
    mutationFn: (task: Task) =>
      api.put<Task>(`/tasks/${task.id}`, { ...task, enabled: !task.enabled }),
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
        size: 280,
        minSize: 220,
        maxSize: 360,
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
        size: 100,
        minSize: 88,
        cell: ({ row }) => (
          <span className='font-mono text-xs'>
            {t(`tasks.types.${row.original.type}`, {
              defaultValue: row.original.type,
            })}
          </span>
        ),
      },
      {
        id: 'target',
        accessorFn: (row) => targetText(row, t),
        header: t('tasks.target'),
        size: 180,
        minSize: 140,
        cell: ({ row }) => (
          <span
            className='block truncate text-sm'
            title={targetText(row.original, t)}
          >
            {targetText(row.original, t)}
          </span>
        ),
      },
      {
        accessorKey: 'timeout',
        header: t('scripts.timeout'),
        size: 90,
        minSize: 80,
        meta: { align: 'end' },
        cell: ({ row }) => (
          <span className='font-mono text-xs'>{row.original.timeout}s</span>
        ),
      },
      {
        accessorKey: 'offline_policy',
        header: t('tasks.offlinePolicy'),
        size: 170,
        minSize: 150,
        cell: ({ row }) => (
          <span className='font-mono text-xs'>
            {t(`tasks.offlinePolicies.${row.original.offline_policy}`, {
              defaultValue: row.original.offline_policy,
            })}
          </span>
        ),
      },
      {
        accessorKey: 'revision',
        header: t('tasks.revision'),
        size: 100,
        minSize: 88,
        meta: { align: 'center' },
        cell: ({ row }) => (
          <span className='font-mono text-xs'>r{row.original.revision}</span>
        ),
      },
      {
        id: 'status',
        accessorFn: (row) => (row.enabled ? 'enabled' : 'disabled'),
        header: t('common.status'),
        size: 104,
        minSize: 96,
        meta: { align: 'center' },
        cell: ({ row }) => (
          <StatusBadge status={row.original.enabled ? 'enabled' : 'disabled'} />
        ),
      },
      {
        id: 'actions',
        enableHiding: false,
        header: '',
        size: 72,
        minSize: 64,
        meta: { align: 'end' },
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
    [canRun, canWrite, removeTask, t, toggleTask]
  )

  return (
    <>
      <CadentraHeader
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
      <Main fluid className='flex flex-1 flex-col gap-6'>
        {query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : (
          <DataTable
            data={query.data || []}
            columns={columns}
            searchPlaceholder={t('tasks.searchPlaceholder')}
          />
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
  if (task.isLoading)
    return (
      <>
        <CadentraHeader title={t('tasks.title')} />
        <Main>
          <LoadingState />
        </Main>
      </>
    )
  if (task.isError || !task.data)
    return (
      <>
        <CadentraHeader title={t('tasks.title')} />
        <Main>
          <ErrorState error={task.error} onRetry={() => task.refetch()} />
        </Main>
      </>
    )
  const item = task.data
  return (
    <>
      <CadentraHeader
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
          <span>{targetText(item, t)}</span>
          <span className='font-mono'>r{item.revision}</span>
        </div>
        <Tabs defaultValue='overview'>
          <TabsList>
            <TabsTrigger value='overview'>{t('tasks.overviewTab')}</TabsTrigger>
            <TabsTrigger value='definition'>
              {t('tasks.definitionTab')}
            </TabsTrigger>
            <TabsTrigger value='targets'>{t('tasks.targetsTab')}</TabsTrigger>
            <TabsTrigger value='executions'>
              {t('tasks.executionsTab')}
            </TabsTrigger>
            <TabsTrigger value='schedule'>{t('tasks.scheduleTab')}</TabsTrigger>
          </TabsList>
          <TabsContent value='overview' className='mt-4 max-w-4xl'>
            <Card>
              <CardHeader>
                <CardTitle className='text-sm'>
                  {t('tasks.overviewTab')}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <dl className='grid gap-x-8 gap-y-3 text-sm sm:grid-cols-[160px_1fr]'>
                  <dt className='text-muted-foreground'>{t('common.type')}</dt>
                  <dd>
                    {t(`tasks.types.${item.type}`, { defaultValue: item.type })}
                  </dd>
                  <dt className='text-muted-foreground'>{t('tasks.target')}</dt>
                  <dd>{targetText(item, t)}</dd>
                  <dt className='text-muted-foreground'>
                    {t('tasks.timeoutSec')}
                  </dt>
                  <dd className='font-mono text-xs'>{item.timeout}s</dd>
                  <dt className='text-muted-foreground'>{t('tasks.retry')}</dt>
                  <dd className='font-mono text-xs'>{item.retry}</dd>
                  <dt className='text-muted-foreground'>
                    {t('tasks.offlinePolicy')}
                  </dt>
                  <dd>
                    {t(`tasks.offlinePolicies.${item.offline_policy}`, {
                      defaultValue: item.offline_policy,
                    })}
                  </dd>
                </dl>
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value='definition' className='mt-4 max-w-4xl'>
            <Card>
              <CardHeader>
                <CardTitle className='text-sm'>
                  {t('tasks.definitionTab')}
                </CardTitle>
              </CardHeader>
              <CardContent>
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
                      (item.application_id
                        ? t('common.unknownApplication')
                        : '-')}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value='targets' className='mt-4 max-w-4xl'>
            <Card>
              <CardHeader>
                <CardTitle className='text-sm'>
                  {t('tasks.targetsTab')}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <TargetDetails
                  target={item.target}
                  nodes={nodes.data || []}
                  groups={groups.data || []}
                />
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value='executions' className='mt-4 max-w-4xl'>
            <Card>
              <CardHeader>
                <CardTitle className='text-sm'>
                  {t('tasks.executionsTab')}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <ExecutionHistory executions={executions.data || []} />
              </CardContent>
            </Card>
          </TabsContent>
          <TabsContent value='schedule' className='mt-4 max-w-4xl'>
            <Card>
              <CardHeader>
                <CardTitle className='text-sm'>
                  {t('tasks.scheduleTab')}
                </CardTitle>
              </CardHeader>
              <CardContent>
                {schedules.data?.length ? (
                  schedules.data.map((schedule) => (
                    <div
                      className='flex items-center justify-between border-b py-3 text-sm last:border-0'
                      key={schedule.id}
                    >
                      <a
                        className='font-medium hover:underline'
                        href={`/schedules/${schedule.id}`}
                      >
                        {t(`schedules.types.${schedule.type}`, {
                          defaultValue: schedule.type,
                        })}
                      </a>
                      <span className='font-mono text-xs'>
                        {schedule.expression || `${schedule.interval_sec}s`}
                      </span>
                    </div>
                  ))
                ) : (
                  <div className='text-sm text-muted-foreground'>
                    {t('tasks.noSchedules')}
                  </div>
                )}
              </CardContent>
            </Card>
          </TabsContent>
        </Tabs>
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
          <col className='w-[30%]' />
          <col className='w-[55%]' />
          <col className='w-[15%]' />
        </colgroup>
        <TableHeader>
          <TableRow>
            <TableHead className='text-center'>{t('common.status')}</TableHead>
            <TableHead>{t('executions.start')}</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {executions.map((execution) => (
            <TableRow key={execution.id}>
              <TableCell className='text-center'>
                <StatusBadge status={execution.status} />
              </TableCell>
              <TableCell>
                <TimeValue value={execution.start_time} />
              </TableCell>
              <TableCell className='text-end'>
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
  const nodes = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const scripts = useQuery({
    queryKey: ['scripts'],
    queryFn: () => api.get<Script[]>('/scripts'),
  })
  const groups = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.get<Group[]>('/groups'),
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
    target: { type: 'node', node_ids: [] },
    parameters: [],
    param_values: {},
    timeout: 300,
    retry: 0,
    run_user: '',
    offline_policy: 'hub_online_required',
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
  const [remoteCondition, setRemoteCondition] = useState({
    node_id: '',
    property: 'online',
    task_id: '',
    operator: '==',
    value: 'online',
  })
  const [andConditions, setAndConditions] = useState(false)
  useEffect(() => {
    if (!current.data || draft) return
    const condition = current.data.condition
    const items = condition?.type === 'and' ? condition.and || [] : condition ? [condition] : []
    const local = items.find((item) => item.local)?.local
    const remote = items.find((item) => item.remote)?.remote
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
    if (remote)
      setRemoteCondition({
        node_id: remote.node_id,
        property: remote.property,
        task_id: remote.task_id || '',
        operator: remote.operator,
        value: remote.value,
      })
    setAndConditions(items.length > 1)
  }, [current.data, draft])
  const setTarget = (target: Target) =>
    setForm((previous) => ({ ...previous, target }))
  const save = async () => {
    if (!form.name) {
      toast.error(t('common.name'))
      return
    }
    try {
      if (editing) await api.put(`/tasks/${id}`, form)
      else await api.post('/tasks', form)
      await client.invalidateQueries({ queryKey: ['tasks'] })
      window.location.assign('/tasks')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('common.noData'))
    }
  }
  const selectedNodes = form.target?.node_ids || []
  const nodeNames = new Map(
    (nodes.data || []).map((node) => [node.id, node.hostname])
  )
  const toggleNode = (nodeId: string) =>
    setTarget({
      ...(form.target as Target),
      type: 'node',
      node_ids: selectedNodes.includes(nodeId)
        ? selectedNodes.filter((value) => value !== nodeId)
        : [...selectedNodes, nodeId],
    })
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
      const items = currentItems.filter((item) => item.type !== candidate.type)
      return { ...previous, condition: { type: 'and', and: [...items, candidate] } }
    })
  const setLocal = () =>
    mergeCondition({ type: 'local', local: localCondition })
  const setRemote = () => {
    if (!remoteCondition.node_id) return
    mergeCondition({ type: 'remote', remote: remoteCondition })
  }
  const clearCondition = (type: 'local' | 'remote') =>
    setForm((previous) => {
      if (previous.condition?.type !== 'and') return { ...previous, condition: undefined }
      const remaining = (previous.condition.and || []).filter((item) => item.type !== type)
      return { ...previous, condition: remaining.length === 1 ? remaining[0] : remaining.length ? { type: 'and', and: remaining } : undefined }
    })
  return (
    <>
      <CadentraHeader
        title={editing ? t('tasks.editTitle') : t('tasks.newTitle')}
        description={t('tasks.formDescription')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <Card className='max-w-4xl'>
          <CardHeader>
            <CardTitle className='text-base'>
              {t('tasks.definitionTab')}
            </CardTitle>
          </CardHeader>
          <CardContent className='grid gap-6'>
            <label className='grid gap-2 text-sm font-medium'>
              {t('common.name')}
              <Input
                required
                value={form.name || ''}
                onChange={(event) =>
                  setForm({ ...form, name: event.target.value })
                }
              />
            </label>
            <div className='grid gap-4 sm:grid-cols-2'>
              <label className='grid gap-2 text-sm font-medium'>
                {t('common.type')}
                <Select
                  value={form.type || 'command'}
                  onValueChange={(value) =>
                    setForm({
                      ...form,
                      type: value,
                      command: value === 'command' ? form.command : '',
                    })
                  }
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
              <label className='grid gap-2 text-sm font-medium'>
                {t('tasks.timeoutSec')}
                <Input
                  type='number'
                  min={0}
                  value={form.timeout ?? 0}
                  onChange={(event) =>
                    setForm({ ...form, timeout: Number(event.target.value) })
                  }
                />
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
                {form.type === 'app_operation' && (
                  <label className='grid gap-2 text-sm font-medium'>
                    {t('tasks.operation')}
                    <Select
                      value={form.app_operation || 'start'}
                      onValueChange={(value) =>
                        setForm({ ...form, app_operation: value })
                      }
                    >
                      <SelectTrigger className='w-full'>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {['start', 'stop', 'restart', 'upgrade'].map(
                          (operation) => (
                            <SelectItem key={operation} value={operation}>
                              {t(`tasks.${operation}`)}
                            </SelectItem>
                          )
                        )}
                      </SelectContent>
                    </Select>
                  </label>
                )}
              </div>
            )}
            <Separator />
            <div className='grid gap-3'>
              <div>
                <h3 className='text-sm font-medium'>{t('tasks.targetsTab')}</h3>
                <p className='text-sm text-muted-foreground'>
                  {selectedNodes.length} {t('common.selected')}
                </p>
              </div>
              <label className='grid gap-2 text-sm font-medium'>
                {t('tasks.targetType')}
                <Select
                  value={form.target?.type || 'node'}
                  onValueChange={(value) =>
                    setTarget({
                      ...(form.target as Target),
                      type: value,
                      node_ids: value === 'node' ? selectedNodes : [],
                    })
                  }
                >
                  <SelectTrigger className='w-full sm:max-w-xs'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='node'>{t('tasks.nodes')}</SelectItem>
                    <SelectItem value='group'>{t('tasks.groups')}</SelectItem>
                    <SelectItem value='label'>{t('tasks.label')}</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              {form.target?.type === 'node' && (
                <div className='grid max-h-56 gap-1 overflow-auto rounded-md border p-2'>
                  {(nodes.data || []).map((node) => (
                    <label
                      className='flex items-center gap-2 rounded px-2 py-2 text-sm hover:bg-muted'
                      key={node.id}
                    >
                      <Checkbox
                        checked={selectedNodes.includes(node.id)}
                        onCheckedChange={() => toggleNode(node.id)}
                      />
                      {node.hostname}
                      <span className='ms-auto text-xs text-muted-foreground'>
                        {node.status}
                      </span>
                    </label>
                  ))}
                </div>
              )}
              {form.target?.type === 'group' && (
                <div className='grid max-h-48 gap-1 overflow-auto rounded-md border p-2'>
                  {(groups.data || []).map((group) => {
                    const selected = form.target?.group_ids || []
                    return (
                      <label
                        className='flex items-center gap-2 rounded px-2 py-2 text-sm hover:bg-muted'
                        key={group.id}
                      >
                        <Checkbox
                          checked={selected.includes(group.id)}
                          onCheckedChange={(checked) =>
                            setTarget({
                              ...(form.target as Target),
                              group_ids: checked
                                ? [...selected, group.id]
                                : selected.filter(
                                    (value) => value !== group.id
                                  ),
                            })
                          }
                        />
                        {group.name}
                      </label>
                    )
                  })}
                </div>
              )}
              {form.target?.type === 'label' && (
                <div className='grid gap-4 sm:grid-cols-2'>
                  <label className='grid gap-2 text-sm font-medium'>
                    {t('tasks.labelKey')}
                    <Input
                      value={form.target.label_key || ''}
                      onChange={(event) =>
                        setTarget({
                          ...(form.target as Target),
                          label_key: event.target.value,
                        })
                      }
                    />
                  </label>
                  <label className='grid gap-2 text-sm font-medium'>
                    {t('tasks.labelValue')}
                    <Input
                      value={form.target.label_value || ''}
                      onChange={(event) =>
                        setTarget({
                          ...(form.target as Target),
                          label_value: event.target.value,
                        })
                      }
                    />
                  </label>
                </div>
              )}
            </div>
            <div className='grid gap-4 sm:grid-cols-2'>
              <label className='grid gap-2 text-sm font-medium'>
                {t('tasks.retry')}
                <Input
                  type='number'
                  min={0}
                  value={form.retry ?? 0}
                  onChange={(event) =>
                    setForm({ ...form, retry: Number(event.target.value) })
                  }
                />
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('tasks.runUser')}
                <Input
                  className='font-mono text-xs'
                  value={form.run_user || ''}
                  onChange={(event) =>
                    setForm({ ...form, run_user: event.target.value })
                  }
                />
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('tasks.offlinePolicy')}
                <Select
                  value={form.offline_policy || 'hub_online_required'}
                  onValueChange={(value) =>
                    setForm({ ...form, offline_policy: value })
                  }
                >
                  <SelectTrigger className='w-full'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='hub_online_required'>
                      {t('tasks.hubOnlineRequired')}
                    </SelectItem>
                    <SelectItem value='allow_offline'>
                      {t('tasks.allowOffline')}
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
          </CardContent>
        </Card>
        <Card className='max-w-4xl'>
          <CardHeader>
            <CardTitle className='text-base'>{t('tasks.parameters')}</CardTitle>
          </CardHeader>
          <CardContent className='grid gap-4'>
            <div className='grid gap-3 sm:grid-cols-[1fr_140px_1fr_auto_auto]'>
              <Input
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
                <SelectTrigger>
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
                      <span className='text-destructive'>{t('tasks.required')}</span>
                    )}
                    <span className='text-muted-foreground'>
                      {item.default || '-'}
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
        <Card className='max-w-4xl'>
          <CardHeader>
            <CardTitle className='text-base'>
              {t('tasks.localCondition')}
            </CardTitle>
          </CardHeader>
          <CardContent className='grid gap-3 sm:grid-cols-[1fr_120px_1fr_auto]'>
            <label className='flex items-center gap-2 text-sm font-medium sm:col-span-4'>
              <Checkbox
                checked={andConditions}
                onCheckedChange={(checked) => setAndConditions(checked === true)}
              />
              {t('tasks.andConditions')}
            </label>
            <Select
              value={localCondition.metric}
              onValueChange={(value) =>
                setLocalCondition({ ...localCondition, metric: value })
              }
            >
              <SelectTrigger>
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
              <SelectTrigger>
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
                placeholder={t('tasks.conditionPath')}
                value={localCondition.path}
                onChange={(event) =>
                  setLocalCondition({ ...localCondition, path: event.target.value })
                }
              />
            )}
            {localCondition.metric === 'command_result' && (
              <Input
                placeholder={t('tasks.conditionCommand')}
                value={localCondition.command}
                onChange={(event) =>
                  setLocalCondition({ ...localCondition, command: event.target.value })
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
        <Card className='max-w-4xl'>
          <CardHeader>
            <CardTitle className='text-base'>
              {t('tasks.remoteCondition')}
            </CardTitle>
          </CardHeader>
          <CardContent className='grid gap-3 sm:grid-cols-[1fr_1fr_100px_1fr_auto]'>
            <Select
              value={remoteCondition.node_id}
              onValueChange={(value) =>
                setRemoteCondition({ ...remoteCondition, node_id: value })
              }
            >
              <SelectTrigger>
                <SelectValue placeholder={t('tasks.selectNode')} />
              </SelectTrigger>
              <SelectContent>
                {(nodes.data || []).map((node) => (
                  <SelectItem key={node.id} value={node.id}>
                    {node.hostname}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select
              value={remoteCondition.property}
              onValueChange={(value) =>
                setRemoteCondition({ ...remoteCondition, property: value })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='online'>online</SelectItem>
                <SelectItem value='last_execution'>
                  {t('tasks.lastExecution')}
                </SelectItem>
              </SelectContent>
            </Select>
            {remoteCondition.property === 'last_execution' && (
              <Input
                className='font-mono text-xs'
                placeholder='task_id'
                value={remoteCondition.task_id}
                onChange={(event) =>
                  setRemoteCondition({
                    ...remoteCondition,
                    task_id: event.target.value,
                  })
                }
              />
            )}
            <Select
              value={remoteCondition.operator}
              onValueChange={(value) =>
                setRemoteCondition({ ...remoteCondition, operator: value })
              }
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value='=='>==</SelectItem>
                <SelectItem value='!='>!=</SelectItem>
              </SelectContent>
            </Select>
            <Input
              placeholder='value'
              value={remoteCondition.value}
              onChange={(event) =>
                setRemoteCondition({
                  ...remoteCondition,
                  value: event.target.value,
                })
              }
            />
            <Button type='button' variant='outline' onClick={setRemote}>
              {t('tasks.setRemote')}
            </Button>
            {form.condition?.remote && (
              <p className='font-mono text-xs text-muted-foreground sm:col-span-5'>
                {nodeNames.get(form.condition.remote.node_id) ||
                  t('common.unknownNode')}
                .{form.condition.remote.property}{' '}
                {form.condition.remote.operator} {form.condition.remote.value}
              </p>
            )}
          </CardContent>
        </Card>
        <div className='flex items-center gap-2'>
          {canWrite ? (
            <Button onClick={save}>{t('common.save')}</Button>
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
  const [running, setRunning] = useState(false)
  const [confirmOpen, setConfirmOpen] = useState(false)
  const [params, setParams] = useState<Record<string, string>>({})
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
  const run = async () => {
    const missing = (task.data?.parameters || []).find(
      (parameter) => parameter.required && !params[parameter.name]
    )
    if (missing) {
      toast.error(`${missing.name} ${t('tasks.required')}`)
      return
    }
    setRunning(true)
    try {
      await api.post(`/tasks/${id}/run`, { params })
      window.location.assign('/executions')
    } catch (error) {
      toast.error(error instanceof Error ? error.message : t('common.noData'))
      setRunning(false)
    }
  }
  return (
    <>
      <CadentraHeader
        title={`${t('tasks.runTitle')}${task.data?.name || id}`}
        description={t('tasks.runDescription')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <Card className='max-w-2xl'>
          <CardHeader>
            <CardTitle className='text-base'>{t('tasks.runNow')}</CardTitle>
          </CardHeader>
          <CardContent className='space-y-4'>
            <p className='text-sm text-muted-foreground'>
              {task.data ? targetText(task.data, t) : t('common.loading')}
            </p>
            {task.data?.parameters?.length ? (
              <div className='grid gap-3'>
                {task.data.parameters.map((parameter) => (
                  <label key={parameter.name} className='grid gap-2 text-sm font-medium'>
                    {parameter.name}
                    <Input
                      type={parameter.type === 'secret' ? 'password' : 'text'}
                      required={parameter.required}
                      value={params[parameter.name] || ''}
                      onChange={(event) =>
                        setParams({ ...params, [parameter.name]: event.target.value })
                      }
                    />
                  </label>
                ))}
              </div>
            ) : null}
            <div className='flex items-center gap-2'>
              {canRun ? (
                <Button
                  onClick={() => setConfirmOpen(true)}
                  disabled={running}
                >
                  {running ? t('tasks.running') : t('tasks.runNow')}
                </Button>
              ) : null}
              <Button asChild variant='outline'>
                <a href={`/tasks/${id}`}>{t('common.cancel')}</a>
              </Button>
            </div>
          </CardContent>
        </Card>
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
  const [deleteOpen, setDeleteOpen] = useState(false)
  return (
    <div className='flex items-center justify-end gap-1'>
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
        <DropdownMenu>
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
        <DropdownMenuContent align='end'>
          <DropdownMenuItem asChild>
            <a href={`/tasks/${task.id}/edit`}>{t('common.edit')}</a>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => toggleTask(task)}>
            {task.enabled ? t('common.disable') : t('common.enable')}
          </DropdownMenuItem>
          <DropdownMenuItem
            variant='destructive'
            onSelect={(event) => {
              event.preventDefault()
              setDeleteOpen(true)
            }}
          >
            {t('common.delete')}
          </DropdownMenuItem>
        </DropdownMenuContent>
        </DropdownMenu>
      ) : null}
      {canWrite ? (
        <ConfirmDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          title={`${t('common.delete')}: ${task.name}`}
          desc={t('tasks.confirmDelete')}
          cancelBtnText={t('common.cancel')}
          confirmText={t('common.delete')}
          destructive
          handleConfirm={() => {
            removeTask(task.id)
            setDeleteOpen(false)
          }}
        />
      ) : null}
    </div>
  )
}
