import { useCallback, useMemo, useRef, useState } from 'react'
import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import type { ColumnDef } from '@tanstack/react-table'
import {
  Activity,
  Check,
  ChevronDown,
  Copy,
  Download,
  Pencil,
  Plus,
  RefreshCw,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { sessionToUser, useAuthStore } from '@/stores/auth-store'
import {
  api,
  ApiError,
  authenticatedFetch,
  type Application,
  type Artifact,
  type AuditLog,
  type Group,
  type Node,
  type NodeConnectionTestResult,
  type NodeEnrollment,
  type Schedule,
  type Script,
  type Settings,
  type SessionInfo,
  type Task,
  type User,
} from '@/lib/api'
import { copyText, selectText } from '@/lib/clipboard'
import { nodeDisplayName } from '@/lib/node-display-name'
import { useCanRun, useCanWrite } from '@/lib/permissions'
import { scheduleDisplayName, scheduleTriggerText } from '@/lib/schedules'
import { readTablePageSize } from '@/lib/table-view'
import { groupsForNode, targetMatchesNode, targetSummary } from '@/lib/targets'
import { cn } from '@/lib/utils'
import { useTheme } from '@/context/theme-provider'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
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
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { buildSchedulePayload } from '@/components/cron/schedule-payload'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { NodeBulkActions } from '@/features/catalog/node-bulk-actions'
import { NodeMonitoringOverviewRow } from '@/features/monitoring/node-overview-metrics'
import { DataTable } from '@/features/shared/data-table'
import { RackList, RackRow, RowText } from '@/features/shared/rack-list'
import {
  DetailField,
  DetailGrid,
  EmptyState,
  ErrorPage,
  ErrorState,
  DangerMenuItem,
  DropdownMenuItem,
  MoreMenu,
  LoadingState,
  SectionCard,
  SectionFull,
  SectionGrid,
  StatusBadge,
  TimeValue,
} from '@/features/shared/ui'

async function readHTTPError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as { error?: string; code?: string }
    return body.error || body.code || `HTTP ${response.status}`
  } catch {
    return `HTTP ${response.status}`
  }
}

async function downloadInboundBundle(nodeID: string) {
  const response = await authenticatedFetch(
    `/api/nodes/${nodeID}/inbound-bundle`
  )
  if (!response.ok) throw new Error(await readHTTPError(response))
  const disposition = response.headers.get('Content-Disposition') || ''
  const matched = /filename="?([^";]+)"?/i.exec(disposition)
  const filename = matched?.[1] || `nodesteer-inbound-${nodeID}.tar.gz`
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

async function rotateInboundBundle(nodeID: string) {
  const response = await authenticatedFetch(
    `/api/nodes/${nodeID}/inbound-bundle/rotate`,
    { method: 'POST' }
  )
  if (!response.ok) throw new Error(await readHTTPError(response))
  const disposition = response.headers.get('Content-Disposition') || ''
  const matched = /filename="?([^";]+)"?/i.exec(disposition)
  const filename = matched?.[1] || `nodesteer-inbound-${nodeID}.tar.gz`
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = filename
  link.click()
  URL.revokeObjectURL(url)
}

/** 触发发布包下载（带鉴权）；失败时抛出 HTTP 状态错误供调用方提示 */
async function downloadArtifactFile(artifact: Artifact) {
  const response = await authenticatedFetch(
    `/api/artifacts/${artifact.id}/download`
  )
  if (!response.ok) throw new Error(`HTTP ${response.status}`)
  const url = URL.createObjectURL(await response.blob())
  const link = document.createElement('a')
  link.href = url
  link.download = artifact.filename
  link.click()
  URL.revokeObjectURL(url)
}

