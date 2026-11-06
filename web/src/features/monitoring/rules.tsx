import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api, type Schedule } from '@/lib/api'
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
  StatusBadge,
} from '@/features/shared/ui'
import type {
  MonitoringRule,
  MonitoringRuleEvent,
  MonitoringTarget,
} from './types'

export function AutomationRules() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const emptyRule = (): MonitoringRule => ({
    id: '',
    name: '',
    target_id: '',
    schedule_id: '',
    operator: 'status_eq',
    expected: '503',
    duration_sec: 0,
    cooldown_sec: 300,
    enabled: false,
  })
  const [rule, setRule] = useState<MonitoringRule>(emptyRule)
  const editor = useEditorPanel(rule)
  const [selectedRule, setSelectedRule] = useState('')
  const [confirmation, setConfirmation] = useState<{
    message: string
    run: () => Promise<unknown>
    destructive?: boolean
    manual?: boolean
    abandon?: boolean
  } | null>(null)
  const targets = useQuery({
    queryKey: ['monitoring-targets'],
    queryFn: () => api.get<MonitoringTarget[]>('/monitoring/http-targets'),
    refetchInterval: 15000,
  })
  const rules = useQuery({
    queryKey: ['monitoring-rules'],
    queryFn: () => api.get<MonitoringRule[]>('/monitoring/rules'),
    refetchInterval: 15000,
  })
  const schedules = useQuery({
    queryKey: ['monitoring-schedules'],
    queryFn: () => api.get<Schedule[]>('/schedules'),
  })
  const events = useQuery({
    queryKey: ['monitoring-events', selectedRule],
    queryFn: () =>
      api.get<MonitoringRuleEvent[]>(
        `/monitoring/rules/${encodeURIComponent(selectedRule)}/events`
      ),
    enabled: !!selectedRule,
    refetchInterval: 15000,
  })
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ['monitoring-rules'] })
    void qc.invalidateQueries({ queryKey: ['monitoring-events'] })
  }
  const action = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => fn(),
    onSuccess: () => {
      refresh()
      toast.success(t('monitoring.saved'))
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const manualAction = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => fn(),
    onSuccess: () => toast.success(t('monitoring.taskSubmitted')),
    onError: (error: Error) => toast.error(error.message),
  })
  const abandonAction = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => fn(),
    onSuccess: () => {
      refresh()
      toast.success(t('monitoring.abandoned'))
    },
    onError: (error: Error) => toast.error(error.message),
  })
  const saveRule = () =>
    action.mutate(
      () =>
        rule.id
          ? api.put(`/monitoring/rules/${rule.id}`, rule)
          : api.post('/monitoring/rules', rule),
      {
        onSuccess: (result) => {
          const saved = result as MonitoringRule
          setRule(saved)
          editor.saved(saved)
          setSelectedRule(saved.id)
        },
      }
    )
  if (targets.isLoading || rules.isLoading || schedules.isLoading)
    return (
      <>
        <NodeSteerHeader title={t('rules.title')} />
        <Main>
          <StateCard>
            <LoadingState />
          </StateCard>
        </Main>
      </>
    )
  if (targets.isError || rules.isError || schedules.isError)
    return (
      <>
        <NodeSteerHeader title={t('rules.title')} />
        <Main>
          <StateCard>
            <ErrorState
              error={targets.error || rules.error || schedules.error}
              onRetry={() => {
                void targets.refetch()
                void rules.refetch()
                void schedules.refetch()
              }}
            />
          </StateCard>
        </Main>
      </>
    )
  return (
    <>
      <NodeSteerHeader
        title={t('rules.title')}
        description={t('rules.description')}
        action={
          <Button
            variant='header-action'
            disabled={action.isPending}
            onClick={() =>
              editor.change(emptyRule(), () => setRule(emptyRule()))
            }
          >
            <Plus className='me-1 size-4' />
            {t('monitoring.createRule')}
          </Button>
        }
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <ListCard data-testid='rules-list'>
          <CardHeader className='px-4 pt-4 pb-1 sm:px-6'>
            <CardTitle>{t('rules.listTitle')}</CardTitle>
          </CardHeader>
          <ListCardBody className='space-y-4 pt-2'>
            {!rules.data?.length ? (
              <EmptyState message={t('rules.noRules')} />
            ) : (
              <RackList testId='monitoring-rules-rack'>
                {(rules.data || []).map((item) => (
                  <RackRow
                    key={item.id}
                    testId={`monitoring-rule-row-${item.id}`}
                    title={<span title={item.name}>{item.name}</span>}
                    fields={[
                      {
                        label: t('monitoring.target'),
                        value:
                          targets.data?.find(
                            (target) => target.id === item.target_id
                          )?.name || '—',
                      },
                      {
                        label: t('monitoring.schedule'),
                        value:
                          schedules.data?.find(
                            (schedule) => schedule.id === item.schedule_id
                          )?.name || item.schedule_id,
                      },
                      {
                        label: t('monitoring.condition'),
                        value: `${item.operator} ${item.expected}`,
                      },
                      {
                        label: t('monitoring.state'),
                        value: (
                          <StatusBadge
                            status={item.enabled ? 'enabled' : 'disabled'}
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
                            editor.change(item, () => {
                              setRule(item)
                              setSelectedRule(item.id)
                            })
                          }}
                        >
                          {t('monitoring.viewEdit')}
                        </Button>
                        <Button
                          variant='outline'
                          size='sm'
                          disabled={action.isPending}
                          onClick={() => {
                            const schedule = schedules.data?.find(
                              (entry) => entry.id === item.schedule_id
                            )
                            if (!schedule) {
                              toast.error(t('monitoring.scheduleNotFound'))
                              return
                            }
                            setConfirmation({
                              message: t(
                                'monitoring.runTheAssociatedNodeTaskNow'
                              ),
                              manual: true,
                              run: () =>
                                api.post(`/tasks/${schedule.task_id}/run`, {
                                  schedule_id: schedule.id,
                                }),
                            })
                          }}
                        >
                          {t('monitoring.runNow')}
                        </Button>
                        <MoreMenu>
                          <DropdownMenuItem
                            disabled={action.isPending}
                            onSelect={() => {
                              setSelectedRule(item.id)
                              window.setTimeout(() => {
                                const history =
                                  document.getElementById('rule-history')
                                history?.scrollIntoView({ block: 'start' })
                                history?.focus({ preventScroll: true })
                              }, 0)
                            }}
                          >
                            {t('webFix.history')}
                          </DropdownMenuItem>
                          <DangerMenuItem
                            label={t('monitoring.delete')}
                            title={t('monitoring.deleteRule')}
                            description={t('common.confirmAction')}
                            confirmText={t('monitoring.confirmAction')}
                            onSelect={() => {
                              void api
                                .del(`/monitoring/rules/${item.id}`)
                                .then((result) => {
                                  if (selectedRule === item.id)
                                    setSelectedRule('')
                                  if (rule.id === item.id) {
                                    setRule(emptyRule())
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
                ))}
              </RackList>
            )}
          </ListCardBody>
        </ListCard>
        {editor.open && (
          <EditorPanel
            title={`${rule.id ? t('webFix.edit') : t('webFix.new')}: ${rule.name || t('rules.title')}`}
            onClose={editor.close}
            busy={action.isPending}
          >
            <div className='grid gap-3 md:grid-cols-2'>
              <div>
                <Label htmlFor='rule-name'>{t('monitoring.name')}</Label>
                <Input
                  id='rule-name'
                  value={rule.name}
                  onChange={(e) => setRule({ ...rule, name: e.target.value })}
                />
              </div>
              <div>
                <Label htmlFor='rule-target'>{t('monitoring.target')}</Label>
                <Select
                  value={rule.target_id}
                  onValueChange={(value) =>
                    setRule({ ...rule, target_id: value })
                  }
                >
                  <SelectTrigger id='rule-target' className='w-full'>
                    <SelectValue placeholder='—' />
                  </SelectTrigger>
                  <SelectContent>
                    {(targets.data || []).map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor='rule-schedule'>
                  {t('monitoring.schedule')}
                </Label>
                <Select
                  value={rule.schedule_id}
                  onValueChange={(value) =>
                    setRule({ ...rule, schedule_id: value })
                  }
                >
                  <SelectTrigger id='rule-schedule' className='w-full'>
                    <SelectValue placeholder='—' />
                  </SelectTrigger>
                  <SelectContent>
                    {(schedules.data || []).map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor='rule-op'>{t('monitoring.condition')}</Label>
                <Select
                  value={rule.operator}
                  onValueChange={(value) =>
                    setRule({ ...rule, operator: value })
                  }
                >
                  <SelectTrigger id='rule-op' className='w-full'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='status_eq'>
                      {t('monitoring.httpStatusEquals')}
                    </SelectItem>
                    <SelectItem value='eq'>
                      {t('monitoring.valueEquals')}
                    </SelectItem>
                    <SelectItem value='lt'>
                      {t('monitoring.valueBelow')}
                    </SelectItem>
                    <SelectItem value='gt'>
                      {t('monitoring.valueAbove')}
                    </SelectItem>
                    <SelectItem value='expires_within'>
                      {t('monitoring.expiresWithin')}
                    </SelectItem>
                    <SelectItem value='probe_error'>
                      {t('monitoring.networkUnreachable')}
                    </SelectItem>
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor='rule-expected'>
                  {t('monitoring.comparisonExpiryExample24h')}
                </Label>
                <Input
                  id='rule-expected'
                  value={rule.expected}
                  onChange={(e) =>
                    setRule({ ...rule, expected: e.target.value })
                  }
                />
              </div>
              <div>
                <Label htmlFor='rule-duration'>
                  {t('monitoring.durationSeconds')}
                </Label>
                <Input
                  id='rule-duration'
                  type='number'
                  value={rule.duration_sec}
                  onChange={(e) =>
                    setRule({
                      ...rule,
                      duration_sec: Number(e.target.value),
                    })
                  }
                />
              </div>
              <div>
                <Label htmlFor='rule-cooldown'>
                  {t('monitoring.cooldownSeconds')}
                </Label>
                <Input
                  id='rule-cooldown'
                  type='number'
                  value={rule.cooldown_sec}
                  onChange={(e) =>
                    setRule({
                      ...rule,
                      cooldown_sec: Number(e.target.value),
                    })
                  }
                />
              </div>
              <div>
                <Label htmlFor='rule-batch'>{t('monitoring.batchSize')}</Label>
                <Input
                  id='rule-batch'
                  type='number'
                  min={1}
                  max={20}
                  value={rule.batch_size || 1}
                  onChange={(e) =>
                    setRule({ ...rule, batch_size: Number(e.target.value) })
                  }
                />
              </div>
              <div>
                <Label htmlFor='rule-verify'>
                  {t('monitoring.verifySchedule')}
                </Label>
                <Select
                  value={rule.verify_schedule_id || '__none__'}
                  onValueChange={(value) =>
                    setRule({
                      ...rule,
                      verify_schedule_id: value === '__none__' ? '' : value,
                    })
                  }
                >
                  <SelectTrigger id='rule-verify' className='w-full'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='__none__'>
                      {t('monitoring.noSchedule')}
                    </SelectItem>
                    {(schedules.data || []).map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label htmlFor='rule-compensate'>
                  {t('monitoring.compensateSchedule')}
                </Label>
                <Select
                  value={rule.compensate_schedule_id || '__none__'}
                  onValueChange={(value) =>
                    setRule({
                      ...rule,
                      compensate_schedule_id: value === '__none__' ? '' : value,
                    })
                  }
                >
                  <SelectTrigger id='rule-compensate' className='w-full'>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value='__none__'>
                      {t('monitoring.noSchedule')}
                    </SelectItem>
                    {(schedules.data || []).map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
            </div>
            <label className='flex items-center gap-2 text-sm'>
              <Checkbox
                checked={rule.enabled}
                onCheckedChange={(checked) =>
                  setRule({ ...rule, enabled: checked === true })
                }
              />
              {t('monitoring.enableAutomaticExecution')}
            </label>
            <p className='text-sm text-muted-foreground'>
              {t('monitoring.disableRuleHelp')}
            </p>
            <div className='flex gap-2'>
              <Button onClick={saveRule} disabled={action.isPending}>
                {rule.id
                  ? t('monitoring.saveRule')
                  : t('monitoring.createRule')}
              </Button>
              <Button
                variant='outline'
                onClick={() =>
                  editor.change(emptyRule(), () => setRule(emptyRule()))
                }
              >
                {t('monitoring.new')}
              </Button>
            </div>
          </EditorPanel>
        )}
        {editor.confirmation}
        {selectedRule && (
          <div id='rule-history' tabIndex={-1} className='scroll-mt-24'>
            <SectionCard title={t('monitoring.triggerHistory')}>
              <p className='mb-4 text-sm text-muted-foreground'>
                {rules.data?.find((item) => item.id === selectedRule)?.name}
              </p>
              {events.isError ? (
                <ErrorState
                  error={events.error}
                  onRetry={() => events.refetch()}
                />
              ) : !events.data?.length ? (
                <EmptyState message={t('common.noData')} />
              ) : (
                <RackList testId='monitoring-rule-events-rack'>
                  {events.data.map((item) => {
                    const executionText = (item.steps || []).length
                      ? item.steps
                          ?.map(
                            (step) =>
                              `${step.phase} · ${step.node_id} · ${step.status} · ${step.execution_id}`
                          )
                          .join(' | ')
                      : item.execution_ids?.join(', ')
                    return (
                      <RackRow
                        key={item.id}
                        testId={`monitoring-rule-event-${item.id}`}
                        title={new Date(item.observed_at).toLocaleString()}
                        fields={[
                          {
                            label: t('monitoring.status'),
                            value: (
                              <>
                                <StatusBadge status={item.status} />
                                {item.error && (
                                  <span
                                    className='ms-1 truncate text-xs text-muted-foreground'
                                    title={item.error}
                                  >
                                    {item.error}
                                  </span>
                                )}
                              </>
                            ),
                          },
                          {
                            label: t('monitoring.executions'),
                            value: (
                              <span title={executionText || '—'}>
                                {executionText || '—'}
                              </span>
                            ),
                          },
                        ]}
                        actions={
                          (item.status === 'unknown' ||
                            item.status === 'failed') && (
                            <>
                              <Button
                                size='sm'
                                disabled={action.isPending}
                                variant='outline'
                                onClick={() =>
                                  setConfirmation({
                                    message:
                                      item.status === 'failed'
                                        ? t('monitoring.confirmFailureHandled')
                                        : t(
                                            'monitoring.confirmThatTheUncertainExecutionHasBeenInspected'
                                          ),
                                    run: () =>
                                      api.post(
                                        `/monitoring/rules/${selectedRule}/resolve-unknown`
                                      ),
                                  })
                                }
                              >
                                {t('monitoring.acknowledge')}
                              </Button>
                              {item.status === 'unknown' && (
                                <Button
                                  size='sm'
                                  disabled={action.isPending}
                                  variant='outline'
                                  onClick={() =>
                                    setConfirmation({
                                      message: t(
                                        'monitoring.confirmAbandonUnknown'
                                      ),
                                      destructive: true,
                                      abandon: true,
                                      run: () =>
                                        api.post(
                                          `/monitoring/rules/${selectedRule}/abandon-unknown`
                                        ),
                                    })
                                  }
                                >
                                  {t('monitoring.abandonUnknown')}
                                </Button>
                              )}
                            </>
                          )
                        }
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
        destructive={confirmation?.destructive}
        isLoading={
          action.isPending || manualAction.isPending || abandonAction.isPending
        }
        handleConfirm={() => {
          if (confirmation) {
            if (confirmation.manual) manualAction.mutate(confirmation.run)
            else if (confirmation.abandon)
              abandonAction.mutate(confirmation.run)
            else action.mutate(confirmation.run)
            setConfirmation(null)
          }
        }}
      />
    </>
  )
}
