import { useCallback, useMemo, useRef, useState } from 'react'
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { ColumnDef } from '@tanstack/react-table'
import { Check, Copy, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { useAuthStore } from '@/stores/auth-store'
import {
  api,
  ApiError,
  getToken,
  type Application,
  type Artifact,
  type AuditLog,
  type Group,
  type Node,
  type NodeEnrollment,
  type Schedule,
  type Script,
  type Settings,
  type Task,
  type User,
} from '@/lib/api'
import { copyText, selectText } from '@/lib/clipboard'
import { useCanWrite } from '@/lib/permissions'
import { readTablePageSize } from '@/lib/table-view'
import { useTheme } from '@/context/theme-provider'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Separator } from '@/components/ui/separator'
import { Skeleton } from '@/components/ui/skeleton'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { DataTable } from '@/features/shared/data-table'
import { TaskScheduleTabs } from '@/features/shared/task-schedule-tabs'
import {
  DetailField,
  DetailGrid,
  EmptyState,
  ErrorPage,
  ErrorState,
  DangerMenuItem,
  DropdownMenuItem,
  MoreMenu,
  SectionCard,
  StatusBadge,
  TableCard,
  TimeValue,
} from '@/features/shared/ui'

export function Agents() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const canWrite = useCanWrite()
  const [enrollmentOpen, setEnrollmentOpen] = useState(false)
  const query = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const { mutate: updateNodeStatus } = useMutation({
    mutationFn: ({ node, status }: { node: Node; status: string }) =>
      api.post(`/nodes/${node.id}`, { status }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['nodes'] }),
    onError: (error) => toast.error(error.message),
  })
  const { mutate: revokeNode } = useMutation({
    mutationFn: (node: Node) =>
      api.post(`/nodes/${node.id}/revoke-credential`, {}),
    onSuccess: () => client.invalidateQueries({ queryKey: ['nodes'] }),
    onError: (error) => toast.error(error.message),
  })
  const { mutate: deleteNode } = useMutation({
    mutationFn: (node: Node) => api.del(`/nodes/${node.id}`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['nodes'] }),
    onError: (error) => toast.error(error.message),
  })
  const columns = useMemo<ColumnDef<Node>[]>(
    () => [
      {
        id: 'hostname',
        accessorFn: (row) => `${row.hostname} ${row.id}`,
        header: t('dashboard.hostname'),
        size: 280,
        minSize: 220,
        maxSize: 360,
        cell: ({ row }: { row: { original: Node } }) => (
          <div>
            <a
              href={`/agents/${row.original.id}`}
              className='block truncate font-medium hover:underline'
              title={row.original.hostname}
            >
              {row.original.hostname}
            </a>
          </div>
        ),
      },
      {
        accessorKey: 'status',
        header: t('common.status'),
        size: 104,
        minSize: 96,
        meta: { align: 'center' },
        cell: ({ row }: { row: { original: Node } }) => (
          <StatusBadge status={row.original.status} />
        ),
      },
      {
        accessorKey: 'ip',
        header: t('dashboard.ip'),
        size: 120,
        minSize: 96,
        cell: ({ row }: { row: { original: Node } }) => (
          <span className='font-mono text-xs'>{row.original.ip || '-'}</span>
        ),
      },
      {
        accessorKey: 'agent_version',
        header: t('nodes.agentVersion'),
        size: 110,
        minSize: 96,
      },
      {
        accessorKey: 'arch',
        header: t('nodes.architecture'),
        size: 90,
        minSize: 80,
        meta: { align: 'center' },
        cell: ({ row }: { row: { original: Node } }) => (
          <span className='font-mono text-xs'>{row.original.arch || '-'}</span>
        ),
      },
      {
        accessorKey: 'deployment_mode',
        header: t('nodes.deploymentMode'),
        size: 130,
        minSize: 110,
      },
      {
        accessorKey: 'last_seen',
        header: t('dashboard.lastSeen'),
        size: 180,
        minSize: 160,
        cell: ({ row }: { row: { original: Node } }) => (
          <TimeValue value={row.original.last_seen} />
        ),
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        size: 72,
        minSize: 64,
        meta: { align: 'end' },
        cell: ({ row }: { row: { original: Node } }) =>
          canWrite ? (
            <MoreMenu>
              <DropdownMenuItem
                onSelect={() =>
                  updateNodeStatus({
                    node: row.original,
                    status:
                      row.original.status === 'maintenance' ||
                      row.original.status === 'disabled'
                        ? 'online'
                        : 'maintenance',
                  })
                }
              >
                {row.original.status === 'maintenance' ||
                row.original.status === 'disabled'
                  ? t('nodes.resume')
                  : t('nodes.maintenance')}
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() =>
                  updateNodeStatus({
                    node: row.original,
                    status:
                      row.original.status === 'disabled'
                        ? 'online'
                        : 'disabled',
                  })
                }
              >
                {row.original.status === 'disabled'
                  ? t('nodes.enable')
                  : t('nodes.disable')}
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() =>
                  updateNodeStatus({ node: row.original, status: 'online' })
                }
              >
                {t('nodes.setOnline')}
              </DropdownMenuItem>
              <DropdownMenuItem
                onSelect={() => {
                  if (window.confirm(t('nodes.confirmRevoke')))
                    revokeNode(row.original)
                }}
              >
                {t('nodes.revokeCredential')}
              </DropdownMenuItem>
              <DangerMenuItem
                title={t('nodes.deleteTitle')}
                description={t('nodes.confirmDelete')}
                onSelect={() => deleteNode(row.original)}
              />
            </MoreMenu>
          ) : null,
      },
    ],
    [canWrite, deleteNode, revokeNode, t, updateNodeStatus]
  )
  return (
    <>
      <ListLayout
        title={t('nodes.title')}
        description={t('nodes.description')}
        query={query}
        columns={columns}
        data={query.data || []}
        searchPlaceholder={t('nodes.searchPlaceholder')}
        storageKey='nodes'
        action={
          canWrite ? (
            <Button onClick={() => setEnrollmentOpen(true)}>
              <Plus className='size-4' />
              {t('nodes.addNode')}
            </Button>
          ) : null
        }
      />
      <NodeEnrollmentDialog
        open={enrollmentOpen}
        onOpenChange={setEnrollmentOpen}
      />
    </>
  )
}

function NodeEnrollmentDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [method, setMethod] = useState<
    'native' | 'docker_run' | 'docker_compose'
  >('native')
  const [nodeName, setNodeName] = useState('')
  const [nodeAddress, setNodeAddress] = useState('')
  const [hubAddress, setHubAddress] = useState(
    typeof window === 'undefined' ? '' : window.location.origin
  )
  const [request, setRequest] = useState<{
    nodeName: string
    nodeIP: string
    hubAddress: string
  } | null>(null)
  const [copied, setCopied] = useState(false)
  const commandRef = useRef<HTMLPreElement>(null)
  const enrollment = useMutation({
    mutationFn: (value: NonNullable<typeof request>) =>
      api.post<NodeEnrollment>('/nodes/enrollment', {
        node_name: value.nodeName,
        node_ip: value.nodeIP,
        hub_address: value.hubAddress,
      }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['nodes'] })
    },
  })
  const clearEnrollment = () => {
    setRequest(null)
    setCopied(false)
    enrollment.reset()
  }
  const resetEnrollment = () => {
    clearEnrollment()
    setMethod('native')
    setNodeName('')
    setNodeAddress('')
    setHubAddress(typeof window === 'undefined' ? '' : window.location.origin)
  }
  const command = enrollment.data?.[method] || ''
  const copy = async () => {
    if (!command) return
    try {
      const copiedToClipboard = await copyText(command)
      if (!copiedToClipboard) {
        selectText(commandRef.current)
        toast.info(t('common.copyManual'))
        return
      }
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    } catch {
      selectText(commandRef.current)
      toast.error(t('common.copyFailed'))
    }
  }
  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) resetEnrollment()
        onOpenChange(nextOpen)
      }}
    >
      <DialogContent className='max-w-3xl'>
        <DialogHeader>
          <DialogTitle>{t('nodes.addNode')}</DialogTitle>
          <DialogDescription>
            {t('nodes.enrollmentDescription')}
          </DialogDescription>
        </DialogHeader>
        <form
          className='grid gap-4 sm:grid-cols-2'
          onSubmit={(event) => {
            event.preventDefault()
            setCopied(false)
            const nextRequest = {
              nodeName: nodeName.trim(),
              nodeIP: nodeAddress.trim(),
              hubAddress: hubAddress.trim(),
            }
            setRequest(nextRequest)
            enrollment.mutate(nextRequest)
          }}
        >
          <div className='grid gap-2 text-sm font-medium'>
            <Label htmlFor='enrollment-node-name'>{t('nodes.nodeName')}</Label>
            <Input
              id='enrollment-node-name'
              value={nodeName}
              onChange={(event) => {
                setNodeName(event.target.value)
                clearEnrollment()
              }}
              placeholder='node-01'
              required
            />
          </div>
          <div className='grid gap-2 text-sm font-medium'>
            <Label htmlFor='enrollment-node-ip'>{t('nodes.nodeAddress')}</Label>
            <Input
              id='enrollment-node-ip'
              value={nodeAddress}
              onChange={(event) => {
                setNodeAddress(event.target.value)
                clearEnrollment()
              }}
              placeholder={t('nodes.nodeAddressPlaceholder')}
              required
            />
          </div>
          <div className='grid gap-2 text-sm font-medium sm:col-span-2'>
            <Label htmlFor='enrollment-hub-address'>
              {t('nodes.hubAddress')}
            </Label>
            <Input
              id='enrollment-hub-address'
              type='url'
              value={hubAddress}
              onChange={(event) => {
                setHubAddress(event.target.value)
                clearEnrollment()
              }}
              placeholder='http://hub.example.com:8080'
              required
            />
          </div>
          <div className='flex flex-wrap items-center gap-2 sm:col-span-2'>
            <Button
              type='submit'
              className='w-fit'
              disabled={enrollment.isPending}
            >
              {t('nodes.generateCommand')}
            </Button>
            {enrollment.data ? (
              <Button
                type='button'
                variant='outline'
                className='ml-auto'
                onClick={() => void copy()}
                aria-label={t('common.copy')}
              >
                {enrollment.data && copied ? (
                  <Check className='size-4' />
                ) : (
                  <Copy className='size-4' />
                )}
                {copied ? t('common.copied') : t('common.copy')}
              </Button>
            ) : null}
          </div>
        </form>
        {enrollment.isPending ? (
          <Skeleton className='h-40 w-full' />
        ) : enrollment.isError ? (
          <ErrorState
            error={enrollment.error}
            onRetry={() => {
              if (request) enrollment.mutate(request)
            }}
          />
        ) : enrollment.data ? (
          <div className='space-y-4'>
            <Tabs
              value={method}
              onValueChange={(value) => {
                setMethod(value as typeof method)
                setCopied(false)
              }}
            >
              <TabsList>
                <TabsTrigger value='native'>{t('nodes.native')}</TabsTrigger>
                <TabsTrigger value='docker_run'>docker run</TabsTrigger>
                <TabsTrigger value='docker_compose'>docker compose</TabsTrigger>
              </TabsList>
            </Tabs>
            <div className='space-y-2'>
              <pre
                ref={commandRef}
                className='max-h-80 overflow-auto rounded-md border bg-muted/30 p-4 font-mono text-xs leading-5 break-all whitespace-pre-wrap select-text'
              >
                {command}
              </pre>
            </div>
            <p className='text-xs text-muted-foreground'>
              {t('nodes.enrollmentGateway')}:{' '}
              <span className='font-mono'>{enrollment.data.gateway_url}</span>
            </p>
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

export function AgentDetail() {
  const { t } = useTranslation()
  const id = window.location.pathname.split('/').pop() || ''
  const client = useQueryClient()
  const canWrite = useCanWrite()
  const [labelKey, setLabelKey] = useState('')
  const [labelValue, setLabelValue] = useState('')
  const node = useQuery({
    queryKey: ['node', id],
    queryFn: () => api.get<Node>(`/nodes/${id}`),
  })
  const executions = useQuery({
    queryKey: ['node-executions', id],
    queryFn: () =>
      api.get<ExecutionLike[]>(`/executions?node_id=${id}&limit=100`),
  })
  const tasks = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<Task[]>('/tasks'),
  })
  const groups = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.get<Group[]>('/groups'),
  })
  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: () => api.get<Schedule[]>('/schedules'),
  })
  const applications = useQuery({
    queryKey: ['node-applications', id],
    queryFn: async () => {
      const all = await api.get<Application[]>('/applications')
      const assigned = await Promise.all(
        all.map(async (application) => {
          try {
            const nodeIds = await api.get<string[]>(
              `/applications/${application.id}/nodes`
            )
            return nodeIds.includes(id) ? application : null
          } catch {
            return null
          }
        })
      )
      return assigned.filter((application): application is Application =>
        Boolean(application)
      )
    },
  })
  const updateLabels = useMutation({
    mutationFn: () =>
      api.post(`/nodes/${id}`, {
        labels: { ...(node.data?.labels || {}), [labelKey.trim()]: labelValue },
      }),
    onSuccess: () => {
      setLabelKey('')
      setLabelValue('')
      client.invalidateQueries({ queryKey: ['node', id] })
    },
    onError: (error) => toast.error(error.message),
  })
  const nodeTasks = (tasks.data || []).filter((task) =>
    taskTargetsNode(task, node.data, groups.data || [])
  )
  const nodeSchedules = (schedules.data || []).filter((schedule) =>
    nodeTasks.some((task) => task.id === schedule.task_id)
  )
  if (node.isError && !node.data)
    return (
      <ErrorPage
        title={t('nodes.nodeTitle')}
        error={node.error}
        onRetry={() => node.refetch()}
        backTo='/agents'
      />
    )
  if (!node.data) return <LoadingPage title={t('nodes.nodeTitle')} />
  const item = node.data
  return (
    <>
      <NodeSteerHeader
        title={item.hostname}
        description={item.ip || item.agent_id}
        action={<StatusBadge status={item.status} />}
      />
      <Main className='flex flex-1 flex-col'>
        <div className='flex w-full flex-col gap-6'>
          <Button asChild variant='ghost' className='w-fit px-0'>
            <Link to='/agents'>← {t('common.back')}</Link>
          </Button>
          <SectionCard title={t('nodes.overview')}>
            <div className='space-y-4'>
              <DetailGrid>
                <DetailField label={t('nodes.nodeId')}>
                  <span
                    className='block truncate font-mono text-xs'
                    title={item.id}
                  >
                    {item.id}
                  </span>
                </DetailField>
                <DetailField label={t('nodes.agentId')}>
                  <span
                    className='block truncate font-mono text-xs'
                    title={item.agent_id}
                  >
                    {item.agent_id}
                  </span>
                </DetailField>
                <DetailField label={t('dashboard.ip')}>
                  <span className='font-mono text-xs'>{item.ip || '-'}</span>
                </DetailField>
                <DetailField label={t('dashboard.os')}>
                  {item.os || '-'}
                </DetailField>
                <DetailField label={t('nodes.agentVersion')}>
                  {item.agent_version || '-'}
                </DetailField>
                <DetailField label={t('nodes.architecture')}>
                  <span className='font-mono text-xs'>{item.arch || '-'}</span>
                </DetailField>
                <DetailField label={t('nodes.deploymentMode')}>
                  {item.deployment_mode || '-'}
                </DetailField>
                <DetailField label={t('nodes.globalRevision')}>
                  <span className='font-mono text-xs'>
                    r{item.global_revision}
                  </span>
                </DetailField>
                <DetailField label={t('nodes.syncStatus')}>
                  <StatusBadge status={item.sync_status || 'unknown'} />
                </DetailField>
                <DetailField label={t('dashboard.lastSeen')}>
                  <TimeValue value={item.last_seen} absolute />
                </DetailField>
              </DetailGrid>
              <Separator />
              <section className='space-y-3'>
                <h3 className='text-sm font-medium'>{t('nodes.labels')}</h3>
                <div className='flex min-h-5 flex-wrap gap-x-4 gap-y-1'>
                  {Object.keys(item.labels || {}).length ? (
                    Object.entries(item.labels || {}).map(([key, value]) => (
                      <span
                        className='font-mono text-xs text-muted-foreground'
                        key={key}
                      >
                        {key}={value}
                      </span>
                    ))
                  ) : (
                    <span className='text-sm text-muted-foreground'>
                      {t('nodes.noLabels')}
                    </span>
                  )}
                </div>
                {canWrite ? (
                  <div className='flex flex-wrap gap-2'>
                    <Input
                      className='h-8 w-32 font-mono text-xs'
                      placeholder='key'
                      value={labelKey}
                      onChange={(event) => setLabelKey(event.target.value)}
                    />
                    <Input
                      className='h-8 w-32 font-mono text-xs'
                      placeholder='value'
                      value={labelValue}
                      onChange={(event) => setLabelValue(event.target.value)}
                    />
                    <Button
                      size='sm'
                      variant='outline'
                      onClick={() => updateLabels.mutate()}
                      disabled={!labelKey.trim() || updateLabels.isPending}
                    >
                      {t('nodes.addLabel')}
                    </Button>
                  </div>
                ) : null}
              </section>
            </div>
          </SectionCard>
          <SectionCard title={t('nodes.inventory')}>
            <div className='space-y-4'>
              {item.inventory ? (
                <DetailGrid>
                  <DetailField label={t('dashboard.os')}>
                    {item.inventory.os} {item.inventory.os_version}
                  </DetailField>
                  <DetailField label={t('nodes.kernel')}>
                    <span
                      className='block truncate font-mono text-xs'
                      title={item.inventory.kernel}
                    >
                      {item.inventory.kernel || '-'}
                    </span>
                  </DetailField>
                  <DetailField label={t('nodes.cpu')}>
                    {item.inventory.cpu
                      ?.map((cpu) => `${cpu.model} (${cpu.cores})`)
                      .join(', ') || '-'}
                  </DetailField>
                  <DetailField label={t('nodes.memory')}>
                    {item.inventory.memory
                      ? `${Math.round(item.inventory.memory.total_kb / 1024)} MB`
                      : '-'}
                  </DetailField>
                  <DetailField label={t('nodes.filesystems')}>
                    {item.inventory.filesystem?.length ? (
                      <ul className='space-y-1 font-mono text-xs'>
                        {item.inventory.filesystem.map((filesystem) => (
                          <li key={filesystem.mount} className='break-words'>
                            {filesystem.mount} ({filesystem.fs_type},{' '}
                            {Math.round(filesystem.free_kb / 1024)}/
                            {Math.round(filesystem.total_kb / 1024)} MB)
                          </li>
                        ))}
                      </ul>
                    ) : (
                      '-'
                    )}
                  </DetailField>
                  <DetailField label={t('nodes.network')}>
                    <span className='font-mono text-xs'>
                      {item.inventory.network
                        ?.map(
                          (network) =>
                            `${network.interface}: ${network.addresses.join(', ')}`
                        )
                        .join('; ') || '-'}
                    </span>
                  </DetailField>
                </DetailGrid>
              ) : (
                <EmptyState message={t('nodes.noInventory')} />
              )}
              <Separator />
              <section className='space-y-3'>
                <h3 className='text-sm font-medium'>
                  {t('nodes.capabilities')}
                </h3>
                <div className='grid gap-2 sm:grid-cols-2'>
                  {Object.entries(item.capabilities || {}).map(
                    ([key, value]) => (
                      <span
                        className='flex min-w-0 items-center gap-2 text-sm'
                        key={key}
                      >
                        <StatusBadge status={value ? 'enabled' : 'disabled'} />
                        <span
                          className='truncate font-mono text-xs'
                          title={key}
                        >
                          {key}
                        </span>
                      </span>
                    )
                  )}
                </div>
              </section>
            </div>
          </SectionCard>
          <SectionCard title={t('nav.executions')}>
            <ExecutionRows
              executions={executions.data || []}
              tasks={tasks.data || []}
            />
          </SectionCard>
          <SectionCard title={t('nodes.tasks')}>
            {nodeTasks.length ? (
              <div className='divide-y'>
                {nodeTasks.map((task) => (
                  <div
                    className='flex min-w-0 items-center gap-3 py-3'
                    key={task.id}
                  >
                    <a
                      className='min-w-0 truncate font-medium hover:underline'
                      href={`/tasks/${task.id}`}
                      title={task.name}
                    >
                      {task.name}
                    </a>
                    <span
                      className='max-w-full truncate font-mono text-xs text-muted-foreground'
                      title={task.type}
                    >
                      {t(`tasks.types.${task.type}`, {
                        defaultValue: task.type,
                      })}
                    </span>
                    <StatusBadge
                      status={task.enabled ? 'enabled' : 'disabled'}
                    />
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState message={t('nodes.noTasks')} />
            )}
          </SectionCard>
          <SectionCard title={t('nodes.schedules')}>
            {nodeSchedules.length ? (
              <div className='divide-y'>
                {nodeSchedules.map((schedule) => (
                  <div
                    className='flex min-w-0 items-center gap-3 py-3'
                    key={schedule.id}
                  >
                    <a
                      className='min-w-0 truncate font-medium hover:underline'
                      href={`/schedules/${schedule.id}`}
                    >
                      {tasks.data?.find((task) => task.id === schedule.task_id)
                        ?.name || t('common.unknownTask')}
                    </a>
                    <span
                      className='max-w-full truncate font-mono text-xs text-muted-foreground'
                      title={schedule.expression || `${schedule.interval_sec}s`}
                    >
                      {schedule.expression || `${schedule.interval_sec}s`}
                    </span>
                    <StatusBadge
                      status={schedule.enabled ? 'enabled' : 'disabled'}
                    />
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState message={t('nodes.noSchedules')} />
            )}
          </SectionCard>
          <SectionCard title={t('apps.title')}>
            {applications.data?.length ? (
              <div className='divide-y'>
                {applications.data.map((application) => (
                  <div
                    className='flex min-w-0 items-center gap-3 py-3'
                    key={application.id}
                  >
                    <a
                      className='min-w-0 truncate font-medium hover:underline'
                      href={`/applications/${application.id}`}
                      title={application.name}
                    >
                      {application.name}
                    </a>
                    <span className='font-mono text-xs text-muted-foreground'>
                      {application.version}
                    </span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState message={t('apps.noApps')} />
            )}
          </SectionCard>
        </div>
      </Main>
    </>
  )
}

type ExecutionLike = {
  id: string
  task_id: string
  node_id: string
  status: string
  trigger_type: string
  start_time: string
  end_time: string
  exit_code: number
}

function ExecutionRows({
  executions,
  tasks,
}: {
  executions: ExecutionLike[]
  tasks: Task[]
}) {
  const { t } = useTranslation()
  const taskNames = new Map(tasks.map((task) => [task.id, task.name]))
  if (!executions.length)
    return <EmptyState message={t('executions.noExecs')} />
  return (
    <TableCard>
      <Table className='min-w-[620px] table-fixed'>
        <colgroup>
          <col className='w-[25%]' />
          <col className='w-[25%]' />
          <col className='w-[20%]' />
          <col className='w-[20%]' />
          <col className='w-[10%]' />
        </colgroup>
        <TableHeader>
          <TableRow>
            <TableHead>{t('dashboard.task')}</TableHead>
            <TableHead className='text-center'>{t('common.status')}</TableHead>
            <TableHead>{t('executions.start')}</TableHead>
            <TableHead>{t('executions.duration')}</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {executions.map((execution) => (
            <TableRow key={execution.id}>
              <TableCell>
                {taskNames.get(execution.task_id) ? (
                  <a
                    href={`/tasks/${execution.task_id}`}
                    className='font-medium hover:underline'
                  >
                    {taskNames.get(execution.task_id)}
                  </a>
                ) : (
                  <span className='font-medium'>{t('common.unknownTask')}</span>
                )}
              </TableCell>
              <TableCell className='text-center'>
                <StatusBadge status={execution.status} />
              </TableCell>
              <TableCell>
                <TimeValue value={execution.start_time} />
              </TableCell>
              <TableCell>
                {execution.end_time
                  ? new Date(
                      new Date(execution.end_time).getTime() -
                        new Date(execution.start_time).getTime()
                    )
                      .toISOString()
                      .slice(11, 19)
                  : '-'}
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

function taskTargetsNode(task: Task, node: Node | undefined, groups: Group[]) {
  if (!node) return false
  if (task.target.type === 'node')
    return task.target.node_ids?.includes(node.id) || false
  if (task.target.type === 'label')
    return (
      node.labels?.[task.target.label_key || ''] === task.target.label_value
    )
  return (task.target.group_ids || []).some((groupId) => {
    const group = groups.find((item) => item.id === groupId)
    if (!group) return false
    return group.type === 'label'
      ? node.labels?.[group.label_key] === group.label_value
      : group.members?.includes(node.id) || false
  })
}

export function Schedules() {
  const { t, i18n } = useTranslation()
  const client = useQueryClient()
  const canWrite = useCanWrite()
  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: () => api.get<Schedule[]>('/schedules'),
  })
  const tasks = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<Task[]>('/tasks'),
  })
  const { mutate: toggleSchedule } = useMutation({
    mutationFn: (schedule: Schedule) =>
      api.put(`/schedules/${schedule.id}`, {
        ...schedule,
        enabled: !schedule.enabled,
      }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['schedules'] }),
    onError: (error) => toast.error(error.message),
  })
  const { mutate: removeSchedule } = useMutation({
    mutationFn: (schedule: Schedule) => api.del(`/schedules/${schedule.id}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['schedules'] })
      toast.success(t('common.deleted'))
    },
    onError: (error) => toast.error(error.message),
  })
  const columns = useMemo<ColumnDef<Schedule>[]>(
    () => [
      {
        id: 'task',
        accessorFn: (row: Schedule) =>
          `${tasks.data?.find((task) => task.id === row.task_id)?.name || ''} ${row.task_id}`,
        header: t('schedules.task'),
        size: 260,
        minSize: 220,
        maxSize: 360,
        cell: ({ row }: { row: { original: Schedule } }) => (
          <a
            href={`/schedules/${row.original.id}`}
            className='block truncate font-medium hover:underline'
            title={
              tasks.data?.find((task) => task.id === row.original.task_id)
                ?.name || t('common.unknownTask')
            }
          >
            {tasks.data?.find((task) => task.id === row.original.task_id)
              ?.name || t('common.unknownTask')}
          </a>
        ),
      },
      {
        accessorKey: 'type',
        header: t('common.type'),
        size: 100,
        minSize: 88,
        cell: ({ row }: { row: { original: Schedule } }) =>
          t(`schedules.types.${row.original.type}`, {
            defaultValue: row.original.type,
          }),
      },
      {
        accessorKey: 'expression',
        header: t('tasks.expression'),
        size: 160,
        minSize: 140,
        cell: ({ row }: { row: { original: Schedule } }) => (
          <span className='font-mono text-xs'>
            {row.original.type === 'interval'
              ? `${i18n.language === 'en' ? 'Every ' : '每 '}${row.original.interval_sec}${i18n.language === 'en' ? 's' : ' 秒'}`
              : row.original.expression || '-'}
          </span>
        ),
      },
      {
        accessorKey: 'timezone',
        header: t('schedules.timezone'),
        size: 120,
        minSize: 100,
      },
      {
        accessorKey: 'execution_owner',
        header: t('schedules.executionOwner'),
        size: 110,
        minSize: 96,
        cell: ({ row }: { row: { original: Schedule } }) =>
          t(`schedules.owners.${row.original.execution_owner}`, {
            defaultValue: row.original.execution_owner,
          }),
      },
      {
        accessorKey: 'offline_policy',
        header: t('schedules.offline'),
        size: 150,
        minSize: 130,
        cell: ({ row }: { row: { original: Schedule } }) =>
          t(`schedules.offlinePolicies.${row.original.offline_policy}`, {
            defaultValue: row.original.offline_policy,
          }),
      },
      {
        accessorKey: 'misfire_policy',
        header: t('tasks.misfire'),
        size: 140,
        minSize: 120,
        cell: ({ row }: { row: { original: Schedule } }) =>
          t(`schedules.misfirePolicies.${row.original.misfire_policy}`, {
            defaultValue: row.original.misfire_policy,
          }),
      },
      {
        accessorKey: 'enabled',
        header: t('common.status'),
        size: 104,
        minSize: 96,
        meta: { align: 'center' },
        cell: ({ row }: { row: { original: Schedule } }) => (
          <StatusBadge status={row.original.enabled ? 'enabled' : 'disabled'} />
        ),
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        size: 72,
        minSize: 64,
        meta: { align: 'end' },
        cell: ({ row }: { row: { original: Schedule } }) => {
          const schedule = row.original
          const taskName =
            tasks.data?.find((task) => task.id === schedule.task_id)?.name ||
            t('common.unknownTask')
          const expression =
            schedule.type === 'interval'
              ? `${i18n.language === 'en' ? 'Every ' : '每 '}${schedule.interval_sec}${i18n.language === 'en' ? 's' : ' 秒'}`
              : schedule.expression || '-'
          const scheduleLabel = `${taskName} · ${expression}`
          return canWrite ? (
            <MoreMenu>
              <DropdownMenuItem asChild>
                <a href={`/schedules/${schedule.id}`}>{t('common.edit')}</a>
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => toggleSchedule(schedule)}>
                {schedule.enabled ? t('common.disable') : t('common.enable')}
              </DropdownMenuItem>
              <DangerMenuItem
                onSelect={() => removeSchedule(schedule)}
                title={`${t('common.delete')}: ${scheduleLabel}`}
                description={t('schedules.confirmDelete', {
                  name: scheduleLabel,
                })}
              />
            </MoreMenu>
          ) : null
        },
      },
    ],
    [canWrite, i18n.language, t, tasks.data, toggleSchedule, removeSchedule]
  )
  return (
    <ListLayout
      title={t('schedules.title')}
      description={t('schedules.description')}
      query={schedules}
      columns={columns}
      data={schedules.data || []}
      searchPlaceholder={t('schedules.searchPlaceholder')}
      storageKey='schedules'
      extra={<TaskScheduleTabs value='schedules' />}
      action={
        canWrite ? (
          <Button asChild>
            <Link to='/schedules/new'>
              <Plus className='me-1 size-4' />
              {t('schedules.newSchedule')}
            </Link>
          </Button>
        ) : null
      }
    />
  )
}

export function Scripts() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const canWrite = useCanWrite()
  const query = useQuery({
    queryKey: ['scripts'],
    queryFn: () => api.get<Script[]>('/scripts'),
  })
  const { mutate: toggleScript } = useMutation({
    mutationFn: (script: Script) =>
      api.put(`/scripts/${script.id}`, { ...script, enabled: !script.enabled }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['scripts'] }),
    onError: (error) => toast.error(error.message),
  })
  const { mutate: removeScript } = useMutation({
    mutationFn: (script: Script) => api.del(`/scripts/${script.id}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['scripts'] })
      toast.success(t('common.deleted'))
    },
    onError: (error) => toast.error(error.message),
  })
  const { mutate: cloneScript } = useMutation({
    mutationFn: (script: Script) => {
      const {
        id: _id,
        revision: _revision,
        created_at: _created,
        updated_at: _updated,
        ...copy
      } = script
      return api.post<Script>('/scripts', {
        ...copy,
        name: `${script.name} (copy)`,
      })
    },
    onSuccess: (script) => {
      client.invalidateQueries({ queryKey: ['scripts'] })
      window.location.assign(`/scripts/${script.id}`)
    },
    onError: (error) => toast.error(error.message),
  })
  const columns = useMemo<ColumnDef<Script>[]>(
    () => [
      {
        id: 'name',
        accessorFn: (row) => `${row.name} ${row.description || ''} ${row.id}`,
        header: t('common.name'),
        size: 280,
        minSize: 220,
        maxSize: 360,
        cell: ({ row }: { row: { original: Script } }) => (
          <div>
            <a
              href={`/scripts/${row.original.id}`}
              className='font-medium hover:underline'
            >
              {row.original.name}
            </a>
            {row.original.description ? (
              <span className='block truncate text-xs text-muted-foreground'>
                {row.original.description}
              </span>
            ) : null}
          </div>
        ),
      },
      {
        accessorKey: 'interpreter',
        header: t('scripts.interpreter'),
        size: 100,
        minSize: 88,
      },
      {
        accessorKey: 'revision',
        header: t('tasks.revision'),
        size: 96,
        minSize: 88,
        meta: { align: 'center' },
        cell: ({ row }: { row: { original: Script } }) => (
          <span className='font-mono text-xs'>r{row.original.revision}</span>
        ),
      },
      {
        accessorKey: 'timeout',
        header: t('scripts.timeout'),
        size: 90,
        minSize: 80,
        meta: { align: 'end' },
        cell: ({ row }: { row: { original: Script } }) =>
          `${row.original.timeout}s`,
      },
      {
        accessorKey: 'sha256',
        header: t('scripts.sha256'),
        size: 190,
        minSize: 160,
        cell: ({ row }: { row: { original: Script } }) => (
          <span className='font-mono text-xs'>
            {row.original.sha256.slice(0, 16)}…
          </span>
        ),
      },
      {
        accessorKey: 'updated_at',
        header: t('scripts.updated'),
        size: 180,
        minSize: 160,
        cell: ({ row }: { row: { original: Script } }) => (
          <TimeValue value={row.original.updated_at} />
        ),
      },
      {
        id: 'status',
        accessorFn: (row: Script) => (row.enabled ? 'enabled' : 'disabled'),
        header: t('common.status'),
        size: 104,
        minSize: 96,
        meta: { align: 'center' },
        cell: ({ row }: { row: { original: Script } }) => (
          <StatusBadge status={row.original.enabled ? 'enabled' : 'disabled'} />
        ),
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        size: 72,
        minSize: 64,
        meta: { align: 'end' },
        cell: ({ row }: { row: { original: Script } }) =>
          canWrite ? (
            <MoreMenu>
              <DropdownMenuItem asChild>
                <a href={`/scripts/${row.original.id}`}>{t('common.edit')}</a>
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => toggleScript(row.original)}>
                {row.original.enabled
                  ? t('common.disable')
                  : t('common.enable')}
              </DropdownMenuItem>
              <DropdownMenuItem onSelect={() => cloneScript(row.original)}>
                {t('scripts.clone')}
              </DropdownMenuItem>
              <DangerMenuItem
                onSelect={() => removeScript(row.original)}
                title={`${t('common.delete')}: ${row.original.name}`}
                description={t('scripts.confirmDelete')}
              />
            </MoreMenu>
          ) : null,
      },
    ],
    [canWrite, cloneScript, removeScript, t, toggleScript]
  )
  return (
    <ListLayout
      title={t('scripts.title')}
      description={t('scripts.description')}
      query={query}
      columns={columns}
      data={query.data || []}
      searchPlaceholder={t('scripts.searchPlaceholder')}
      storageKey='scripts'
      action={
        canWrite ? (
          <Button asChild>
            <Link to='/scripts/new'>
              <Plus className='me-1 size-4' />
              {t('scripts.newScript')}
            </Link>
          </Button>
        ) : null
      }
    />
  )
}

export function Groups() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const canWrite = useCanWrite()
  const query = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.get<Group[]>('/groups'),
  })
  const nodes = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const { mutate: removeGroup } = useMutation({
    mutationFn: (group: Group) => api.del(`/groups/${group.id}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['groups'] })
      toast.success(t('common.deleted'))
    },
    onError: (error) => toast.error(error.message),
  })
  const columns = useMemo<ColumnDef<Group>[]>(
    () => [
      {
        accessorKey: 'name',
        header: t('common.name'),
        size: 240,
        minSize: 180,
        maxSize: 320,
        cell: ({ row }: { row: { original: Group } }) => (
          <span className='font-medium'>{row.original.name}</span>
        ),
      },
      {
        accessorKey: 'type',
        header: t('common.type'),
        size: 110,
        minSize: 96,
        cell: ({ row }: { row: { original: Group } }) =>
          t(`groups.types.${row.original.type}`, {
            defaultValue: row.original.type,
          }),
      },
      {
        accessorKey: 'description',
        header: t('common.description'),
        size: 360,
        minSize: 220,
      },
      {
        id: 'members',
        accessorFn: (row: Group) =>
          row.type === 'label'
            ? (nodes.data || []).filter(
                (node) => node.labels?.[row.label_key] === row.label_value
              ).length
            : row.members?.length || 0,
        header: t('groups.members'),
        size: 90,
        minSize: 72,
        meta: { align: 'end' },
        cell: ({ row }: { row: { original: Group } }) =>
          row.original.type === 'label'
            ? (nodes.data || []).filter(
                (node) =>
                  node.labels?.[row.original.label_key] ===
                  row.original.label_value
              ).length
            : row.original.members?.length || 0,
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        size: 72,
        minSize: 64,
        meta: { align: 'end' },
        cell: ({ row }: { row: { original: Group } }) =>
          canWrite ? (
            <MoreMenu>
              <DropdownMenuItem asChild>
                <a href={`/groups/${row.original.id}`}>{t('common.edit')}</a>
              </DropdownMenuItem>
              <DangerMenuItem
                onSelect={() => removeGroup(row.original)}
                title={`${t('common.delete')}: ${row.original.name}`}
                description={t('groups.confirmDelete')}
              />
            </MoreMenu>
          ) : null,
      },
    ],
    [canWrite, nodes.data, removeGroup, t]
  )
  return (
    <ListLayout
      title={t('groups.title')}
      description={t('groups.description')}
      query={query}
      columns={columns}
      data={query.data || []}
      searchPlaceholder={t('groups.searchPlaceholder')}
      storageKey='groups'
      action={
        canWrite ? (
          <Button asChild>
            <Link to='/groups/new'>
              <Plus className='me-1 size-4' />
              {t('groups.newGroup')}
            </Link>
          </Button>
        ) : null
      }
    />
  )
}

export function Applications() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const canWrite = useCanWrite()
  const [deleteError, setDeleteError] = useState('')
  const query = useQuery({
    queryKey: ['applications'],
    queryFn: () => api.get<Application[]>('/applications'),
  })
  const { mutate: removeApplication } = useMutation({
    mutationFn: (application: Application) =>
      api.del(`/applications/${application.id}`),
    onMutate: () => setDeleteError(''),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['applications'] })
      toast.success(t('common.deleted'))
    },
    onError: (error) => {
      const message =
        error instanceof ApiError && error.status === 409
          ? t('apps.deleteBlocked')
          : error instanceof Error
            ? error.message
            : t('common.noData')
      setDeleteError(message)
    },
  })
  const columns = useMemo<ColumnDef<Application>[]>(
    () => [
      {
        accessorKey: 'name',
        header: t('common.name'),
        size: 220,
        minSize: 180,
        maxSize: 300,
        cell: ({ row }: { row: { original: Application } }) => (
          <a
            href={`/applications/${row.original.id}`}
            className='font-medium hover:underline'
          >
            {row.original.name}
          </a>
        ),
      },
      {
        accessorKey: 'version',
        header: t('common.version'),
        size: 96,
        minSize: 80,
      },
      {
        accessorKey: 'binary_path',
        header: t('apps.binary'),
        size: 300,
        minSize: 220,
        maxSize: 460,
        cell: ({ row }: { row: { original: Application } }) => (
          <span
            className='block truncate font-mono text-xs'
            title={row.original.binary_path}
          >
            {row.original.binary_path}
          </span>
        ),
      },
      {
        accessorKey: 'unit_name',
        header: t('apps.unit'),
        size: 260,
        minSize: 180,
        maxSize: 380,
        cell: ({ row }: { row: { original: Application } }) => (
          <span
            className='block truncate font-mono text-xs'
            title={row.original.unit_name || '-'}
          >
            {row.original.unit_name || '-'}
          </span>
        ),
      },
      {
        accessorKey: 'revision',
        header: t('tasks.revision'),
        size: 96,
        minSize: 88,
        meta: { align: 'center' },
        cell: ({ row }: { row: { original: Application } }) => (
          <span className='font-mono text-xs'>r{row.original.revision}</span>
        ),
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        size: 72,
        minSize: 64,
        meta: { align: 'end' },
        cell: ({ row }: { row: { original: Application } }) =>
          canWrite ? (
            <MoreMenu>
              <DropdownMenuItem asChild>
                <a href={`/applications/${row.original.id}`}>
                  {t('common.edit')}
                </a>
              </DropdownMenuItem>
              <DangerMenuItem
                onSelect={() => removeApplication(row.original)}
                title={`${t('common.delete')}: ${row.original.name}`}
                description={t('apps.confirmDelete')}
              />
            </MoreMenu>
          ) : null,
      },
    ],
    [canWrite, removeApplication, t]
  )
  return (
    <ListLayout
      title={t('apps.title')}
      description={t('apps.description')}
      query={query}
      columns={columns}
      data={query.data || []}
      searchPlaceholder={t('apps.searchPlaceholder')}
      storageKey='applications'
      extra={deleteError ? <ErrorState error={new Error(deleteError)} /> : null}
      action={
        canWrite ? (
          <Button asChild>
            <Link to='/applications/new'>
              <Plus className='me-1 size-4' />
              {t('apps.newApp')}
            </Link>
          </Button>
        ) : null
      }
    />
  )
}

export function Artifacts() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const canWrite = useCanWrite()
  const query = useQuery({
    queryKey: ['artifacts'],
    queryFn: () => api.get<Artifact[]>('/artifacts'),
  })
  const applications = useQuery({
    queryKey: ['applications'],
    queryFn: () => api.get<Application[]>('/applications'),
  })
  const { mutate: removeArtifact } = useMutation({
    mutationFn: (artifact: Artifact) => api.del(`/artifacts/${artifact.id}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['artifacts'] })
      toast.success(t('common.deleted'))
    },
    onError: (error) => toast.error(error.message),
  })
  const downloadArtifact = useCallback(
    async (artifact: Artifact) => {
      try {
        const response = await fetch(`/api/artifacts/${artifact.id}/download`, {
          headers: { Authorization: `Bearer ${getToken() || ''}` },
        })
        if (!response.ok)
          throw new Error(`${t('artifacts.download')}: HTTP ${response.status}`)
        const url = URL.createObjectURL(await response.blob())
        const link = document.createElement('a')
        link.href = url
        link.download = artifact.filename
        link.click()
        URL.revokeObjectURL(url)
      } catch (error) {
        toast.error(
          error instanceof Error ? error.message : t('artifacts.download')
        )
      }
    },
    [t]
  )
  const columns = useMemo<ColumnDef<Artifact>[]>(
    () => [
      {
        accessorKey: 'name',
        header: t('common.name'),
        size: 200,
        minSize: 160,
        maxSize: 280,
        cell: ({ row }: { row: { original: Artifact } }) => (
          <span
            className='block truncate font-medium'
            title={row.original.name}
          >
            {row.original.name}
          </span>
        ),
      },
      {
        accessorKey: 'version',
        header: t('common.version'),
        size: 100,
        minSize: 84,
      },
      {
        accessorKey: 'architecture',
        header: t('artifacts.archField'),
        size: 100,
        minSize: 84,
      },
      {
        accessorKey: 'filename',
        header: t('artifacts.filename'),
        size: 220,
        minSize: 160,
        maxSize: 320,
        cell: ({ row }: { row: { original: Artifact } }) => (
          <span
            className='block truncate font-mono text-xs'
            title={row.original.filename}
          >
            {row.original.filename}
          </span>
        ),
      },
      {
        accessorKey: 'size',
        header: t('artifacts.size'),
        size: 90,
        minSize: 80,
        meta: { align: 'end' },
        cell: ({ row }: { row: { original: Artifact } }) =>
          `${Math.round(row.original.size / 1024)} KB`,
      },
      {
        accessorKey: 'sha256',
        header: 'SHA256',
        size: 220,
        minSize: 180,
        cell: ({ row }: { row: { original: Artifact } }) => (
          <span
            className='block truncate font-mono text-xs'
            title={row.original.sha256}
          >
            {row.original.sha256.slice(0, 16)}…
          </span>
        ),
      },
      {
        id: 'usage',
        accessorFn: (row: Artifact) =>
          applications.data?.filter(
            (application) => application.artifact_id === row.id
          ).length || 0,
        header: t('artifacts.usage'),
        size: 220,
        minSize: 160,
        cell: ({ row }: { row: { original: Artifact } }) => {
          const used =
            applications.data?.filter(
              (application) => application.artifact_id === row.original.id
            ) || []
          return used.length ? (
            <div className='flex min-w-0 flex-wrap gap-x-2 gap-y-1'>
              {used.map((application) => (
                <a
                  className='max-w-full truncate hover:underline'
                  href={`/applications/${application.id}`}
                  key={application.id}
                  title={application.name}
                >
                  {application.name}
                </a>
              ))}
            </div>
          ) : (
            '-'
          )
        },
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        size: 72,
        minSize: 64,
        meta: { align: 'end' },
        cell: ({ row }: { row: { original: Artifact } }) => (
          <MoreMenu>
            <DropdownMenuItem
              onSelect={(event) => {
                event.preventDefault()
                void downloadArtifact(row.original)
              }}
            >
              {t('artifacts.download')}
            </DropdownMenuItem>
            {canWrite ? (
              <DropdownMenuItem
                onSelect={() =>
                  window.location.assign(
                    `/applications/new?artifact_id=${row.original.id}`
                  )
                }
              >
                {t('artifacts.createApplication')}
              </DropdownMenuItem>
            ) : null}
            {canWrite ? (
              <DangerMenuItem
                onSelect={() => removeArtifact(row.original)}
                title={`${t('common.delete')}: ${row.original.name}`}
                description={t('artifacts.confirmDelete')}
              />
            ) : null}
          </MoreMenu>
        ),
      },
    ],
    [applications.data, canWrite, downloadArtifact, removeArtifact, t]
  )
  return (
    <ListLayout
      title={t('artifacts.title')}
      description={t('artifacts.description')}
      query={query}
      columns={columns}
      data={query.data || []}
      searchPlaceholder={t('artifacts.searchPlaceholder')}
      storageKey='artifacts'
      action={
        canWrite ? (
          <Button asChild>
            <Link to='/artifacts/new'>
              <Plus className='me-1 size-4' />
              {t('common.upload')}
            </Link>
          </Button>
        ) : null
      }
    />
  )
}