export function Agents() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const canWrite = useCanWrite()
  const canRun = useCanRun()
  const [enrollmentOpen, setEnrollmentOpen] = useState(false)
  const query = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const groups = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.get<Group[]>('/groups'),
  })
  const groupNamesByNode = useMemo(() => {
    const map = new Map<string, string[]>()
    for (const node of query.data || []) {
      const names = groupsForNode(node, groups.data || []).map(
        (group) => group.name
      )
      if (names.length) map.set(node.id, names)
    }
    return map
  }, [groups.data, query.data])
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
        accessorKey: 'status',
        header: t('common.status'),
        size: 112,
        minSize: 88,
        cell: ({ row }: { row: { original: Node } }) => (
          <StatusBadge status={row.original.status} />
        ),
      },
      {
        id: 'hostname',
        accessorFn: (row) =>
          `${row.display_name || ''} ${row.hostname || ''} ${row.id}`,
        header: t('dashboard.hostname'),
        size: 230,
        minSize: 140,
        maxSize: 340,
        cell: ({ row }: { row: { original: Node } }) => {
          const name = nodeDisplayName(row.original, t('common.unknownNode'))
          const showHostname =
            Boolean(row.original.display_name?.trim()) &&
            row.original.hostname !== name
          return (
            <RowText
              primary={
                <a
                  href={`/agents/${row.original.id}`}
                  className='block truncate font-medium hover:underline'
                  title={
                    showHostname
                      ? `${name} (${row.original.hostname})`
                      : row.original.hostname
                  }
                >
                  {name}
                </a>
              }
              secondary={
                showHostname || row.original.endpoint ? (
                  <span className='flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5'>
                    {row.original.connection_mode ? (
                      <span className='font-mono'>
                        {row.original.connection_mode}
                      </span>
                    ) : null}
                    {row.original.endpoint ? (
                      <span
                        className='min-w-0 truncate font-mono'
                        title={row.original.endpoint}
                      >
                        {row.original.endpoint}
                      </span>
                    ) : null}
                    {showHostname ? (
                      <span
                        className='min-w-0 truncate font-mono'
                        title={row.original.hostname}
                      >
                        {row.original.hostname}
                      </span>
                    ) : null}
                  </span>
                ) : undefined
              }
            />
          )
        },
      },
      {
        accessorKey: 'os',
        header: t('dashboard.os'),
        size: 220,
        minSize: 180,
        cell: ({ row }: { row: { original: Node } }) => (
          <span className='block truncate' title={row.original.os || '-'}>
            {row.original.os || '-'}
          </span>
        ),
      },
      {
        id: 'groups',
        header: t('nodes.groups'),
        enableSorting: false,
        size: 150,
        minSize: 86,
        cell: ({ row }: { row: { original: Node } }) => {
          const names = groupNamesByNode.get(row.original.id)?.join(', ')
          return (
            <span className='block truncate' title={names || '-'}>
              {names || '-'}
            </span>
          )
        },
      },
      {
        accessorKey: 'ip',
        header: t('dashboard.ip'),
        size: 140,
        minSize: 124,
        cell: ({ row }: { row: { original: Node } }) => (
          <span
            className='block truncate font-mono text-xs'
            title={row.original.ip || '-'}
          >
            {row.original.ip || '-'}
          </span>
        ),
      },
      {
        accessorKey: 'arch',
        header: t('nodes.architecture'),
        size: 92,
        minSize: 76,
        cell: ({ row }: { row: { original: Node } }) => (
          <span className='font-mono text-xs'>{row.original.arch || '-'}</span>
        ),
      },
      {
        accessorKey: 'agent_version',
        header: t('nodes.agentVersion'),
        size: 128,
        minSize: 124,
      },
      {
        accessorKey: 'deployment_mode',
        header: t('nodes.deploymentMode'),
        size: 132,
        minSize: 108,
      },
      {
        accessorKey: 'last_seen',
        header: t('dashboard.lastSeen'),
        size: 160,
        minSize: 110,
        cell: ({ row }: { row: { original: Node } }) => (
          <TimeValue value={row.original.last_seen} />
        ),
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        size: 64,
        minSize: 56,
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
    [canWrite, deleteNode, groupNamesByNode, revokeNode, t, updateNodeStatus]
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
        rackPrimaryColumnId='hostname'
        enableRowSelection={canWrite || canRun}
        bulkEntityName={t('nav.nodes')}
        renderBulkActions={(table) => (
          <NodeBulkActions
            nodes={table
              .getFilteredSelectedRowModel()
              .rows.map((row) => row.original)}
            onClear={() => table.resetRowSelection()}
          />
        )}
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
  const [nodeEndpoint, setNodeEndpoint] = useState('')
  const [hubAddress, setHubAddress] = useState(
    typeof window === 'undefined' ? '' : window.location.origin
  )
  const [request, setRequest] = useState<{
    nodeName: string
    nodeIP: string
    endpoint: string
    hubAddress: string
  } | null>(null)
  const [copied, setCopied] = useState(false)
  const commandRef = useRef<HTMLPreElement>(null)
  const enrollment = useMutation({
    mutationFn: (value: NonNullable<typeof request>) =>
      api.post<NodeEnrollment>('/nodes/enrollment', {
        node_name: value.nodeName,
        node_ip: value.nodeIP,
        endpoint: value.endpoint,
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
    setNodeEndpoint('')
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
          data-testid='node-enrollment-form'
          className='grid gap-4 sm:grid-cols-2'
          onSubmit={(event) => {
            event.preventDefault()
            setCopied(false)
            const nextRequest = {
              nodeName: nodeName.trim(),
              nodeIP: nodeAddress.trim(),
              endpoint: nodeEndpoint.trim(),
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
            <Label htmlFor='enrollment-endpoint'>{t('nodes.endpoint')}</Label>
            <Input
              id='enrollment-endpoint'
              data-testid='enrollment-endpoint'
              value={nodeEndpoint}
              onChange={(event) => {
                setNodeEndpoint(event.target.value)
                clearEnrollment()
              }}
              placeholder={t('nodes.endpointPlaceholder')}
              required
            />
            <p className='text-xs font-normal text-muted-foreground'>
              {t('nodes.endpointHint')}
            </p>
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
            {enrollment.data.inbound_bundle_available &&
            enrollment.data.node_id ? (
              <div className='rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm'>
                <p>{t('nodes.inboundBundleHint')}</p>
                <Button
                  type='button'
                  variant='outline'
                  size='sm'
                  className='mt-2'
                  data-testid='enrollment-download-bundle'
                  onClick={() => {
                    if (!enrollment.data?.node_id) return
                    void downloadInboundBundle(enrollment.data.node_id).catch(
                      (error: Error) => toast.error(error.message)
                    )
                  }}
                >
                  <Download className='size-4' />
                  {t('nodes.downloadBundle')}
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}

/** Administrator-only endpoint edit; server normalizes host:port and marks bundle stale. */
function NodeEndpointDialog({
  node,
  open,
  onOpenChange,
}: {
  node: Node
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [value, setValue] = useState(node.endpoint || '')
  const [error, setError] = useState('')
  const update = useMutation({
    mutationFn: (endpoint: string) =>
      api.post(`/nodes/${node.id}`, { endpoint }),
    onSuccess: () => {
      toast.success(t('nodes.endpointUpdated'))
      void client.invalidateQueries({ queryKey: ['node', node.id] })
      void client.invalidateQueries({ queryKey: ['nodes'] })
      onOpenChange(false)
    },
    onError: (err) => setError(err.message),
  })
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('nodes.editEndpoint')}</DialogTitle>
          <DialogDescription>
            {t('nodes.editEndpointDescription')}
          </DialogDescription>
        </DialogHeader>
        <form
          className='grid gap-3'
          onSubmit={(event) => {
            event.preventDefault()
            if (!value.trim()) {
              setError(t('nodes.endpointInvalid'))
              return
            }
            setError('')
            update.mutate(value.trim())
          }}
        >
          <div className='grid gap-2'>
            <Label htmlFor='node-endpoint'>
              {t('nodes.endpoint')}
            </Label>
            <Input
              id='node-endpoint'
              data-testid='node-endpoint-input'
              value={value}
              onChange={(event) => {
                setValue(event.target.value)
                setError('')
              }}
              placeholder={t('nodes.endpointPlaceholder')}
              aria-invalid={Boolean(error)}
              required
            />
            <p className='text-xs text-muted-foreground'>
              {t('nodes.editEndpointHint')}
            </p>
            {error ? (
              <p className='text-xs text-destructive' role='alert'>
                {error}
              </p>
            ) : null}
          </div>
          <div className='flex justify-end gap-2'>
            <Button
              type='button'
              variant='outline'
              onClick={() => onOpenChange(false)}
            >
              {t('common.cancel')}
            </Button>
            <Button type='submit' disabled={update.isPending}>
              {t('common.save')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function CollapsibleList({
  title,
  count,
  preview,
  children,
}: {
  title: string
  count: number
  preview: string
  children: React.ReactNode
}) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  return (
    <Collapsible open={open} onOpenChange={setOpen} className='space-y-2'>
      <div className='flex flex-wrap items-center justify-between gap-2'>
        <h3 className='text-sm font-medium'>
          {title}
          <span className='ms-1.5 font-mono text-xs text-muted-foreground'>
            {count}
          </span>
        </h3>
        <CollapsibleTrigger asChild>
          <Button
            variant='ghost'
            size='sm'
            className='h-7 px-2 text-xs'
            aria-label={`${open ? t('common.collapse') : t('common.showAll')} ${title}`}
          >
            {open ? t('common.collapse') : t('common.showAll')}
            <ChevronDown
              className={cn(
                'ms-1 size-3.5 transition-transform',
                open && 'rotate-180'
              )}
            />
          </Button>
        </CollapsibleTrigger>
      </div>
      {!open && preview ? (
        <p
          className='truncate font-mono text-xs text-muted-foreground'
          title={preview}
        >
          {preview}
        </p>
      ) : null}
      <CollapsibleContent>{children}</CollapsibleContent>
    </Collapsible>
  )
}

export function AgentDetail() {
  const { t } = useTranslation()
  const id = window.location.pathname.split('/').pop() || ''
  const client = useQueryClient()
  const canWrite = useCanWrite()
  const canRun = useCanRun()
  const canAdmin = useCanWrite()
  const [renameOpen, setRenameOpen] = useState(false)
  const [labelKey, setLabelKey] = useState('')
  const [labelValue, setLabelValue] = useState('')
  const [connectionTest, setConnectionTest] =
    useState<NodeConnectionTestResult | null>(null)
  const [testingConnection, setTestingConnection] = useState(false)
  const [endpointOpen, setEndpointOpen] = useState(false)
  const node = useQuery({
    queryKey: ['node', id],
    queryFn: () => api.get<Node>(`/nodes/${id}`),
  })
  const executions = useQuery({
    queryKey: ['node-executions', id],
    queryFn: () =>
      api.get<ExecutionLike[]>(`/executions?node_id=${id}&limit=5`),
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
  const runConnectionTest = async () => {
    setTestingConnection(true)
    try {
      const result = await api.post<NodeConnectionTestResult>(
        `/nodes/${id}/connection-test`,
        {}
      )
      setConnectionTest(result)
      void client.invalidateQueries({ queryKey: ['node', id] })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    } finally {
      setTestingConnection(false)
    }
  }
  const downloadBundle = async () => {
    try {
      await downloadInboundBundle(id)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }
  const rotateBundle = async () => {
    if (!window.confirm(t('nodes.rotateBundleConfirm'))) return
    try {
      await rotateInboundBundle(id)
      toast.success(t('nodes.rotateBundleSuccess'))
      await client.invalidateQueries({ queryKey: ['node', id] })
      await client.invalidateQueries({ queryKey: ['nodes'] })
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }
  const nodeSchedules = (schedules.data || []).filter((schedule) =>
    targetMatchesNode(schedule.target, node.data, groups.data || [])
  )
  // 任务通过“命中节点的调度”反查，不再依赖 Task.Target
  const nodeTaskIds = new Set(nodeSchedules.map((schedule) => schedule.task_id))
  const nodeTasks = (tasks.data || []).filter((task) =>
    nodeTaskIds.has(task.id)
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
  const nodeGroupNames = groupsForNode(item, groups.data || [])
    .map((group) => group.name)
    .join(', ')
  return (
    <>
      <NodeSteerHeader
        title={nodeDisplayName(item, t('common.unknownNode'))}
        description={item.ip || item.agent_id}
        action={
          <div className='flex items-center gap-2'>
            <StatusBadge status={item.status} />
            {canRun ? (
              <Button
                variant='outline'
                size='sm'
                onClick={() => setRenameOpen(true)}
              >
                <Pencil className='size-4' />
                {t('nodes.rename')}
              </Button>
            ) : null}
          </div>
        }
        backTo='/agents'
      />
      <Main className='flex flex-1 flex-col gap-6'>
        {canAdmin && (
          <NodeMonitoringOverviewRow
            nodeId={item.id}
            nodeName={nodeDisplayName(item, t('common.unknownNode'))}
            status={item.status}
          />
        )}
        <nav
          aria-label={t('webFix.sections')}
          className='flex flex-wrap gap-3 text-sm'
        >
          {[
            'overview',
            'inventory',
            'executions',
            'tasks',
            'schedules',
            'applications',
          ].map((section) => (
            <a
              key={section}
              href={`#node-${section}`}
              className='text-primary hover:underline'
            >
              {t(`webFix.nodeSections.${section}`)}
            </a>
          ))}
        </nav>
        <SectionGrid className='w-full'>
          <SectionFull id='node-overview'>
            <SectionCard title={t('nodes.overview')}>
              <div className='space-y-4'>
                <DetailGrid>
                  <DetailField label={t('nodes.displayName')}>
                    <span
                      className='block truncate'
                      title={nodeDisplayName(item, t('common.unknownNode'))}
                    >
                      {nodeDisplayName(item, t('common.unknownNode'))}
                    </span>
                  </DetailField>
                  <DetailField label={t('dashboard.hostname')}>
                    <span
                      className='block truncate font-mono text-xs'
                      title={item.hostname}
                    >
                      {item.hostname || '-'}
                    </span>
                  </DetailField>
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
                    <span className='font-mono text-xs'>
                      {item.arch || '-'}
                    </span>
                  </DetailField>
                  <DetailField label={t('nodes.deploymentMode')}>
                    {item.deployment_mode || '-'}
                  </DetailField>
                  <DetailField label={t('nodes.groups')}>
                    {nodeGroupNames || '-'}
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
                <section className='space-y-3' data-testid='node-inbound-section'>
                  <div className='flex flex-wrap items-center justify-between gap-2'>
                    <h2 className='text-sm font-medium'>
                      {t('nodes.inbound')}
                    </h2>
                    {canAdmin ? (
                      <div className='flex flex-wrap gap-2'>
                        {item.endpoint &&
                        (!item.inbound?.configured ||
                          item.inbound_bundle_stale) ? (
                          <Button
                            type='button'
                            size='sm'
                            variant='outline'
                            data-testid='node-download-bundle'
                            onClick={() => void downloadBundle()}
                          >
                            <Download className='size-4' />
                            {t('nodes.downloadBundle')}
                          </Button>
                        ) : null}
                        {item.endpoint && item.inbound?.configured ? (
                          <Button
                            type='button'
                            size='sm'
                            variant='outline'
                            data-testid='node-rotate-bundle'
                            onClick={() => void rotateBundle()}
                          >
                            <RefreshCw className='size-4' />
                            {t('nodes.rotateBundle')}
                          </Button>
                        ) : null}
                        <Button
                          type='button'
                          size='sm'
                          variant='outline'
                          data-testid='node-edit-endpoint'
                          onClick={() => setEndpointOpen(true)}
                        >
                          <Pencil className='size-4' />
                          {t('nodes.editEndpoint')}
                        </Button>
                        {item.endpoint ? (
                          <Button
                            type='button'
                            size='sm'
                            variant='outline'
                            data-testid='node-test-connection'
                            disabled={testingConnection}
                            onClick={() => void runConnectionTest()}
                          >
                            <Activity className='size-4' />
                            {t('nodes.testConnection')}
                          </Button>
                        ) : null}
                      </div>
                    ) : null}
                  </div>
                  <DetailGrid>
                    <DetailField label={t('nodes.endpoint')}>
                      <span className='font-mono text-xs'>
                        {item.endpoint || '-'}
                      </span>
                    </DetailField>
                    <DetailField label={t('nodes.connectionMode')}>
                      <span className='font-mono text-xs'>
                        {item.connection_mode || '-'}
                      </span>
                    </DetailField>
                    <DetailField label={t('nodes.lastConnectionMode')}>
                      <span className='font-mono text-xs'>
                        {item.last_connection_mode || '-'}
                      </span>
                    </DetailField>
                    <DetailField label={t('nodes.inboundStatus')}>
                      <StatusBadge
                        status={
                          item.inbound_bundle_stale
                            ? 'stale'
                            : item.inbound?.configured
                              ? 'configured'
                              : 'not_configured'
                        }
                      />
                    </DetailField>
                    <DetailField label={t('nodes.inboundCapable')}>
                      {item.inbound?.capable
                        ? t('common.yes')
                        : t('common.no')}
                    </DetailField>
                    <DetailField label={t('nodes.dataPlane')}>
                      {item.inbound?.data_plane
                        ? t('nodes.dataPlaneAvailable')
                        : item.connection_mode === 'outbound'
                          ? t('nodes.dataPlaneStandby')
                          : t('nodes.dataPlaneUnavailable')}
                    </DetailField>
                    <DetailField label={t('nodes.certFingerprint')}>
                      <span
                        className='block truncate font-mono text-xs'
                        title={item.inbound?.cert_fingerprint || '-'}
                      >
                        {item.inbound?.cert_fingerprint || '-'}
                      </span>
                    </DetailField>
                    <DetailField label={t('nodes.certNotAfter')}>
                      <TimeValue value={item.inbound?.cert_not_after} />
                    </DetailField>
                    <DetailField label={t('nodes.lastHandshake')}>
                      <TimeValue value={item.inbound?.last_handshake_at} />
                    </DetailField>
                    <DetailField label={t('nodes.lastError')}>
                      <span className='font-mono text-xs'>
                        {item.inbound?.last_error || '-'}
                      </span>
                    </DetailField>
                  </DetailGrid>
                  {item.inbound?.last_error === 'inbound_certificate_expired' ? (
                    <p
                      className='rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive'
                      role='alert'
                    >
                      {t('nodes.certExpiredWarning')}
                    </p>
                  ) : null}
                  {item.inbound?.last_error === 'inbound_pin_mismatch' ||
                  item.inbound?.last_error === 'pin_mismatch' ? (
                    <p
                      className='rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive'
                      role='alert'
                    >
                      {t('nodes.pinMismatchWarning')}
                    </p>
                  ) : null}
                  {connectionTest ? (
                    <div
                      className='space-y-2 rounded-md border p-3'
                      data-testid='node-connection-test-result'
                    >
                      <p className='text-sm font-medium'>
                        {connectionTest.connected
                          ? t('nodes.testConnected', {
                              mode: connectionTest.origin || '-',
                            })
                          : t('nodes.testResult')}
                      </p>
                      <ul className='space-y-1 text-xs'>
                        {connectionTest.stages.map((stage) => (
                          <li
                            key={stage.name}
                            className='flex flex-wrap items-center gap-2 font-mono'
                          >
                            <span className='w-20'>{stage.name}</span>
                            <StatusBadge
                              status={stage.ok ? 'success' : 'failed'}
                            />
                            {stage.code ? <span>{stage.code}</span> : null}
                            {stage.error ? (
                              <span className='text-muted-foreground'>
                                {stage.error}
                              </span>
                            ) : null}
                            {stage.duration_ms ? (
                              <span className='text-muted-foreground'>
                                {stage.duration_ms}ms
                              </span>
                            ) : null}
                          </li>
                        ))}
                      </ul>
                    </div>
                  ) : null}
                </section>
                <Separator />
                <section className='space-y-3'>
                  <h2 className='text-sm font-medium'>{t('nodes.labels')}</h2>
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
                        aria-label={t('nodes.labelKey')}
                        placeholder='key'
                        value={labelKey}
                        onChange={(event) => setLabelKey(event.target.value)}
                      />
                      <Input
                        className='h-8 w-32 font-mono text-xs'
                        aria-label={t('nodes.labelValue')}
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
          </SectionFull>
          <SectionFull id='node-inventory'>
            <SectionCard title={t('nodes.inventory')}>
              <div className='space-y-4'>
                {item.inventory ? (
                  <>
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
                    </DetailGrid>
                    <Separator />
                    <CollapsibleList
                      title={t('nodes.filesystems')}
                      count={item.inventory.filesystem?.length || 0}
                      preview={
                        item.inventory.filesystem?.length
                          ? t('nodes.filesystemPreview', {
                              mount: item.inventory.filesystem[0].mount,
                              count: item.inventory.filesystem.length,
                            })
                          : ''
                      }
                    >
                      <ul className='grid gap-1 font-mono text-xs sm:grid-cols-2 xl:grid-cols-3'>
                        {item.inventory.filesystem?.map((filesystem) => (
                          <li key={filesystem.mount} className='break-words'>
                            {filesystem.mount} ({filesystem.fs_type},{' '}
                            {Math.round(filesystem.free_kb / 1024)}/
                            {Math.round(filesystem.total_kb / 1024)} MB)
                          </li>
                        ))}
                      </ul>
                    </CollapsibleList>
                    <Separator />
                    <CollapsibleList
                      title={t('nodes.network')}
                      count={item.inventory.network?.length || 0}
                      preview={
                        item.inventory.network?.length
                          ? t('nodes.networkPreview', {
                              interfaces: item.inventory.network
                                .map((network) => network.interface)
                                .join(', '),
                              count: item.inventory.network.length,
                            })
                          : ''
                      }
                    >
                      <ul className='grid gap-2 font-mono text-xs sm:grid-cols-2'>
                        {item.inventory.network?.map((network) => (
                          <li key={network.interface} className='break-words'>
                            <span className='font-medium'>
                              {network.interface}
                            </span>{' '}
                            {network.addresses.join(', ')}
                          </li>
                        ))}
                      </ul>
                    </CollapsibleList>
                  </>
                ) : (
                  <EmptyState message={t('nodes.noInventory')} />
                )}
                <Separator />
                <section className='space-y-3'>
                  <h2 className='text-sm font-medium'>
                    {t('nodes.capabilities')}
                  </h2>
                  <div className='grid gap-2 sm:grid-cols-2'>
                    {Object.entries(item.capabilities || {}).map(
                      ([key, value]) => (
                        <span
                          className='flex min-w-0 items-center gap-2 text-sm'
                          key={key}
                        >
                          <StatusBadge
                            status={value ? 'enabled' : 'disabled'}
                          />
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
          </SectionFull>
          <SectionFull id='node-executions'>
            <SectionCard
              title={t('nav.executions')}
              action={
                <Link to='/executions' search={{ node_id: id }}>
                  {t('common.viewAll')}
                </Link>
              }
            >
              {executions.isLoading ? (
                <LoadingState />
              ) : executions.isError ? (
                <ErrorState
                  error={executions.error}
                  onRetry={() => executions.refetch()}
                />
              ) : (
                <ExecutionRows
                  executions={executions.data || []}
                  tasks={tasks.data || []}
                />
              )}
            </SectionCard>
          </SectionFull>
          <SectionFull id='node-tasks'>
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
          </SectionFull>
          <SectionFull id='node-schedules'>
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
                        title={scheduleDisplayName(schedule, t)}
                      >
                        {scheduleDisplayName(schedule, t)}
                      </a>
                      <span className='min-w-0 truncate text-xs text-muted-foreground'>
                        {tasks.data?.find(
                          (task) => task.id === schedule.task_id
                        )?.name || t('common.unknownTask')}
                      </span>
                      <span
                        className='max-w-full truncate font-mono text-xs text-muted-foreground'
                        title={scheduleTriggerText(schedule, t)}
                      >
                        {scheduleTriggerText(schedule, t)}
                      </span>
                      <span className='max-w-full truncate text-xs text-muted-foreground'>
                        {targetSummary(
                          schedule.target,
                          t,
                          new Map(
                            (node.data ? [node.data] : []).map((item) => [
                              item.id,
                              nodeDisplayName(item, t('common.unknownNode')),
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
          </SectionFull>
          <SectionFull id='node-applications'>
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
          </SectionFull>
        </SectionGrid>
      </Main>
      <NodeRenameDialog
        key={`rename:${item.id}:${renameOpen ? 'open' : 'closed'}:${item.display_name || ''}`}
        node={item}
        open={renameOpen}
        onOpenChange={setRenameOpen}
      />
      <NodeEndpointDialog
        key={`endpoint:${item.id}:${endpointOpen ? 'open' : 'closed'}:${item.endpoint || ''}`}
        node={item}
        open={endpointOpen}
        onOpenChange={setEndpointOpen}
      />
    </>
  )
}

/** 节点显示名重命名：Operator/Administrator 可用，空值表示清除并回退 hostname。 */
function NodeRenameDialog({
  node,
  open,
  onOpenChange,
}: {
  node: Node
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { t } = useTranslation()
  const client = useQueryClient()
  const [value, setValue] = useState(node.display_name || '')
  const [error, setError] = useState('')
  const rename = useMutation({
    mutationFn: (displayName: string) =>
      api.post(`/nodes/${node.id}`, { display_name: displayName }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: ['nodes'] })
      void client.invalidateQueries({ queryKey: ['node', node.id] })
      void client.invalidateQueries({ queryKey: ['monitoring-nodes-overview'] })
      void client.invalidateQueries({ queryKey: ['dashboard'] })
      toast.success(t('nodes.renameSuccess'))
      onOpenChange(false)
    },
    onError: (mutationError: Error) => setError(mutationError.message),
  })
  const runeCount = Array.from(value).length
  const tooLong = runeCount > 64
  const hasControl = Array.from(value).some((char) => {
    const code = char.codePointAt(0) ?? 0
    return code <= 0x1f || (code >= 0x7f && code <= 0x9f)
  })
  const invalid = tooLong || hasControl
  const submit = () => {
    if (invalid) return
    setError('')
    rename.mutate(value.trim())
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>{t('nodes.renameTitle')}</DialogTitle>
          <DialogDescription>{t('nodes.renameDescription')}</DialogDescription>
        </DialogHeader>
        <form
          className='grid gap-3'
          onSubmit={(event) => {
            event.preventDefault()
            submit()
          }}
        >
          <div className='grid gap-2'>
            <Label htmlFor='node-display-name'>{t('nodes.displayName')}</Label>
            <Input
              id='node-display-name'
              value={value}
              onChange={(event) => {
                setValue(event.target.value)
                setError('')
              }}
              placeholder={node.hostname}
              aria-invalid={invalid || Boolean(error)}
            />
            <div className='flex items-center justify-between gap-2 text-xs text-muted-foreground'>
              <span>{t('nodes.renameHint')}</span>
              <span className={tooLong ? 'text-destructive' : undefined}>
                {runeCount}/64
              </span>
            </div>
            {tooLong || hasControl || error ? (
              <p className='text-xs text-destructive' role='alert'>
                {error ||
                  (tooLong
                    ? t('nodes.renameTooLong')
                    : t('nodes.renameControlChars'))}
              </p>
            ) : null}
          </div>
          <div className='flex justify-end gap-2'>
            <Button
              type='button'
              variant='outline'
              onClick={() => onOpenChange(false)}
            >
              {t('common.cancel')}
            </Button>
            <Button type='submit' disabled={invalid || rename.isPending}>
              {t('common.save')}
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
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
    <RackList testId='node-executions-rack'>
      {executions.map((execution) => (
        <RackRow
          key={execution.id}
          testId={`node-execution-${execution.id}`}
          title={
            taskNames.get(execution.task_id) ? (
              <a
                href={`/tasks/${execution.task_id}`}
                className='hover:underline'
                title={taskNames.get(execution.task_id)}
              >
                {taskNames.get(execution.task_id)}
              </a>
            ) : (
              t('common.unknownTask')
            )
          }
          fields={[
            {
              label: t('common.status'),
              value: <StatusBadge status={execution.status} />,
            },
            {
              label: t('executions.start'),
              value: <TimeValue value={execution.start_time} />,
            },
            {
              label: t('executions.duration'),
              value: execution.end_time
                ? new Date(
                    new Date(execution.end_time).getTime() -
                      new Date(execution.start_time).getTime()
                  )
                    .toISOString()
                    .slice(11, 19)
                : '-',
            },
          ]}
          actions={
            <a
              className='text-sm text-primary hover:underline'
              href={`/executions/${execution.id}`}
            >
              {t('common.viewDetails')}
            </a>
          }
        />
      ))}
    </RackList>
  )
}

/** 审计资源 → 详情路由映射；无详情路由的资源类型（如 setting）返回 undefined */
function auditResourcePath(resource: string, id: string): string | undefined {
  if (!id) return undefined
  switch (resource) {
    case 'task':
      return `/tasks/${id}`
    case 'script':
      return `/scripts/${id}`
    case 'schedule':
      return `/schedules/${id}`
    case 'application':
      return `/applications/${id}`
    case 'group':
      return `/groups/${id}`
    case 'node':
      return `/agents/${id}`
    case 'execution':
      return `/executions/${id}`
    case 'artifact':
      return `/artifacts/${id}`
    case 'user':
      return `/users/${id}`
    case 'file_transfer':
      return `/transfers/${id}`
    default:
      return undefined
  }
}

export function Schedules() {
  const { t } = useTranslation()
  const client = useQueryClient()
  const canRun = useCanRun()
  const canWrite = useCanWrite()
  const schedules = useQuery({
    queryKey: ['schedules'],
    queryFn: () => api.get<Schedule[]>('/schedules'),
  })
  const tasks = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<Task[]>('/tasks'),
  })
  const nodes = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const groups = useQuery({
    queryKey: ['groups'],
    queryFn: () => api.get<Group[]>('/groups'),
  })
  const [runTarget, setRunTarget] = useState<Schedule | null>(null)
  const { mutate: toggleSchedule } = useMutation({
    mutationFn: (schedule: Schedule) =>
      api.put(
        `/schedules/${schedule.id}`,
        buildSchedulePayload({ ...schedule, enabled: !schedule.enabled })
      ),
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
  const runSchedule = useMutation({
    mutationFn: (schedule: Schedule) =>
      api.post(`/tasks/${schedule.task_id}/run`, {
        schedule_id: schedule.id,
      }),
    onSuccess: () => {
      toast.success(t('schedules.runStarted'))
      window.location.assign('/executions')
    },
    onError: (error) => toast.error(error.message),
  })
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
  const groupNames = useMemo(
    () => new Map((groups.data || []).map((group) => [group.id, group.name])),
    [groups.data]
  )
  const columns = useMemo<ColumnDef<Schedule>[]>(
    () => [
      {
        id: 'name',
        accessorFn: (row: Schedule) =>
          `${scheduleDisplayName(row, t)} ${row.id}`,
        header: t('common.name'),
        size: 220,
        minSize: 170,
        maxSize: 320,
        cell: ({ row }: { row: { original: Schedule } }) => (
          <a
            href={`/schedules/${row.original.id}`}
            className='block truncate font-medium hover:underline'
            title={scheduleDisplayName(row.original, t)}
          >
            {scheduleDisplayName(row.original, t)}
          </a>
        ),
      },
      {
        id: 'task',
        accessorFn: (row: Schedule) =>
          `${tasks.data?.find((task) => task.id === row.task_id)?.name || ''} ${row.task_id}`,
        header: t('schedules.task'),
        size: 200,
        minSize: 150,
        maxSize: 280,
        cell: ({ row }: { row: { original: Schedule } }) => (
          <a
            href={`/tasks/${row.original.task_id}`}
            className='block truncate hover:underline'
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
        id: 'target',
        accessorFn: (row: Schedule) =>
          targetSummary(row.target, t, nodeNames, groupNames),
        header: t('schedules.target'),
        size: 190,
        minSize: 140,
        cell: ({ row }: { row: { original: Schedule } }) => {
          const text = targetSummary(
            row.original.target,
            t,
            nodeNames,
            groupNames
          )
          return (
            <span className='block truncate text-sm' title={text}>
              {text}
            </span>
          )
        },
      },
      {
        id: 'trigger',
        accessorFn: (row: Schedule) => scheduleTriggerText(row, t),
        header: t('schedules.expression'),
        size: 185,
        minSize: 130,
        cell: ({ row }: { row: { original: Schedule } }) => {
          const text = scheduleTriggerText(row.original, t)
          return (
            <div className='min-w-0'>
              <div className='truncate text-xs text-muted-foreground'>
                {t(`schedules.types.${row.original.type}`, {
                  defaultValue: row.original.type,
                })}
              </div>
              <span className='block truncate font-mono text-xs' title={text}>
                {text}
              </span>
            </div>
          )
        },
      },
      {
        accessorKey: 'timezone',
        header: t('schedules.timezone'),
        size: 130,
        minSize: 110,
      },
      {
        accessorKey: 'execution_owner',
        header: t('schedules.executionOwner'),
        size: 100,
        minSize: 90,
        cell: ({ row }: { row: { original: Schedule } }) =>
          t(`schedules.owners.${row.original.execution_owner}`, {
            defaultValue: row.original.execution_owner,
          }),
      },
      {
        accessorKey: 'offline_policy',
        header: t('schedules.offline'),
        size: 145,
        minSize: 105,
        cell: ({ row }: { row: { original: Schedule } }) =>
          t(`schedules.offlinePolicies.${row.original.offline_policy}`, {
            defaultValue: row.original.offline_policy,
          }),
      },
      {
        accessorKey: 'misfire_policy',
        header: t('schedules.misfire'),
        size: 130,
        minSize: 105,
        cell: ({ row }: { row: { original: Schedule } }) =>
          t(`schedules.misfirePolicies.${row.original.misfire_policy}`, {
            defaultValue: row.original.misfire_policy,
          }),
      },
      {
        accessorKey: 'enabled',
        header: t('common.status'),
        size: 96,
        minSize: 76,
        cell: ({ row }: { row: { original: Schedule } }) => (
          <StatusBadge status={row.original.enabled ? 'enabled' : 'disabled'} />
        ),
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        size: 64,
        minSize: 56,
        cell: ({ row }: { row: { original: Schedule } }) => {
          const schedule = row.original
          if (!canWrite && !canRun) return null
          return (
            <MoreMenu>
              {canRun ? (
                <DropdownMenuItem onSelect={() => setRunTarget(schedule)}>
                  {t('schedules.runNow')}
                </DropdownMenuItem>
              ) : null}
              {canWrite ? (
                <DropdownMenuItem asChild>
                  <a href={`/schedules/${schedule.id}`}>{t('common.edit')}</a>
                </DropdownMenuItem>
              ) : null}
              {canWrite ? (
                <DropdownMenuItem onSelect={() => toggleSchedule(schedule)}>
                  {schedule.enabled ? t('common.disable') : t('common.enable')}
                </DropdownMenuItem>
              ) : null}
              {canWrite ? (
                <DangerMenuItem
                  onSelect={() => removeSchedule(schedule)}
                  title={`${t('common.delete')}: ${scheduleDisplayName(schedule, t)}`}
                  description={t('schedules.confirmDelete', {
                    name: scheduleDisplayName(schedule, t),
                  })}
                />
              ) : null}
            </MoreMenu>
          )
        },
      },
    ],
    [
      canRun,
      canWrite,
      groupNames,
      nodeNames,
      removeSchedule,
      t,
      tasks.data,
      toggleSchedule,
    ]
  )
  return (
    <>
      <ListLayout
        title={t('schedules.title')}
        description={t('schedules.description')}
        query={schedules}
        columns={columns}
        data={schedules.data || []}
        searchPlaceholder={t('schedules.searchPlaceholder')}
        storageKey='schedules'
        emptyMessage={t('schedules.noSchedules')}
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
      <ConfirmDialog
        open={runTarget !== null}
        onOpenChange={(open) => {
          if (!open) setRunTarget(null)
        }}
        title={t('schedules.confirmRun', {
          name: runTarget ? scheduleDisplayName(runTarget, t) : '',
        })}
        desc={t('schedules.runDescription')}
        cancelBtnText={t('common.cancel')}
        confirmText={t('schedules.runNow')}
        isLoading={runSchedule.isPending}
        handleConfirm={() => {
          if (runTarget) runSchedule.mutate(runTarget)
          setRunTarget(null)
        }}
      />
    </>
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
          <RowText
            primary={
              <a
                href={`/scripts/${row.original.id}`}
                className='block truncate font-medium hover:underline'
                title={row.original.name}
              >
                {row.original.name}
              </a>
            }
            secondary={
              row.original.description ? (
                <span
                  className='block truncate text-xs text-muted-foreground'
                  title={row.original.description}
                >
                  {row.original.description}
                </span>
              ) : undefined
            }
          />
        ),
      },
      {
        accessorKey: 'interpreter',
        header: t('scripts.interpreter'),
        size: 130,
        minSize: 110,
        cell: ({ row }: { row: { original: Script } }) => (
          <span
            className='block truncate font-mono text-xs'
            title={row.original.interpreter}
          >
            {row.original.interpreter}
          </span>
        ),
      },
      {
        accessorKey: 'revision',
        header: t('tasks.revision'),
        size: 92,
        minSize: 92,
        cell: ({ row }: { row: { original: Script } }) => (
          <span className='font-mono text-xs'>r{row.original.revision}</span>
        ),
      },
      {
        accessorKey: 'timeout',
        header: t('scripts.timeout'),
        size: 80,
        minSize: 76,
        cell: ({ row }: { row: { original: Script } }) =>
          `${row.original.timeout}s`,
      },
      {
        accessorKey: 'sha256',
        header: t('scripts.sha256'),
        size: 160,
        minSize: 148,
        cell: ({ row }: { row: { original: Script } }) => (
          <span className='font-mono text-xs' title={row.original.sha256}>
            {row.original.sha256.slice(0, 16)}…
          </span>
        ),
      },
      {
        accessorKey: 'updated_at',
        header: t('scripts.updated'),
        size: 170,
        minSize: 155,
        cell: ({ row }: { row: { original: Script } }) => (
          <TimeValue value={row.original.updated_at} />
        ),
      },
      {
        id: 'status',
        accessorFn: (row: Script) => (row.enabled ? 'enabled' : 'disabled'),
        header: t('common.status'),
        size: 96,
        minSize: 88,
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
        minSize: 190,
        maxSize: 320,
        cell: ({ row }: { row: { original: Group } }) => (
          <span
            className='block truncate font-medium'
            title={row.original.name}
          >
            {row.original.name}
          </span>
        ),
      },
      {
        accessorKey: 'type',
        header: t('common.type'),
        size: 100,
        minSize: 90,
        cell: ({ row }: { row: { original: Group } }) =>
          t(`groups.types.${row.original.type}`, {
            defaultValue: row.original.type,
          }),
      },
      {
        accessorKey: 'description',
        header: t('common.description'),
        size: 340,
        minSize: 260,
        cell: ({ row }: { row: { original: Group } }) => (
          <span className='block truncate' title={row.original.description}>
            {row.original.description || '-'}
          </span>
        ),
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
        size: 84,
        minSize: 76,
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
        size: 210,
        minSize: 180,
        maxSize: 300,
        cell: ({ row }: { row: { original: Application } }) => (
          <a
            href={`/applications/${row.original.id}`}
            className='block truncate font-medium hover:underline'
            title={row.original.name}
          >
            {row.original.name}
          </a>
        ),
      },
      {
        accessorKey: 'version',
        header: t('common.version'),
        size: 88,
        minSize: 76,
      },
      {
        accessorKey: 'binary_path',
        header: t('apps.binary'),
        size: 300,
        minSize: 230,
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
        size: 250,
        minSize: 200,
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
        size: 92,
        minSize: 92,
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
        await downloadArtifactFile(artifact)
      } catch (error) {
        const detail = error instanceof Error ? `: ${error.message}` : ''
        toast.error(`${t('artifacts.download')}${detail}`)
      }
    },
    [t]
  )
  const columns = useMemo<ColumnDef<Artifact>[]>(
    () => [
      {
        accessorKey: 'name',
        header: t('common.name'),
        size: 190,
        minSize: 160,
        maxSize: 280,
        cell: ({ row }: { row: { original: Artifact } }) => (
          <a
            className='block truncate font-medium hover:underline'
            href={`/artifacts/${row.original.id}`}
            title={row.original.name}
          >
            {row.original.name}
          </a>
        ),
      },
      {
        accessorKey: 'version',
        header: t('common.version'),
        size: 88,
        minSize: 76,
      },
      {
        accessorKey: 'architecture',
        header: t('artifacts.archField'),
        size: 90,
        minSize: 76,
      },
      {
        accessorKey: 'filename',
        header: t('artifacts.filename'),
        size: 210,
        minSize: 170,
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
        size: 84,
        minSize: 78,
        cell: ({ row }: { row: { original: Artifact } }) =>
          `${Math.round(row.original.size / 1024)} KB`,
      },
      {
        accessorKey: 'sha256',
        header: 'SHA256',
        size: 160,
        minSize: 140,
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
        size: 200,
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

export function ArtifactDetail() {
  const { t } = useTranslation()
  const canWrite = useCanWrite()
  const client = useQueryClient()
  const id = window.location.pathname.split('/').pop() || ''
  const [deleteOpen, setDeleteOpen] = useState(false)
  const artifact = useQuery({
    queryKey: ['artifact', id],
    queryFn: () => api.get<Artifact>(`/artifacts/${id}`),
  })
  const applications = useQuery({
    queryKey: ['applications'],
    queryFn: () => api.get<Application[]>('/applications'),
  })
  const { mutate: removeArtifact, isPending: removing } = useMutation({
    mutationFn: () => api.del(`/artifacts/${id}`),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ['artifacts'] })
      toast.success(t('common.deleted'))
      window.location.assign('/artifacts')
    },
    onError: (error) => toast.error(error.message),
  })
  if (artifact.isError && !artifact.data)
    return (
      <ErrorPage
        title={t('artifacts.title')}
        error={artifact.error}
        onRetry={() => artifact.refetch()}
        backTo='/artifacts'
      />
    )
  if (!artifact.data) return <LoadingPage title={t('artifacts.title')} />
  const item = artifact.data
  const usedBy = (applications.data || []).filter(
    (application) => application.artifact_id === item.id
  )
  const download = async () => {
    try {
      await downloadArtifactFile(item)
    } catch (error) {
      const detail = error instanceof Error ? `: ${error.message}` : ''
      toast.error(`${t('artifacts.download')}${detail}`)
    }
  }
  return (
    <>
      <NodeSteerHeader
        title={item.name}
        description={`${item.version || '-'} · ${item.architecture || '-'}`}
        backTo='/artifacts'
        action={
          <div className='flex items-center gap-2'>
            <Button variant='outline' onClick={() => void download()}>
              {t('artifacts.download')}
            </Button>
            {canWrite ? (
              <Button variant='destructive' onClick={() => setDeleteOpen(true)}>
                {t('common.delete')}
              </Button>
            ) : null}
          </div>
        }
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <ConfirmDialog
          open={deleteOpen}
          onOpenChange={setDeleteOpen}
          title={`${t('common.delete')}: ${item.name}`}
          desc={t('artifacts.confirmDelete')}
          cancelBtnText={t('common.cancel')}
          confirmText={t('common.delete')}
          destructive
          isLoading={removing}
          handleConfirm={() => {
            removeArtifact()
            setDeleteOpen(false)
          }}
        />
        <SectionCard title={t('artifacts.title')}>
          <DetailGrid>
            <DetailField label={t('common.name')}>{item.name}</DetailField>
            <DetailField label={t('common.version')}>
              {item.version || '-'}
            </DetailField>
            <DetailField label={t('artifacts.archField')}>
              {item.architecture || '-'}
            </DetailField>
            <DetailField label={t('artifacts.filename')}>
              <span className='font-mono text-xs break-all'>
                {item.filename}
              </span>
            </DetailField>
            <DetailField label={t('artifacts.size')}>
              {`${Math.round(item.size / 1024)} KB`}
            </DetailField>
            <DetailField label='SHA256'>
              <span className='font-mono text-xs break-all'>{item.sha256}</span>
            </DetailField>
            <DetailField label={t('artifacts.uploaded')}>
              <TimeValue value={item.uploaded_at} absolute />
            </DetailField>
          </DetailGrid>
        </SectionCard>
        <SectionCard title={t('artifacts.usage')}>
          {usedBy.length ? (
            <div className='flex flex-wrap gap-x-3 gap-y-2'>
              {usedBy.map((application) => (
                <a
                  className='hover:underline'
                  href={`/applications/${application.id}`}
                  key={application.id}
                >
                  {application.name}
                </a>
              ))}
            </div>
          ) : (
            <span className='text-sm text-muted-foreground'>-</span>
          )}
        </SectionCard>
      </Main>
    </>
  )
}

export function Audit() {
  const { t } = useTranslation()
  const [page, setPage] = useState(1)
  const [pageSize, setPageSize] = useState(() => readTablePageSize('audit', 10))
  const [filter, setFilter] = useState('')
  const query = useQuery({
    queryKey: ['audit', filter, page, pageSize],
    queryFn: () =>
      api.getPage<AuditLog>(
        `/audit?limit=${pageSize}&offset=${(page - 1) * pageSize}&q=${encodeURIComponent(filter)}`
      ),
    placeholderData: keepPreviousData,
  })
  const columns = useMemo<ColumnDef<AuditLog>[]>(
    () => [
      {
        accessorKey: 'created_at',
        header: t('common.time'),
        size: 170,
        minSize: 155,
        cell: ({ row }: { row: { original: AuditLog } }) => (
          <TimeValue value={row.original.created_at} />
        ),
      },
      {
        accessorKey: 'username',
        header: t('common.name'),
        size: 130,
        minSize: 110,
      },
      {
        accessorKey: 'action',
        header: t('common.actions'),
        size: 140,
        minSize: 120,
      },
      {
        accessorKey: 'resource',
        header: t('common.type'),
        size: 110,
        minSize: 96,
      },
      {
        accessorKey: 'resource_id',
        header: t('common.resourceId'),
        size: 210,
        minSize: 160,
        cell: ({ row }: { row: { original: AuditLog } }) => {
          const path = auditResourcePath(
            row.original.resource,
            row.original.resource_id
          )
          return path ? (
            <a
              className='block truncate font-mono text-xs hover:underline'
              href={path}
              title={row.original.resource_id}
            >
              {row.original.resource_id}
            </a>
          ) : (
            <span
              className='block truncate font-mono text-xs'
              title={row.original.resource_id}
            >
              {row.original.resource_id || '-'}
            </span>
          )
        },
      },
      {
        accessorKey: 'detail',
        header: t('common.description'),
        size: 420,
        minSize: 260,
        cell: ({ row }: { row: { original: AuditLog } }) => (
          <span className='block truncate' title={row.original.detail}>
            {row.original.detail || '-'}
          </span>
        ),
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
      filter={filter}
      onFilterChange={(value) => {
        setFilter(value)
        setPage(1)
      }}
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
    onSuccess: async (_result, { user, role }) => {
      const auth = useAuthStore.getState().auth
      if (auth.user?.userId === user.id) {
        // PUT 已确认该角色，先关闭失权表单，再用服务端会话核验完整身份。
        auth.setUser({ ...auth.user, role: [role] })
        if (role !== 'administrator') setShowForm(false)
        const session = await api.get<SessionInfo>('/me')
        if (useAuthStore.getState().auth.accessToken !== auth.accessToken)
          return
        auth.setUser(sessionToUser(session))
        if (session.role !== 'administrator') {
          client.removeQueries({ queryKey: ['users'] })
          return
        }
      }
      await client.invalidateQueries({ queryKey: ['users'] })
    },
    onError: (error) => toast.error(error.message),
  })
  const columns = useMemo<ColumnDef<User>[]>(
    () => [
      {
        accessorKey: 'username',
        header: t('common.name'),
        size: 320,
        minSize: 200,
        maxSize: 320,
        cell: ({ row }: { row: { original: User } }) => (
          <a
            className='block truncate font-medium hover:underline'
            href={`/users/${row.original.id}`}
            title={row.original.username}
          >
            {row.original.username}
          </a>
        ),
      },
      {
        accessorKey: 'role',
        header: t('misc.role'),
        size: 160,
        minSize: 160,
        cell: ({ row }: { row: { original: User } }) => (
          <Select
            value={row.original.role}
            onValueChange={(role) => setUserRole({ user: row.original, role })}
          >
            <SelectTrigger
              className='h-8 w-36'
              aria-label={`${t('misc.role')}: ${row.original.username}`}
            >
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
        size: 170,
        minSize: 155,
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

export function UserDetail() {
  const { t } = useTranslation()
  const isAdministrator = useAuthStore(
    (state) => state.auth.user?.role.includes('administrator') ?? false
  )
  const id = window.location.pathname.split('/').pop() || ''
  const query = useQuery({
    queryKey: ['users'],
    queryFn: () => api.get<User[]>('/users'),
    enabled: isAdministrator,
  })
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
  if (query.isError && !query.data)
    return (
      <ErrorPage
        title={t('misc.usersTitle')}
        error={query.error}
        onRetry={() => query.refetch()}
        backTo='/users'
      />
    )
  if (!query.data) return <LoadingPage title={t('misc.usersTitle')} />
  const user = query.data.find((item) => item.id === id)
  if (!user)
    return (
      <ErrorPage
        title={t('misc.usersTitle')}
        error={
          new Error(
            t('errors.notFoundResource', { resource: t('errors.types.user') })
          )
        }
        backTo='/users'
      />
    )
  return (
    <>
      <NodeSteerHeader
        title={user.username}
        description={t(`misc.${user.role}`, { defaultValue: user.role })}
        backTo='/users'
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <SectionCard title={t('misc.usersTitle')}>
          <DetailGrid>
            <DetailField label={t('misc.username')}>
              {user.username}
            </DetailField>
            <DetailField label={t('misc.role')}>
              {t(`misc.${user.role}`, { defaultValue: user.role })}
            </DetailField>
            <DetailField label={t('misc.created')}>
              <TimeValue value={user.created_at} absolute />
            </DetailField>
            <DetailField label='ID'>
              <span
                className='block truncate font-mono text-xs'
                title={user.id}
              >
                {user.id}
              </span>
            </DetailField>
          </DetailGrid>
        </SectionCard>
      </Main>
    </>
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
        <div className='grid gap-8 lg:grid-cols-[200px_minmax(0,1fr)]'>
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
                <CardContent className='grid gap-4 sm:grid-cols-2'>
                  {query.isError ? (
                    <ErrorState
                      error={query.error}
                      onRetry={() => query.refetch()}
                    />
                  ) : query.isLoading ? (
                    <div className='h-8 animate-pulse rounded bg-muted' />
                  ) : !Object.keys(settings).length ? (
                    <EmptyState message={t('common.noData')} />
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
                      className='w-fit sm:col-span-2'
                      onClick={() => save.mutate()}
                      disabled={
                        save.isPending ||
                        query.isError ||
                        query.isLoading ||
                        !Object.keys(settings).length
                      }
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
  rackPrimaryColumnId,
  pagination,
  filter,
  onFilterChange,
  emptyMessage,
  enableRowSelection,
  renderBulkActions,
  bulkEntityName,
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
  /** rack 视图下作为行标题的列 id；缺省取第一个业务列。 */
  rackPrimaryColumnId?: string
  /** 空态文案；缺省使用通用「暂无数据」 */
  emptyMessage?: string
  /** 提供时启用服务端分页（页码/每页条数受控） */
  pagination?: {
    total: number
    page: number
    pageSize: number
    onPageChange: (page: number) => void
    onPageSizeChange: (pageSize: number) => void
  }
  filter?: string
  onFilterChange?: (value: string) => void
  /** 启用行多选（注入前置选择列） */
  enableRowSelection?: boolean
  /** 选中后浮出的批量操作工具条内容 */
  renderBulkActions?: (
    table: import('@tanstack/react-table').Table<TData>
  ) => React.ReactNode
  /** 工具条读屏文案中的实体名 */
  bulkEntityName?: string
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
          <LoadingState />
        ) : onFilterChange || data.length || (pagination?.total ?? 0) > 0 ? (
          <DataTable
            data={data}
            columns={columns}
            searchPlaceholder={searchPlaceholder}
            storageKey={storageKey}
            variant='rack'
            rackPrimaryColumnId={rackPrimaryColumnId}
            manualPagination={Boolean(pagination)}
            manualFiltering={Boolean(onFilterChange)}
            filter={filter}
            onFilterChange={onFilterChange}
            total={pagination?.total}
            page={pagination?.page}
            pageSize={pagination?.pageSize}
            onPageChange={pagination?.onPageChange}
            onPageSizeChange={pagination?.onPageSizeChange}
            enableRowSelection={enableRowSelection}
            renderBulkActions={renderBulkActions}
            bulkEntityName={bulkEntityName}
          />
        ) : (
          <Card>
            <EmptyState message={emptyMessage || t('common.noData')} />
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
