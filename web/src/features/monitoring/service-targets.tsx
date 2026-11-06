import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api, type Node } from '@/lib/api'
import { nodeDisplayName } from '@/lib/node-display-name'
import { Button } from '@/components/ui/button'
import { CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { ConfirmDialog } from '@/components/confirm-dialog'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { EditorPanel, useEditorPanel } from '@/features/shared/editor-panel'
import { RackList, RackRow } from '@/features/shared/rack-list'
import {
  DangerMenuItem,
  DropdownMenuItem,
  EmptyState,
  ErrorState,
  ListCard,
  ListCardBody,
  LoadingState,
  MoreMenu,
  SectionCard,
  StateCard,
} from '@/features/shared/ui'
import { ObservationState } from './observation-state'
import type { MonitoringObservation, MonitoringTarget } from './types'

function emptyTarget(): MonitoringTarget {
  return {
    id: '',
    name: '',
    url: '',
    json_path: [],
    interval_sec: 60,
    timeout_sec: 5,
    enabled: true,
  }
}

export function ServiceTargets() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const [target, setTarget] = useState<MonitoringTarget>(emptyTarget)
  const [path, setPath] = useState('')
  const [queryParams, setQueryParams] = useState('')
  const [selected, setSelected] = useState('')
  const editor = useEditorPanel({ target, path, queryParams })
  const [confirmation, setConfirmation] = useState<{
    message: string
    run: () => Promise<unknown>
  } | null>(null)
  const targets = useQuery({
    queryKey: ['monitoring-targets'],
    queryFn: () => api.get<MonitoringTarget[]>('/monitoring/http-targets'),
    refetchInterval: 15000,
  })
  const latest = useQuery({
    queryKey: ['monitoring-latest'],
    queryFn: async () => {
      const result: Record<string, MonitoringObservation> = {}
      let after = ''
      do {
        const query = new URLSearchParams({ limit: '1000', after })
        const page = await api.get<{
          items: (MonitoringObservation & { target_id: string })[]
          next: string
        }>(`/monitoring/latest?${query}`)
        for (const item of page.items) result[item.target_id] = item
        after = page.next
      } while (after)
      return result
    },
    refetchInterval: 15000,
  })
  const probeNodes = useQuery({
    queryKey: ['monitoring-node-list'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const observations = useQuery({
    queryKey: ['monitoring-observations', selected],
    queryFn: () =>
      api.get<MonitoringObservation[]>(
        `/monitoring/http-targets/${encodeURIComponent(selected)}/observations`
      ),
    enabled: !!selected,
    refetchInterval: 15000,
  })
  const action = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => fn(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['monitoring-targets'] })
      void qc.invalidateQueries({ queryKey: ['monitoring-latest'] })
      void qc.invalidateQueries({ queryKey: ['monitoring-observations'] })
      toast.success(t('monitoring.saved'))
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const editTarget = (item: MonitoringTarget) => {
    setTarget(item)
    setPath((item.json_path || []).join('.'))
    setQueryParams(JSON.stringify(item.query_params || [], null, 2))
  }
  const saveTarget = () => {
    try {
      const params: unknown = queryParams.trim() ? JSON.parse(queryParams) : []
      if (!Array.isArray(params))
        throw new Error(t('monitoring.queryParametersMustBeAnArray'))
      const value = {
        ...target,
        max_age_sec:
          target.kind === 'node_metric'
            ? target.max_age_sec || target.interval_sec * 2
            : target.max_age_sec,
        json_path: path ? path.split('.') : [],
        query_params: params,
      }
      action.mutate(
        () =>
          target.id
            ? api.put(`/monitoring/http-targets/${target.id}`, value)
            : api.post('/monitoring/http-targets', value),
        {
          onSuccess: (result) => {
            const saved = result as MonitoringTarget
            setTarget(saved)
            editor.saved({ target: saved, path, queryParams })
            setSelected(saved.id)
          },
        }
      )
    } catch (error) {
      toast.error(error instanceof Error ? error.message : String(error))
    }
  }
  const openCreate = () =>
    editor.change({ target: emptyTarget(), path: '', queryParams: '' }, () => {
      setTarget(emptyTarget())
      setPath('')
      setQueryParams('')
    })
  if (targets.isLoading || latest.isLoading || probeNodes.isLoading) {
    return (
      <>
        <NodeSteerHeader
          title={t('monitoring.httpServices')}
          description={t('monitoring.serviceMonitoringDescription')}
        />
        <Main>
          <StateCard>
            <LoadingState />
          </StateCard>
        </Main>
      </>
    )
  }
  if (targets.isError || latest.isError || probeNodes.isError) {
    return (
      <>
        <NodeSteerHeader
          title={t('monitoring.httpServices')}
          description={t('monitoring.serviceMonitoringDescription')}
        />
        <Main>
          <StateCard>
            <ErrorState
              error={targets.error || latest.error || probeNodes.error}
              onRetry={() => {
                void targets.refetch()
                void latest.refetch()
                void probeNodes.refetch()
              }}
            />
          </StateCard>
        </Main>
      </>
    )
  }
  return (
    <>
      <NodeSteerHeader
        title={t('monitoring.httpServices')}
        description={t('monitoring.serviceMonitoringDescription')}
        action={
          <Button
            variant='header-action'
            disabled={action.isPending}
            onClick={openCreate}
          >
            <Plus className='me-1 size-4' />
            {t('monitoring.createTarget')}
          </Button>
        }
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <ListCard data-testid='service-targets-list'>
          <CardHeader className='px-4 pt-4 pb-1 sm:px-6'>
            <CardTitle>{t('monitoring.serviceTargets')}</CardTitle>
          </CardHeader>
          <ListCardBody className='space-y-4 pt-2'>
            {!targets.data?.length ? (
              <EmptyState message={t('monitoring.noTargets')} />
            ) : (
              <RackList testId='monitoring-targets-rack'>
                {(targets.data || []).map((item) => {
                  const location =
                    item.kind === 'node_metric'
                      ? `${nodeDisplayName(
                          probeNodes.data?.find(
                            (node) => node.id === item.monitored_node_id
                          ) ?? { id: item.monitored_node_id },
                          t('common.unknownNode')
                        )} · ${item.metric || '—'}`
                      : item.url
                  return (
                    <RackRow
                      key={item.id}
                      testId={`monitoring-target-row-${item.id}`}
                      title={<span title={item.name}>{item.name}</span>}
                      subtitle={
                        <span className='truncate' title={location}>
                          {location}
                        </span>
                      }
                      fields={[
                        {
                          label: t('monitoring.state'),
                          value: (
                            <ObservationState
                              target={item}
                              observation={latest.data?.[item.id]}
                              error={latest.isError}
                              now={now}
                            />
                          ),
                        },
                      ]}
                      actions={
                        <>
                          <Button
                            variant='outline'
                            size='sm'
                            disabled={action.isPending}
                            onClick={() => {
                              editor.change(
                                {
                                  target: item,
                                  path: (item.json_path || []).join('.'),
                                  queryParams: JSON.stringify(
                                    item.query_params || [],
                                    null,
                                    2
                                  ),
                                },
                                () => {
                                  setSelected(item.id)
                                  editTarget(item)
                                }
                              )
                            }}
                          >
                            {t('monitoring.viewEdit')}
                          </Button>
                          <MoreMenu>
                            <DropdownMenuItem
                              disabled={action.isPending}
                              onSelect={() => {
                                setSelected(item.id)
                                window.setTimeout(() => {
                                  const history =
                                    document.getElementById('service-history')
                                  history?.scrollIntoView({ block: 'start' })
                                  history?.focus({ preventScroll: true })
                                }, 0)
                              }}
                            >
                              {t('webFix.history')}
                            </DropdownMenuItem>
                            <DangerMenuItem
                              label={t('monitoring.delete')}
                              title={t('monitoring.deleteTargetAndItsHistory')}
                              description={t('common.confirmAction')}
                              confirmText={t('monitoring.confirmAction')}
                              onSelect={() => {
                                void api
                                  .del(`/monitoring/http-targets/${item.id}`)
                                  .then((result) => {
                                    if (selected === item.id) setSelected('')
                                    if (target.id === item.id) {
                                      setTarget(emptyTarget())
                                      editor.removed()
                                    }
                                    return result
                                  })
                              }}
                            />
                          </MoreMenu>
                        </>
                      }
                    />
                  )
                })}
              </RackList>
            )}
          </ListCardBody>
        </ListCard>
        {editor.open && (
          <EditorPanel
            title={`${target.id ? t('webFix.edit') : t('webFix.new')}: ${target.name || t('monitoring.serviceTargets')}`}
            onClose={editor.close}
            busy={action.isPending}
          >
            <div className='grid gap-3 md:grid-cols-2 [&>div]:min-w-0'>
              <div>
                <Label htmlFor='monitor-kind'>{t('monitoring.kind')}</Label>
                <Select
                  value={target.kind || 'http'}
                  onValueChange={(value) => {
                    setTarget({
                      ...target,
                      kind: value as 'http' | 'tcp' | 'icmp' | 'node_metric',
                      url: '',
                      credential_env: '',
                      json_path: [],
                      query_params: [],
                      source_node_id: '',
                      monitored_node_id: '',
                      metric: value === 'node_metric' ? 'cpu.usage' : '',
                      series: '',
                    })
                    setPath('')
                    setQueryParams('')
                  }}
                >
                  <SelectTrigger id='monitor-kind' className='w-full'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='http'>HTTP / HTTPS</SelectItem>
                    <SelectItem value='tcp'>TCP</SelectItem>
                    <SelectItem value='icmp'>ICMP</SelectItem>
                    <SelectItem value='node_metric'>
                      {t('monitoring.nodeMetric')}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {target.kind !== 'node_metric' && (
                <div>
                  <Label htmlFor='monitor-source'>
                    {t('monitoring.probeSource')}
                  </Label>
                  <Select
                    value={target.source_node_id || '__hub__'}
                    onValueChange={(value) =>
                      setTarget({
                        ...target,
                        source_node_id: value === '__hub__' ? '' : value,
                      })
                    }
                  >
                    <SelectTrigger id='monitor-source' className='w-full'>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value='__hub__'>Hub</SelectItem>
                      {(probeNodes.data || []).map((item) => (
                        <SelectItem key={item.id} value={item.id}>
                          {nodeDisplayName(item, t('common.unknownNode'))} (
                          {item.status})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
              <div>
                <Label htmlFor='monitor-name'>{t('monitoring.name')}</Label>
                <Input
                  id='monitor-name'
                  value={target.name}
                  onChange={(e) =>
                    setTarget({ ...target, name: e.target.value })
                  }
                />
              </div>
              {target.kind !== 'node_metric' && (
                <div>
                  <Label htmlFor='monitor-url'>URL</Label>
                  <Input
                    id='monitor-url'
                    value={target.url}
                    placeholder={
                      target.kind === 'tcp'
                        ? 'tcp://host:443'
                        : target.kind === 'icmp'
                          ? 'icmp://host'
                          : 'https://example.com/health'
                    }
                    onChange={(e) =>
                      setTarget({ ...target, url: e.target.value })
                    }
                  />
                </div>
              )}
              {target.kind === 'node_metric' && (
                <>
                  <div>
                    <Label htmlFor='monitor-node'>{t('monitoring.node')}</Label>
                    <Select
                      value={target.monitored_node_id || ''}
                      onValueChange={(value) =>
                        setTarget({ ...target, monitored_node_id: value })
                      }
                    >
                      <SelectTrigger id='monitor-node' className='w-full'>
                        <SelectValue placeholder='—' />
                      </SelectTrigger>
                      <SelectContent>
                        {(probeNodes.data || []).map((item) => (
                          <SelectItem key={item.id} value={item.id}>
                            {nodeDisplayName(item, t('common.unknownNode'))} (
                            {item.status})
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div>
                    <Label htmlFor='monitor-metric'>
                      {t('monitoring.metricName')}
                    </Label>
                    <Input
                      id='monitor-metric'
                      value={target.metric || ''}
                      placeholder='cpu.usage'
                      onChange={(e) =>
                        setTarget({ ...target, metric: e.target.value })
                      }
                    />
                  </div>
                  <div>
                    <Label htmlFor='monitor-series'>
                      {t('monitoring.series')}
                    </Label>
                    <Input
                      id='monitor-series'
                      value={target.series || ''}
                      onChange={(e) =>
                        setTarget({ ...target, series: e.target.value })
                      }
                    />
                  </div>
                </>
              )}
              {(target.kind === undefined || target.kind === 'http') && (
                <>
                  <div>
                    <Label htmlFor='monitor-path'>
                      {t('monitoring.jsonFieldPathDotSeparated')}
                    </Label>
                    <Input
                      id='monitor-path'
                      value={path}
                      onChange={(e) => setPath(e.target.value)}
                    />
                  </div>
                  <div>
                    <Label htmlFor='monitor-secret'>
                      {t('monitoring.bearerCredentialEnvironmentVariable')}
                    </Label>
                    <Input
                      id='monitor-secret'
                      value={target.credential_env || ''}
                      placeholder='NODESTEER_MONITOR_SECRET_...'
                      onChange={(e) =>
                        setTarget({
                          ...target,
                          credential_env: e.target.value,
                        })
                      }
                    />
                  </div>
                </>
              )}
              <div>
                <Label htmlFor='monitor-interval'>
                  {t('monitoring.intervalSeconds')}
                </Label>
                <Input
                  id='monitor-interval'
                  type='number'
                  value={target.interval_sec}
                  onChange={(e) =>
                    setTarget({
                      ...target,
                      interval_sec: Number(e.target.value),
                    })
                  }
                />
              </div>
              <div>
                <Label htmlFor='monitor-timeout'>
                  {t('monitoring.timeoutSeconds')}
                </Label>
                <Input
                  id='monitor-timeout'
                  type='number'
                  value={target.timeout_sec}
                  onChange={(e) =>
                    setTarget({
                      ...target,
                      timeout_sec: Number(e.target.value),
                    })
                  }
                />
              </div>
              {target.kind === 'node_metric' && (
                <div>
                  <Label htmlFor='monitor-max-age'>
                    {t('monitoring.maxAge')}
                  </Label>
                  <Input
                    id='monitor-max-age'
                    type='number'
                    min={1}
                    max={300}
                    value={target.max_age_sec || target.interval_sec * 2}
                    onChange={(event) =>
                      setTarget({
                        ...target,
                        max_age_sec: Number(event.target.value),
                      })
                    }
                  />
                  <p className='text-xs text-muted-foreground'>
                    {t('monitoring.maxAgeHelp')}
                  </p>
                </div>
              )}
              {(target.kind === undefined || target.kind === 'http') && (
                <div className='md:col-span-2'>
                  <Label htmlFor='monitor-query'>
                    {t('monitoring.queryParametersJsonNameValueOrSecretEnv')}
                  </Label>
                  <Textarea
                    id='monitor-query'
                    className='w-full rounded-md border border-input bg-background p-2 text-sm'
                    rows={3}
                    value={queryParams}
                    onChange={(e) => setQueryParams(e.target.value)}
                    placeholder='[{"name":"region","value":"north"}]'
                  />
                </div>
              )}
            </div>
            <label className='flex items-center gap-2 text-sm'>
              <Checkbox
                checked={target.enabled}
                onCheckedChange={(checked) =>
                  setTarget({ ...target, enabled: checked === true })
                }
              />
              {t('monitoring.enableCollection')}
            </label>
            <div className='flex gap-2'>
              <Button onClick={saveTarget} disabled={action.isPending}>
                {target.id
                  ? t('monitoring.saveTarget')
                  : t('monitoring.createTarget')}
              </Button>
              <Button
                variant='outline'
                onClick={() => {
                  editor.change(
                    { target: emptyTarget(), path: '', queryParams: '' },
                    () => {
                      setTarget(emptyTarget())
                      setPath('')
                      setQueryParams('')
                    }
                  )
                }}
              >
                {t('monitoring.new')}
              </Button>
            </div>
          </EditorPanel>
        )}
        {editor.confirmation}
        {selected && (
          <div id='service-history' tabIndex={-1} className='scroll-mt-24'>
            <SectionCard title={t('monitoring.recentObservations')}>
              <p className='mb-4 text-sm text-muted-foreground'>
                {targets.data?.find((item) => item.id === selected)?.name}
              </p>
              {observations.isError ? (
                <ErrorState
                  error={observations.error}
                  onRetry={() => observations.refetch()}
                />
              ) : !observations.data?.length ? (
                <EmptyState message={t('common.noData')} />
              ) : (
                <RackList testId='monitoring-observations-rack'>
                  {observations.data.map((item) => {
                    const valueText =
                      item.error ||
                      (item.value === null ? '—' : JSON.stringify(item.value))
                    return (
                      <RackRow
                        key={item.id}
                        testId={`monitoring-observation-row-${item.id}`}
                        title={new Date(item.observed_at).toLocaleString()}
                        fields={[
                          {
                            label: t('monitoring.duration'),
                            value:
                              typeof item.duration_ns === 'number'
                                ? `${(item.duration_ns / 1e6).toFixed(1)} ms`
                                : '—',
                            mono: true,
                          },
                          {
                            label: 'HTTP',
                            value: item.status_code || '—',
                            mono: true,
                          },
                          {
                            label: t('monitoring.valueError'),
                            value: <span title={valueText}>{valueText}</span>,
                          },
                        ]}
                      />
                    )
                  })}
                </RackList>
              )}
            </SectionCard>
          </div>
        )}
      </Main>
      <ConfirmDialog
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmation(null)
        }}
        title={t('monitoring.confirmTitle')}
        cancelBtnText={t('common.cancel')}
        confirmText={t('monitoring.confirmAction')}
        desc={confirmation?.message || ''}
        destructive
        isLoading={action.isPending}
        handleConfirm={() => {
          if (confirmation) action.mutate(confirmation.run)
          setConfirmation(null)
        }}
      />
    </>
  )
}
