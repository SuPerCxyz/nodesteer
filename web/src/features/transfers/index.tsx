import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import type { ColumnDef } from '@tanstack/react-table'
import { Plus, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api, type FileTransfer, type Node } from '@/lib/api'
import { useCanWrite } from '@/lib/permissions'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { DataTable } from '@/features/shared/data-table'
import {
  DropdownMenuItem,
  EmptyState,
  ErrorState,
  LoadingState,
  MoreMenu,
  StatusBadge,
  TimeValue,
} from '@/features/shared/ui'

type TargetDraft = { node_id: string; destination_path: string }

export function FileTransfers() {
  const { t } = useTranslation()
  const canWrite = useCanWrite()
  const client = useQueryClient()
  const [sourceNodeID, setSourceNodeID] = useState('')
  const [sourcePath, setSourcePath] = useState('')
  const [targetNodeID, setTargetNodeID] = useState('')
  const [destinationPath, setDestinationPath] = useState('')
  const [targets, setTargets] = useState<TargetDraft[]>([])
  const nodes = useQuery({
    queryKey: ['nodes'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const transfers = useQuery({
    queryKey: ['transfers'],
    queryFn: () => api.get<FileTransfer[]>('/transfers'),
    refetchInterval: 3000,
  })
  const create = useMutation({
    mutationFn: () =>
      api.post<FileTransfer>('/transfers', {
        source_node_id: sourceNodeID,
        source_path: sourcePath,
        targets,
      }),
    onSuccess: () => {
      setSourcePath('')
      setTargets([])
      setTargetNodeID('')
      setDestinationPath('')
      client.invalidateQueries({ queryKey: ['transfers'] })
      toast.success(t('transfers.created'))
    },
    onError: (error) => toast.error(error.message),
  })
  const { mutate: retryTransfer } = useMutation({
    mutationFn: (id: string) => api.post(`/transfers/${id}/retry`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['transfers'] }),
    onError: (error) => toast.error(error.message),
  })
  const { mutate: cancelTransfer } = useMutation({
    mutationFn: (id: string) => api.post(`/transfers/${id}/cancel`),
    onSuccess: () => client.invalidateQueries({ queryKey: ['transfers'] }),
    onError: (error) => toast.error(error.message),
  })
  const nodeNames = useMemo(
    () => new Map((nodes.data || []).map((node) => [node.id, node.hostname])),
    [nodes.data]
  )
  const columns = useMemo<ColumnDef<FileTransfer>[]>(
    () => [
      {
        id: 'source',
        accessorFn: (row: FileTransfer) =>
          `${nodeNames.get(row.source_node_id) || ''} ${row.id}`,
        header: t('transfers.source'),
        size: 130,
        minSize: 108,
        cell: ({ row }: { row: { original: FileTransfer } }) =>
          nodeNames.get(row.original.source_node_id) || t('common.unknownNode'),
      },
      {
        accessorKey: 'source_path',
        header: t('transfers.sourcePath'),
        size: 310,
        minSize: 170,
        maxSize: 470,
        cell: ({ row }: { row: { original: FileTransfer } }) => (
          <span
            className='block truncate font-mono text-xs'
            title={row.original.source_path}
          >
            {row.original.source_path}
          </span>
        ),
      },
      {
        accessorKey: 'status',
        header: t('common.status'),
        size: 104,
        minSize: 96,
        cell: ({ row }: { row: { original: FileTransfer } }) => (
          <StatusBadge status={row.original.status} />
        ),
      },
      {
        id: 'error',
        accessorFn: (row: FileTransfer) =>
          `${row.error || ''} ${row.targets
            .map((target) => target.error || '')
            .join(' ')}`,
        header: t('transfers.errorReason'),
        size: 280,
        minSize: 170,
        maxSize: 460,
        cell: ({ row }: { row: { original: FileTransfer } }) =>
          row.original.error ? (
            <span
              className='block truncate text-xs text-destructive'
              title={row.original.error}
            >
              {row.original.error}
            </span>
          ) : (
            <span className='text-muted-foreground'>-</span>
          ),
      },
      {
        id: 'targets',
        header: t('transfers.targets'),
        size: 255,
        minSize: 160,
        cell: ({ row }: { row: { original: FileTransfer } }) => (
          <div className='flex flex-wrap gap-x-3 gap-y-2'>
            {row.original.targets.map((target) => (
              <span
                key={target.node_id}
                className='flex max-w-full min-w-0 flex-col gap-1'
              >
                <span className='inline-flex max-w-full min-w-0 items-center gap-1'>
                  <span
                    className='max-w-40 truncate text-xs'
                    title={
                      nodeNames.get(target.node_id) || t('common.unknownNode')
                    }
                  >
                    {nodeNames.get(target.node_id) || t('common.unknownNode')}
                  </span>
                  <StatusBadge status={target.status} />
                </span>
                {target.error ? (
                  <span
                    className='max-w-full truncate text-xs text-destructive'
                    title={target.error}
                  >
                    {target.error}
                  </span>
                ) : null}
              </span>
            ))}
          </div>
        ),
      },
      {
        accessorKey: 'updated_at',
        header: t('common.time'),
        size: 175,
        minSize: 120,
        cell: ({ row }: { row: { original: FileTransfer } }) => (
          <TimeValue value={row.original.updated_at} />
        ),
      },
      {
        id: 'actions',
        header: '',
        enableHiding: false,
        size: 72,
        minSize: 64,
        cell: ({ row }: { row: { original: FileTransfer } }) => {
          const canRetry = row.original.status === 'FAILED'
          const canCancel = !['SUCCESS', 'CANCELED'].includes(
            row.original.status
          )
          // 终态且无可用操作时不渲染空菜单（缺陷 FAIL-A-012）
          if (!canWrite || (!canRetry && !canCancel)) return null
          return (
            <MoreMenu>
              {canRetry && (
                <DropdownMenuItem
                  onSelect={() => retryTransfer(row.original.id)}
                >
                  {t('transfers.retry')}
                </DropdownMenuItem>
              )}
              {canCancel && (
                <DropdownMenuItem
                  onSelect={() => cancelTransfer(row.original.id)}
                >
                  {t('transfers.cancel')}
                </DropdownMenuItem>
              )}
            </MoreMenu>
          )
        },
      },
    ],
    [canWrite, cancelTransfer, nodeNames, retryTransfer, t]
  )
  const addTarget = () => {
    if (
      !targetNodeID ||
      !destinationPath ||
      targets.some((target) => target.node_id === targetNodeID)
    )
      return
    setTargets((current) => [
      ...current,
      { node_id: targetNodeID, destination_path: destinationPath },
    ])
    setTargetNodeID('')
    setDestinationPath('')
  }
  const source = nodes.data?.find((node) => node.id === sourceNodeID)
  return (
    <>
      <NodeSteerHeader
        title={t('transfers.title')}
        description={t('transfers.description')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        {canWrite ? (
          <Card className='w-full'>
            <CardHeader>
              <CardTitle className='text-sm'>
                {t('transfers.newTransfer')}
              </CardTitle>
            </CardHeader>
            <CardContent className='space-y-4'>
              <div className='grid gap-4 md:grid-cols-[minmax(180px,1fr)_minmax(0,2fr)_108px]'>
                <label className='grid gap-2 text-sm'>
                  {t('transfers.source')}
                  <Select value={sourceNodeID} onValueChange={setSourceNodeID}>
                    <SelectTrigger>
                      <SelectValue placeholder={t('transfers.selectSource')} />
                    </SelectTrigger>
                    <SelectContent>
                      {(nodes.data || []).map((node) => (
                        <SelectItem key={node.id} value={node.id}>
                          {node.hostname}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </label>
                <label className='grid gap-2 text-sm'>
                  {t('transfers.sourcePath')}
                  <Input
                    value={sourcePath}
                    onChange={(event) => setSourcePath(event.target.value)}
                    placeholder='/var/lib/app/config.yaml'
                    className='font-mono'
                  />
                </label>
                <div className='hidden md:block' aria-hidden='true' />
              </div>
              <div className='grid gap-4 md:grid-cols-[minmax(180px,1fr)_minmax(0,2fr)_108px]'>
                <label className='grid gap-2 text-sm'>
                  {t('transfers.target')}
                  <Select value={targetNodeID} onValueChange={setTargetNodeID}>
                    <SelectTrigger>
                      <SelectValue placeholder={t('transfers.selectTarget')} />
                    </SelectTrigger>
                    <SelectContent>
                      {(nodes.data || [])
                        .filter((node) => node.id !== sourceNodeID)
                        .map((node) => (
                          <SelectItem key={node.id} value={node.id}>
                            {node.hostname}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </label>
                <label className='grid gap-2 text-sm'>
                  {t('transfers.destinationPath')}
                  <Input
                    value={destinationPath}
                    onChange={(event) => setDestinationPath(event.target.value)}
                    placeholder='/var/lib/app/config.yaml'
                    className='font-mono'
                  />
                </label>
                <Button
                  type='button'
                  variant='outline'
                  className='w-full self-end'
                  onClick={addTarget}
                  disabled={!targetNodeID || !destinationPath}
                >
                  <Plus className='size-4' />
                  {t('transfers.addTarget')}
                </Button>
              </div>
              {targets.length > 0 && (
                <div className='grid gap-2 text-sm'>
                  {targets.map((target) => (
                    <div
                      key={target.node_id}
                      className='flex items-center justify-between rounded-md border px-3 py-2'
                    >
                      <span>
                        <span className='font-medium'>
                          {nodeNames.get(target.node_id) ||
                            t('common.unknownNode')}
                        </span>
                        <span className='ms-3 font-mono text-xs text-muted-foreground'>
                          {target.destination_path}
                        </span>
                      </span>
                      <Button
                        type='button'
                        variant='ghost'
                        size='icon'
                        className='size-8'
                        onClick={() =>
                          setTargets((current) =>
                            current.filter(
                              (item) => item.node_id !== target.node_id
                            )
                          )
                        }
                        aria-label={t('common.remove')}
                      >
                        <X className='size-4' />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
              <Button
                onClick={() => create.mutate()}
                disabled={
                  !source ||
                  !sourcePath ||
                  targets.length === 0 ||
                  create.isPending
                }
              >
                {create.isPending
                  ? t('transfers.creating')
                  : t('transfers.start')}
              </Button>
            </CardContent>
          </Card>
        ) : null}
        {transfers.isError ? (
          <ErrorState
            error={transfers.error}
            onRetry={() => transfers.refetch()}
          />
        ) : transfers.isLoading ? (
          <LoadingState />
        ) : transfers.data?.length ? (
          <DataTable
            data={transfers.data}
            columns={columns}
            storageKey='transfers'
            searchPlaceholder={t('transfers.searchPlaceholder')}
          />
        ) : (
          <Card>
            <EmptyState message={t('transfers.empty')} />
          </Card>
        )}
      </Main>
    </>
  )
}