export function Audit() {
  const { t } = useTranslation()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(() => readTablePageSize('audit', 10))
  const query = useQuery({
    queryKey: ['audit', page, pageSize],
    queryFn: () =>
      api.getPage<AuditLog>(
        `/audit?limit=${pageSize}&offset=${(page - 1) * pageSize}`
      ),
    placeholderData: keepPreviousData,
  })
  const columns = useMemo<ColumnDef<AuditLog>[]>(
    () => [
      {
        accessorKey: 'created_at',
        header: t('common.time'),
        size: 180,
        minSize: 160,
        cell: ({ row }: { row: { original: AuditLog } }) => (
          <TimeValue value={row.original.created_at} />
        ),
      },
      {
        accessorKey: 'username',
        header: t('common.name'),
        size: 140,
        minSize: 110,
      },
      {
        accessorKey: 'action',
        header: t('common.actions'),
        size: 150,
        minSize: 120,
      },
      {
        accessorKey: 'resource',
        header: t('common.type'),
        size: 130,
        minSize: 100,
      },
      {
        accessorKey: 'resource_id',
        header: t('common.resourceId'),
        size: 220,
        minSize: 140,
        cell: ({ row }: { row: { original: AuditLog } }) => (
          <span
            className='block truncate font-mono text-xs'
            title={row.original.resource_id}
          >
            {row.original.resource_id || '-'}
          </span>
        ),
      },
      {
        accessorKey: 'detail',
        header: t('common.description'),
        size: 420,
        minSize: 240,
      },
    ],
    [t]
  )
  return (
    <ListLayout
      title={t('misc.auditTitle')}
      description={t('misc.auditDescription')}
      query={query}
      columns={columns}
      data={query.data?.items || []}
      searchPlaceholder={t('misc.auditSearch')}
      storageKey='audit'
      pagination={{
        total: query.data?.total ?? 0,
        page,
        pageSize,
        onPageChange: setPage,
        onPageSizeChange: setPageSize,
      }}
    />
  )
}

export function Users() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const isAdministrator = useAuthStore(
    (state) => state.auth.user?.role.includes('administrator') ?? false
  )
  const [showForm, setShowForm] = useState(false)
  const [form, setForm] = useState({
    username: '',
    password: '',
    role: 'viewer',
  })
  const query = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<User[]>('/users'),
    enabled: isAdministrator,
  })
  const create = useMutation({
    mutationFn: () => api.post<User>('/users', form),
    onSuccess: () => {
      setForm({ username: '', password: '', role: 'viewer' })
      setShowForm(false)
      client.invalidateQueries({ queryKey: ['users'] })
      toast.success(t('common.created'))
    },
    onError: (error) => toast.error(error.message),
  })
  const { mutate: setUserRole } = useMutation({
    mutationFn: ({ user, role }: { user: User; role: string }) =>
      api.put(`/users/${user.id}`, { role }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['users'] }),
    onError: (error) => toast.error(error.message),
  })
  const columns = useMemo<ColumnDef<User>[]>(
    () => [
      {
        accessorKey: 'username',
        header: t('common.name'),
        size: 360,
        minSize: 180,
        maxSize: 320,
        cell: ({ row }: { row: { original: User } }) => (
          <span className='font-medium'>{row.original.username}</span>
        ),
      },
      {
        accessorKey: 'role',
        header: t('misc.role'),
        size: 160,
        minSize: 160,
        meta: { align: 'center' },
        cell: ({ row }: { row: { original: User } }) => (
          <Select
            value={row.original.role}
            onValueChange={(role) => setUserRole({ user: row.original, role })}
          >
            <SelectTrigger className='h-8 w-36'>
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='administrator'>
                {t('misc.administrator')}
              </SelectItem>
              <SelectItem value='operator'>{t('misc.operator')}</SelectItem>
              <SelectItem value='viewer'>{t('misc.viewer')}</SelectItem>
            </SelectContent>
          </Select>
        ),
      },
      {
        accessorKey: 'created_at',
        header: t('common.time'),
        size: 180,
        minSize: 160,
        cell: ({ row }: { row: { original: User } }) => (
          <TimeValue value={row.original.created_at} />
        ),
      },
    ],
    [setUserRole, t]
  )
  if (!isAdministrator) {
    return (
      <>
        <NodeSteerHeader
          title={t('misc.usersTitle')}
          description={t('misc.usersDescription')}
        />
        <Main>
          <ErrorState error={new Error(t('misc.adminOnly'))} />
        </Main>
      </>
    )
  }
  return (
    <ListLayout
      title={t('misc.usersTitle')}
      description={t('misc.usersDescription')}
      query={query}
      columns={columns}
      data={query.data || []}
      searchPlaceholder={t('misc.usersSearch')}
      storageKey='users'
      action={
        <Button onClick={() => setShowForm((value) => !value)}>
          {showForm ? t('common.cancel') : t('misc.newUser')}
        </Button>
      }
      extra={
        showForm ? (
          <Card>
            <CardContent className='grid gap-4 pt-6 sm:grid-cols-3'>
              <label className='grid gap-2 text-sm font-medium'>
                {t('misc.username')}
                <Input
                  value={form.username}
                  onChange={(event) =>
                    setForm({ ...form, username: event.target.value })
                  }
                />
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('misc.password')}
                <Input
                  type='password'
                  value={form.password}
                  onChange={(event) =>
                    setForm({ ...form, password: event.target.value })
                  }
                />
              </label>
              <label className='grid gap-2 text-sm font-medium'>
                {t('misc.role')}
                <Select
                  value={form.role}
                  onValueChange={(role) => setForm({ ...form, role })}
                >
                  <SelectTrigger>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='administrator'>
                      {t('misc.administrator')}
                    </SelectItem>
                    <SelectItem value='operator'>
                      {t('misc.operator')}
                    </SelectItem>
                    <SelectItem value='viewer'>{t('misc.viewer')}</SelectItem>
                  </SelectContent>
                </Select>
              </label>
              <Button
                className='w-fit'
                onClick={() => create.mutate()}
                disabled={!form.username || !form.password || create.isPending}
              >
                {t('common.create')}
              </Button>
            </CardContent>
          </Card>
        ) : null
      }
    />
  )
}

export function Settings() {
  const { t } = useTranslation()
  const { theme, setTheme } = useTheme()
  const canWrite = useCanWrite()
  const [section, setSection] = useState<'runtime' | 'appearance'>('runtime')
  const query = useQuery({
    queryKey: ['settings'],
    queryFn: () => api.get<Settings>('/settings'),
  })
  const [values, setValues] = useState<Settings>({})
  const client = useQueryClient()
  const save = useMutation({
    mutationFn: () => api.put('/settings', values),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['settings'] })
      toast.success(t('misc.saved'))
    },
    onError: (error) => toast.error(error.message),
  })
  const settings =
    values === query.data || Object.keys(values).length
      ? values
      : query.data || {}
  return (
    <>
      <NodeSteerHeader
        title={t('misc.settingsTitle')}
        description={t('misc.settingsDescription')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <div className='grid gap-8 lg:grid-cols-[180px_minmax(0,720px)]'>
          <nav
            className='flex gap-1 overflow-x-auto lg:flex-col'
            aria-label={t('misc.settingsTitle')}
          >
            <Button
              variant={section === 'runtime' ? 'secondary' : 'ghost'}
              className='justify-start'
              onClick={() => setSection('runtime')}
            >
              {t('misc.runtimeSettings')}
            </Button>
            <Button
              variant={section === 'appearance' ? 'secondary' : 'ghost'}
              className='justify-start'
              onClick={() => setSection('appearance')}
            >
              {t('misc.appearance')}
            </Button>
          </nav>
          <div>
            <Separator className='mb-6 lg:hidden' />
            {section === 'runtime' ? (
              <Card>
                <CardHeader>
                  <CardTitle className='text-base'>
                    {t('misc.runtimeSettings')}
                  </CardTitle>
                  <p className='text-sm text-muted-foreground'>
                    {t('misc.runtimeSettingsDescription')}
                  </p>
                </CardHeader>
                <CardContent className='grid gap-4'>
                  {query.isLoading ? (
                    <div className='h-8 animate-pulse rounded bg-muted' />
                  ) : (
                    Object.entries(settings).map(([key, value]) => (
                      <label
                        className='grid gap-2 text-sm font-medium'
                        key={key}
                      >
                        {t(`misc.settings.${key}`, { defaultValue: key })}
                        <Input
                          className='font-mono text-sm'
                          aria-label={t(`misc.settings.${key}`, {
                            defaultValue: key,
                          })}
                          value={value}
                          disabled={!canWrite}
                          onChange={(event) =>
                            setValues({
                              ...settings,
                              [key]: event.target.value,
                            })
                          }
                        />
                      </label>
                    ))
                  )}
                  {canWrite ? (
                    <Button
                      className='w-fit'
                      onClick={() => save.mutate()}
                      disabled={save.isPending}
                    >
                      {t('common.save')}
                    </Button>
                  ) : null}
                </CardContent>
              </Card>
            ) : (
              <Card>
                <CardHeader>
                  <CardTitle className='text-base'>
                    {t('misc.appearance')}
                  </CardTitle>
                  <p className='text-sm text-muted-foreground'>
                    {t('misc.appearanceDescription')}
                  </p>
                </CardHeader>
                <CardContent>
                  <label className='grid max-w-sm gap-2 text-sm font-medium'>
                    {t('misc.theme')}
                    <Select
                      value={theme}
                      onValueChange={(value) =>
                        setTheme(value as 'light' | 'dark' | 'system')
                      }
                    >
                      <SelectTrigger>
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value='light'>{t('misc.light')}</SelectItem>
                        <SelectItem value='dark'>{t('misc.dark')}</SelectItem>
                        <SelectItem value='system'>
                          {t('nav.sections.system')}
                        </SelectItem>
                      </SelectContent>
                    </Select>
                  </label>
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      </Main>
    </>
  )
}

function ListLayout<TData>({
  title,
  description,
  query,
  columns,
  data,
  searchPlaceholder,
  action,
  extra,
  storageKey,
  pagination,
}: {
  title: string
  description: string
  query: {
    isLoading: boolean
    isError: boolean
    error: unknown
    refetch: () => unknown
  }
  columns: import('@tanstack/react-table').ColumnDef<TData>[]
  data: TData[]
  searchPlaceholder: string
  action?: React.ReactNode
  extra?: React.ReactNode
  /** 表格视图持久化键（列可见性/每页条数） */
  storageKey?: string
  /** 提供时启用服务端分页（页码/每页条数受控） */
  pagination?: {
    total: number
    page: number
    pageSize: number
    onPageChange: (page: number) => void
    onPageSizeChange: (pageSize: number) => void
  }
}) {
  const { t } = useTranslation()
  return (
    <>
      <NodeSteerHeader
        title={title}
        description={description}
        action={action}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        {extra}
        {query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : query.isLoading ? (
          <div className='grid gap-2'>
            <Skeleton className='h-9 w-full' />
            <Skeleton className='h-12 w-full' />
            <Skeleton className='h-12 w-full' />
            <Skeleton className='h-12 w-full' />
            <Skeleton className='h-12 w-full' />
          </div>
        ) : data.length || (pagination?.total ?? 0) > 0 ? (
          <DataTable
            data={data}
            columns={columns}
            searchPlaceholder={searchPlaceholder}
            storageKey={storageKey}
            manualPagination={Boolean(pagination)}
            total={pagination?.total}
            page={pagination?.page}
            pageSize={pagination?.pageSize}
            onPageChange={pagination?.onPageChange}
            onPageSizeChange={pagination?.onPageSizeChange}
          />
        ) : (
          <Card>
            <EmptyState message={t('common.noData')} />
          </Card>
        )}
      </Main>
    </>
  )
}

function LoadingPage({ title }: { title: string }) {
  return (
    <>
      <NodeSteerHeader title={title} />
      <Main>
        <div className='grid gap-3' aria-label={title}>
          <Skeleton className='h-8 w-48' />
          <Skeleton className='h-12 w-full' />
          <Skeleton className='h-12 w-full' />
          <Skeleton className='h-12 w-full' />
        </div>
      </Main>
    </>
  )
}
