import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { api, type Node } from '@/lib/api'
import { nodeDisplayName } from '@/lib/node-display-name'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogContent,
  DialogFooter,
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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
  LoadingState,
  MoreMenu,
  StatusBadge,
} from '@/features/shared/ui'

type AlertRule = {
  id: string
  name: string
  description?: string
  node_id: string
  metric: string
  series?: string
  operator: string
  threshold: number
  duration_sec: number
  recovery_sec: number
  cooldown_sec: number
  severity: string
  channels: string[]
  enabled: boolean
  silenced_until?: string
  created_at?: string
  updated_at?: string
}

type ChannelConfig = {
  url_env?: string
  token_env?: string
  chat_id?: string
  timeout_sec?: number
  smtp_host?: string
  smtp_port?: number
  username?: string
  password_env?: string
  from?: string
  to_env?: string
  tls_mode?: string
}

type NotificationChannel = {
  id: string
  name: string
  type: string
  config: ChannelConfig
  enabled: boolean
  created_at?: string
  updated_at?: string
}

type AlertEvent = {
  id: string
  rule_id: string
  rule_name: string
  node_id: string
  node_hostname: string
  metric: string
  series: string
  operator: string
  status: string
  severity: string
  value: number | null
  threshold: number
  started_at: string
  fired_at: string
  resolved_at: string
  acknowledged_at: string
  silenced_until: string
  message: string
  error: string
}

type EventList = {
  events: AlertEvent[]
  total: number
  limit: number
  offset: number
}

const OPERATORS = ['gt', 'gte', 'lt', 'lte', 'eq', 'ne']
const SEVERITIES = ['info', 'warning', 'critical']
const CHANNEL_TYPES = [
  'webhook',
  'slack',
  'dingtalk',
  'wecom',
  'telegram',
  'email',
]
const EVENT_PAGE_SIZE = 20

function emptyRule(): AlertRule {
  return {
    id: '',
    name: '',
    description: '',
    node_id: '',
    metric: 'cpu.usage',
    series: '',
    operator: 'gt',
    threshold: 90,
    duration_sec: 60,
    recovery_sec: 30,
    cooldown_sec: 300,
    severity: 'warning',
    channels: [],
    enabled: true,
  }
}

function emptyChannel(): NotificationChannel {
  return {
    id: '',
    name: '',
    type: 'webhook',
    config: { url_env: '', timeout_sec: 10 },
    enabled: true,
  }
}

function operatorSymbol(operator: string) {
  return (
    {
      gt: '>',
      gte: '≥',
      lt: '<',
      lte: '≤',
      eq: '=',
      ne: '≠',
    }[operator] || operator
  )
}

function hasTimestamp(value?: string | null) {
  return (
    typeof value === 'string' &&
    value.trim() !== '' &&
    !value.startsWith('0001-01-01')
  )
}

function formatTime(value?: string) {
  return hasTimestamp(value) ? new Date(value as string).toLocaleString() : '—'
}

export function MonitoringAlerts() {
  const { t } = useTranslation()
  const qc = useQueryClient()
  const [rule, setRule] = useState<AlertRule>(emptyRule)
  const [channel, setChannel] = useState<NotificationChannel>(emptyChannel)
  const ruleEditor = useEditorPanel(rule)
  const channelEditor = useEditorPanel(channel)
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 30000)
    return () => window.clearInterval(timer)
  }, [])
  const [silenceTarget, setSilenceTarget] = useState<AlertRule | null>(null)
  const [silenceMinutes, setSilenceMinutes] = useState(60)
  const [confirmation, setConfirmation] = useState<{
    message: string
    run: () => Promise<unknown>
  } | null>(null)
  const rules = useQuery({
    queryKey: ['alert-rules'],
    queryFn: () => api.get<AlertRule[]>('/monitoring/alerts/rules'),
    refetchInterval: 15000,
  })
  const channels = useQuery({
    queryKey: ['notification-channels'],
    queryFn: () =>
      api.get<NotificationChannel[]>('/monitoring/notification-channels'),
    refetchInterval: 15000,
  })
  const nodes = useQuery({
    queryKey: ['monitoring-node-list'],
    queryFn: () => api.get<Node[]>('/nodes'),
  })
  const action = useMutation({
    mutationFn: async (fn: () => Promise<unknown>) => fn(),
    onSuccess: () => toast.success(t('alerts.saved')),
    onError: (error: Error) => toast.error(error.message),
  })
  const testChannel = useMutation({
    mutationFn: (id: string) =>
      api.post(`/monitoring/notification-channels/${id}/test`),
    onSuccess: () => toast.success(t('alerts.channelTested')),
    onError: (error: Error) => toast.error(error.message),
  })
  const refreshRules = () => {
    void qc.invalidateQueries({ queryKey: ['alert-rules'] })
    void qc.invalidateQueries({ queryKey: ['alert-events'] })
  }
  const refreshChannels = () => {
    void qc.invalidateQueries({ queryKey: ['notification-channels'] })
  }
  const nodeName = (id: string) =>
    nodeDisplayName(
      nodes.data?.find((node) => node.id === id) ?? { id },
      t('common.unknownNode')
    )
  const channelName = (id: string) =>
    channels.data?.find((item) => item.id === id)?.name || id
  const saveRule = () =>
    action.mutate(
      () =>
        rule.id
          ? api.put(`/monitoring/alerts/rules/${rule.id}`, rule)
          : api.post('/monitoring/alerts/rules', rule),
      {
        onSuccess: (saved) => {
          setRule(saved as AlertRule)
          ruleEditor.saved(saved)
          refreshRules()
        },
      }
    )
  const saveChannel = () =>
    action.mutate(
      () =>
        channel.id
          ? api.put(`/monitoring/notification-channels/${channel.id}`, channel)
          : api.post('/monitoring/notification-channels', channel),
      {
        onSuccess: (saved) => {
          setChannel(saved as NotificationChannel)
          channelEditor.saved(saved)
          refreshChannels()
        },
      }
    )
  const ruleBusy = action.isPending
  return (
    <>
      <NodeSteerHeader
        title={t('alerts.title')}
        description={t('alerts.description')}
      />
      <Main className='flex flex-1 flex-col gap-6'>
        <Tabs defaultValue='rules' className='space-y-4'>
          <TabsList>
            <TabsTrigger value='rules'>{t('alerts.rulesTab')}</TabsTrigger>
            <TabsTrigger value='channels'>
              {t('alerts.channelsTab')}
            </TabsTrigger>
            <TabsTrigger value='events'>{t('alerts.eventsTab')}</TabsTrigger>
          </TabsList>
          <TabsContent value='rules' className='space-y-6'>
            <div className='flex justify-end'>
              <Button
                variant='header-action'
                disabled={action.isPending}
                onClick={() =>
                  ruleEditor.change(emptyRule(), () => setRule(emptyRule()))
                }
              >
                {t('alerts.newRule')}
              </Button>
            </div>
            <Card>
              <CardHeader>
                <CardTitle>{t('alerts.rulesTab')}</CardTitle>
              </CardHeader>
              <CardContent className='space-y-4'>
                {rules.isLoading ? (
                  <LoadingState />
                ) : rules.isError ? (
                  <ErrorState
                    error={rules.error}
                    onRetry={() => rules.refetch()}
                  />
                ) : !rules.data?.length ? (
                  <EmptyState message={t('alerts.noRules')} />
                ) : (
                  <RackList testId='alert-rules-rack'>
                    {rules.data.map((item) => {
                      const channelText = item.channels.length
                        ? item.channels.map(channelName).join(', ')
                        : '—'
                      const silenced =
                        hasTimestamp(item.silenced_until) &&
                        Date.parse(item.silenced_until as string) > now
                      return (
                        <RackRow
                          key={item.id}
                          testId={`alert-rule-row-${item.id}`}
                          title={<span title={item.name}>{item.name}</span>}
                          subtitle={
                            <span
                              className='truncate'
                              title={
                                item.node_id
                                  ? nodeName(item.node_id)
                                  : t('alerts.allNodes')
                              }
                            >
                              {item.node_id
                                ? nodeName(item.node_id)
                                : t('alerts.allNodes')}
                            </span>
                          }
                          fields={[
                            {
                              label: t('alerts.metric'),
                              value: item.metric,
                              mono: true,
                            },
                            {
                              label: t('alerts.condition'),
                              value: `${operatorSymbol(item.operator)} ${item.threshold}`,
                            },
                            {
                              label: t('alerts.duration'),
                              value: `${item.duration_sec}s`,
                            },
                            {
                              label: t('alerts.severity'),
                              value: <StatusBadge status={item.severity} />,
                            },
                            {
                              label: t('alerts.channels'),
                              value: (
                                <span title={channelText}>{channelText}</span>
                              ),
                            },
                            {
                              label: t('alerts.state'),
                              value: (
                                <>
                                  <StatusBadge
                                    status={
                                      item.enabled ? 'enabled' : 'disabled'
                                    }
                                  />
                                  {silenced && (
                                    <span className='ms-1 text-xs text-muted-foreground'>
                                      {t('alerts.silenced')}
                                    </span>
                                  )}
                                </>
                              ),
                            },
                          ]}
                          actions={
                            <>
                              <Button
                                variant='outline'
                                size='sm'
                                disabled={action.isPending}
                                onClick={() =>
                                  ruleEditor.change(item, () => setRule(item))
                                }
                              >
                                {t('alerts.edit')}
                              </Button>
                              <MoreMenu>
                                <DropdownMenuItem
                                  disabled={action.isPending}
                                  onSelect={() =>
                                    action.mutate(
                                      () =>
                                        api.post(
                                          `/monitoring/alerts/rules/${item.id}/enable`,
                                          { enabled: !item.enabled }
                                        ),
                                      { onSuccess: refreshRules }
                                    )
                                  }
                                >
                                  {item.enabled
                                    ? t('alerts.disable')
                                    : t('alerts.enable')}
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  disabled={action.isPending}
                                  onSelect={() => {
                                    setSilenceTarget(item)
                                    setSilenceMinutes(60)
                                  }}
                                >
                                  {silenced
                                    ? t('alerts.unsilence')
                                    : t('alerts.silence')}
                                </DropdownMenuItem>
                                <DangerMenuItem
                                  label={t('alerts.delete')}
                                  title={t('alerts.confirmDeleteRule')}
                                  description={t('common.confirmAction')}
                                  confirmText={t('alerts.confirm')}
                                  onSelect={() => {
                                    void api
                                      .del(
                                        `/monitoring/alerts/rules/${item.id}`
                                      )
                                      .then((result) => {
                                        if (rule.id === item.id) {
                                          setRule(emptyRule())
                                          ruleEditor.removed()
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
              </CardContent>
            </Card>
            {ruleEditor.open && (
              <EditorPanel
                title={`${rule.id ? t('alerts.editRule') : t('alerts.newRule')}: ${rule.name}`}
                onClose={ruleEditor.close}
                busy={action.isPending}
              >
                <div className='space-y-4'>
                  <div className='grid gap-3 md:grid-cols-2'>
                    <div>
                      <Label htmlFor='alert-name'>{t('alerts.name')}</Label>
                      <Input
                        id='alert-name'
                        value={rule.name}
                        onChange={(event) =>
                          setRule({ ...rule, name: event.target.value })
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor='alert-description'>
                        {t('alerts.descriptionLabel')}
                      </Label>
                      <Textarea
                        id='alert-description'
                        value={rule.description || ''}
                        onChange={(event) =>
                          setRule({
                            ...rule,
                            description: event.target.value,
                          })
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor='alert-node'>{t('alerts.node')}</Label>
                      <Select
                        value={rule.node_id || '__all__'}
                        onValueChange={(value) =>
                          setRule({
                            ...rule,
                            node_id: value === '__all__' ? '' : value,
                          })
                        }
                      >
                        <SelectTrigger id='alert-node' className='w-full'>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value='__all__'>
                            {t('alerts.allNodes')}
                          </SelectItem>
                          {(nodes.data || []).map((node) => (
                            <SelectItem key={node.id} value={node.id}>
                              {nodeDisplayName(node, t('common.unknownNode'))}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <Label htmlFor='alert-metric'>{t('alerts.metric')}</Label>
                      <Input
                        id='alert-metric'
                        value={rule.metric}
                        onChange={(event) =>
                          setRule({ ...rule, metric: event.target.value })
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor='alert-series'>{t('alerts.series')}</Label>
                      <Input
                        id='alert-series'
                        value={rule.series || ''}
                        onChange={(event) =>
                          setRule({ ...rule, series: event.target.value })
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor='alert-operator'>
                        {t('alerts.condition')}
                      </Label>
                      <Select
                        value={rule.operator}
                        onValueChange={(value) =>
                          setRule({ ...rule, operator: value })
                        }
                      >
                        <SelectTrigger id='alert-operator' className='w-full'>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {OPERATORS.map((operator) => (
                            <SelectItem key={operator} value={operator}>
                              {operatorSymbol(operator)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    <div>
                      <Label htmlFor='alert-threshold'>
                        {t('alerts.threshold')}
                      </Label>
                      <Input
                        id='alert-threshold'
                        type='number'
                        value={rule.threshold}
                        onChange={(event) =>
                          setRule({
                            ...rule,
                            threshold: Number(event.target.value),
                          })
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor='alert-duration'>
                        {t('alerts.duration')}
                      </Label>
                      <Input
                        id='alert-duration'
                        type='number'
                        value={rule.duration_sec}
                        onChange={(event) =>
                          setRule({
                            ...rule,
                            duration_sec: Number(event.target.value),
                          })
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor='alert-recovery'>
                        {t('alerts.recovery')}
                      </Label>
                      <Input
                        id='alert-recovery'
                        type='number'
                        value={rule.recovery_sec}
                        onChange={(event) =>
                          setRule({
                            ...rule,
                            recovery_sec: Number(event.target.value),
                          })
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor='alert-cooldown'>
                        {t('alerts.cooldown')}
                      </Label>
                      <Input
                        id='alert-cooldown'
                        type='number'
                        value={rule.cooldown_sec}
                        onChange={(event) =>
                          setRule({
                            ...rule,
                            cooldown_sec: Number(event.target.value),
                          })
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor='alert-severity'>
                        {t('alerts.severity')}
                      </Label>
                      <Select
                        value={rule.severity}
                        onValueChange={(value) =>
                          setRule({ ...rule, severity: value })
                        }
                      >
                        <SelectTrigger id='alert-severity' className='w-full'>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {SEVERITIES.map((severity) => (
                            <SelectItem key={severity} value={severity}>
                              {t(`alerts.severity_${severity}`)}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                  </div>
                  <div className='space-y-2'>
                    <Label>{t('alerts.channels')}</Label>
                    <div className='flex flex-wrap gap-3'>
                      {(channels.data || []).map((item) => (
                        <label
                          key={item.id}
                          className='flex items-center gap-2 text-sm'
                        >
                          <Checkbox
                            checked={rule.channels.includes(item.id)}
                            onCheckedChange={(checked) =>
                              setRule({
                                ...rule,
                                channels:
                                  checked === true
                                    ? [...rule.channels, item.id]
                                    : rule.channels.filter(
                                        (id) => id !== item.id
                                      ),
                              })
                            }
                          />
                          {item.name}
                        </label>
                      ))}
                    </div>
                    <p className='text-xs text-muted-foreground'>
                      {t('alerts.noChannelsHint')}
                    </p>
                  </div>
                  <label className='flex items-center gap-2 text-sm'>
                    <Checkbox
                      checked={rule.enabled}
                      onCheckedChange={(checked) =>
                        setRule({ ...rule, enabled: checked === true })
                      }
                    />
                    {t('alerts.enabled')}
                  </label>
                  <div className='flex gap-2'>
                    <Button onClick={saveRule} disabled={ruleBusy}>
                      {rule.id ? t('alerts.saveRule') : t('alerts.createRule')}
                    </Button>
                    <Button
                      variant='outline'
                      onClick={() =>
                        ruleEditor.change(emptyRule(), () =>
                          setRule(emptyRule())
                        )
                      }
                    >
                      {t('alerts.new')}
                    </Button>
                  </div>
                </div>
              </EditorPanel>
            )}
            {ruleEditor.confirmation}
          </TabsContent>
          <TabsContent value='channels' className='space-y-6'>
            <div className='flex justify-end'>
              <Button
                variant='header-action'
                disabled={action.isPending}
                onClick={() =>
                  channelEditor.change(emptyChannel(), () =>
                    setChannel(emptyChannel())
                  )
                }
              >
                {t('alerts.newChannel')}
              </Button>
            </div>
            <Card>
              <CardHeader>
                <CardTitle>{t('alerts.channelsTab')}</CardTitle>
              </CardHeader>
              <CardContent className='space-y-4'>
                {channels.isLoading ? (
                  <LoadingState />
                ) : channels.isError ? (
                  <ErrorState
                    error={channels.error}
                    onRetry={() => channels.refetch()}
                  />
                ) : !channels.data?.length ? (
                  <EmptyState message={t('alerts.noChannels')} />
                ) : (
                  <RackList testId='notification-channels-rack'>
                    {channels.data.map((item) => (
                      <RackRow
                        key={item.id}
                        testId={`notification-channel-row-${item.id}`}
                        title={<span title={item.name}>{item.name}</span>}
                        subtitle={item.type}
                        fields={[
                          {
                            label: t('alerts.state'),
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
                              onClick={() =>
                                channelEditor.change(item, () =>
                                  setChannel(item)
                                )
                              }
                            >
                              {t('alerts.edit')}
                            </Button>
                            <MoreMenu>
                              <DropdownMenuItem
                                disabled={action.isPending}
                                onSelect={() => testChannel.mutate(item.id)}
                              >
                                {t('alerts.test')}
                              </DropdownMenuItem>
                              <DangerMenuItem
                                label={t('alerts.delete')}
                                title={t('alerts.confirmDeleteChannel')}
                                description={t('common.confirmAction')}
                                confirmText={t('alerts.confirm')}
                                onSelect={() => {
                                  void api
                                    .del(
                                      `/monitoring/notification-channels/${item.id}`
                                    )
                                    .then((result) => {
                                      if (channel.id === item.id) {
                                        setChannel(emptyChannel())
                                        channelEditor.removed()
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
              </CardContent>
            </Card>
            {channelEditor.open && (
              <EditorPanel
                title={`${channel.id ? t('alerts.editChannel') : t('alerts.newChannel')}: ${channel.name}`}
                onClose={channelEditor.close}
                busy={action.isPending}
              >
                <div className='space-y-4'>
                  <div className='grid gap-3 md:grid-cols-2'>
                    <div>
                      <Label htmlFor='channel-name'>{t('alerts.name')}</Label>
                      <Input
                        id='channel-name'
                        value={channel.name}
                        onChange={(event) =>
                          setChannel({ ...channel, name: event.target.value })
                        }
                      />
                    </div>
                    <div>
                      <Label htmlFor='channel-type'>
                        {t('alerts.channelType')}
                      </Label>
                      <Select
                        value={channel.type}
                        onValueChange={(value) =>
                          setChannel({
                            ...channel,
                            type: value,
                            config:
                              value === 'telegram'
                                ? {
                                    token_env: '',
                                    chat_id: '',
                                    timeout_sec: 10,
                                  }
                                : value === 'email'
                                  ? {
                                      smtp_host: '',
                                      smtp_port: 587,
                                      username: '',
                                      password_env: '',
                                      from: '',
                                      to_env: '',
                                      tls_mode: 'starttls',
                                      timeout_sec: 10,
                                    }
                                  : { url_env: '', timeout_sec: 10 },
                          })
                        }
                      >
                        <SelectTrigger id='channel-type' className='w-full'>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {CHANNEL_TYPES.map((type) => (
                            <SelectItem key={type} value={type}>
                              {type}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    {['webhook', 'slack', 'dingtalk', 'wecom'].includes(
                      channel.type
                    ) && (
                      <>
                        <div>
                          <Label htmlFor='channel-url-env'>
                            {t('alerts.urlEnv')}
                          </Label>
                          <Input
                            id='channel-url-env'
                            value={channel.config.url_env || ''}
                            onChange={(event) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  url_env: event.target.value,
                                },
                              })
                            }
                          />
                        </div>
                        <div>
                          <Label htmlFor='channel-timeout'>
                            {t('alerts.timeout')}
                          </Label>
                          <Input
                            id='channel-timeout'
                            type='number'
                            value={channel.config.timeout_sec || 10}
                            onChange={(event) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  timeout_sec: Number(event.target.value),
                                },
                              })
                            }
                          />
                        </div>
                      </>
                    )}
                    {channel.type === 'telegram' && (
                      <>
                        <div>
                          <Label htmlFor='channel-token-env'>
                            {t('alerts.tokenEnv')}
                          </Label>
                          <Input
                            id='channel-token-env'
                            value={channel.config.token_env || ''}
                            onChange={(event) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  token_env: event.target.value,
                                },
                              })
                            }
                          />
                        </div>
                        <div>
                          <Label htmlFor='channel-chat-id'>
                            {t('alerts.chatId')}
                          </Label>
                          <Input
                            id='channel-chat-id'
                            value={channel.config.chat_id || ''}
                            onChange={(event) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  chat_id: event.target.value,
                                },
                              })
                            }
                          />
                        </div>
                      </>
                    )}
                    {channel.type === 'email' && (
                      <>
                        <div>
                          <Label htmlFor='channel-smtp-host'>
                            {t('alerts.smtpHost')}
                          </Label>
                          <Input
                            id='channel-smtp-host'
                            value={channel.config.smtp_host || ''}
                            onChange={(event) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  smtp_host: event.target.value,
                                },
                              })
                            }
                          />
                        </div>
                        <div>
                          <Label htmlFor='channel-smtp-port'>
                            {t('alerts.smtpPort')}
                          </Label>
                          <Input
                            id='channel-smtp-port'
                            type='number'
                            value={channel.config.smtp_port || 587}
                            onChange={(event) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  smtp_port: Number(event.target.value),
                                },
                              })
                            }
                          />
                        </div>
                        <div>
                          <Label htmlFor='channel-username'>
                            {t('alerts.username')}
                          </Label>
                          <Input
                            id='channel-username'
                            value={channel.config.username || ''}
                            onChange={(event) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  username: event.target.value,
                                },
                              })
                            }
                          />
                        </div>
                        <div>
                          <Label htmlFor='channel-password-env'>
                            {t('alerts.passwordEnv')}
                          </Label>
                          <Input
                            id='channel-password-env'
                            value={channel.config.password_env || ''}
                            onChange={(event) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  password_env: event.target.value,
                                },
                              })
                            }
                          />
                        </div>
                        <div>
                          <Label htmlFor='channel-from'>
                            {t('alerts.from')}
                          </Label>
                          <Input
                            id='channel-from'
                            value={channel.config.from || ''}
                            onChange={(event) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  from: event.target.value,
                                },
                              })
                            }
                          />
                        </div>
                        <div>
                          <Label htmlFor='channel-to-env'>
                            {t('alerts.toEnv')}
                          </Label>
                          <Input
                            id='channel-to-env'
                            value={channel.config.to_env || ''}
                            onChange={(event) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  to_env: event.target.value,
                                },
                              })
                            }
                          />
                        </div>
                        <div>
                          <Label htmlFor='channel-tls'>
                            {t('alerts.tlsMode')}
                          </Label>
                          <Select
                            value={channel.config.tls_mode || 'starttls'}
                            onValueChange={(value) =>
                              setChannel({
                                ...channel,
                                config: {
                                  ...channel.config,
                                  tls_mode: value,
                                },
                              })
                            }
                          >
                            <SelectTrigger id='channel-tls' className='w-full'>
                              <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                              <SelectItem value='none'>none</SelectItem>
                              <SelectItem value='starttls'>starttls</SelectItem>
                              <SelectItem value='tls'>tls</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                      </>
                    )}
                  </div>
                  <p className='text-xs text-muted-foreground'>
                    {t('alerts.secretHint')}
                  </p>
                  <label className='flex items-center gap-2 text-sm'>
                    <Checkbox
                      checked={channel.enabled}
                      onCheckedChange={(checked) =>
                        setChannel({ ...channel, enabled: checked === true })
                      }
                    />
                    {t('alerts.enabled')}
                  </label>
                  <div className='flex gap-2'>
                    <Button onClick={saveChannel} disabled={ruleBusy}>
                      {channel.id
                        ? t('alerts.saveChannel')
                        : t('alerts.createChannel')}
                    </Button>
                    <Button
                      variant='outline'
                      onClick={() =>
                        channelEditor.change(emptyChannel(), () =>
                          setChannel(emptyChannel())
                        )
                      }
                    >
                      {t('alerts.new')}
                    </Button>
                  </div>
                </div>
              </EditorPanel>
            )}
            {channelEditor.confirmation}
          </TabsContent>
          <TabsContent value='events'>
            <AlertEvents
              rules={rules.data || []}
              nodes={nodes.data || []}
              onAck={(id) =>
                action.mutate(
                  () => api.post(`/monitoring/alerts/events/${id}/ack`),
                  {
                    onSuccess: () => {
                      void qc.invalidateQueries({ queryKey: ['alert-events'] })
                    },
                  }
                )
              }
            />
          </TabsContent>
        </Tabs>
      </Main>
      <Dialog
        open={silenceTarget !== null}
        onOpenChange={(open) => {
          if (!open) setSilenceTarget(null)
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{t('alerts.silence')}</DialogTitle>
          </DialogHeader>
          <div className='space-y-2'>
            <Label htmlFor='silence-minutes'>
              {t('alerts.silenceMinutes')}
            </Label>
            <Input
              id='silence-minutes'
              type='number'
              min={1}
              max={43200}
              value={silenceMinutes}
              onChange={(event) =>
                setSilenceMinutes(Number(event.target.value))
              }
            />
          </div>
          <DialogFooter>
            <Button
              variant='outline'
              onClick={() => {
                if (!silenceTarget) return
                action.mutate(
                  () =>
                    api.del(
                      `/monitoring/alerts/rules/${silenceTarget.id}/silence`
                    ),
                  {
                    onSuccess: () => {
                      setSilenceTarget(null)
                      refreshRules()
                    },
                  }
                )
              }}
            >
              {t('alerts.unsilence')}
            </Button>
            <Button
              onClick={() => {
                if (!silenceTarget) return
                action.mutate(
                  () =>
                    api.post(
                      `/monitoring/alerts/rules/${silenceTarget.id}/silence`,
                      { duration_sec: silenceMinutes * 60 }
                    ),
                  {
                    onSuccess: () => {
                      setSilenceTarget(null)
                      refreshRules()
                    },
                  }
                )
              }}
            >
              {t('alerts.silence')}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
      <ConfirmDialog
        open={confirmation !== null}
        onOpenChange={(open) => {
          if (!open) setConfirmation(null)
        }}
        title={t('alerts.confirmTitle')}
        cancelBtnText={t('common.cancel')}
        confirmText={t('alerts.confirm')}
        desc={confirmation?.message || ''}
        destructive
        isLoading={action.isPending}
        handleConfirm={() => {
          if (confirmation)
            action.mutate(confirmation.run, {
              onSuccess: () => {
                refreshRules()
                refreshChannels()
              },
            })
          setConfirmation(null)
        }}
      />
    </>
  )
}

function AlertEvents({
  rules,
  nodes,
  onAck,
}: {
  rules: AlertRule[]
  nodes: Node[]
  onAck: (id: string) => void
}) {
  const { t } = useTranslation()
  const [state, setState] = useState<'active' | 'history'>('active')
  const [status, setStatus] = useState('')
  const [severity, setSeverity] = useState('')
  const [ruleId, setRuleId] = useState('')
  const [nodeId, setNodeId] = useState('')
  const [offset, setOffset] = useState(0)
  const query = useQuery({
    queryKey: ['alert-events', state, status, severity, ruleId, nodeId, offset],
    queryFn: () => {
      const params = new URLSearchParams({
        state,
        limit: String(EVENT_PAGE_SIZE),
        offset: String(offset),
      })
      if (status) params.set('status', status)
      if (severity) params.set('severity', severity)
      if (ruleId) params.set('rule_id', ruleId)
      if (nodeId) params.set('node_id', nodeId)
      return api.get<EventList>(`/monitoring/alerts/events?${params}`)
    },
    refetchInterval: 15000,
  })
  return (
    <Card>
      <CardHeader className='flex flex-row items-center justify-between space-y-0'>
        <CardTitle>{t('alerts.eventsTab')}</CardTitle>
        <div className='flex gap-2'>
          <Button
            variant={state === 'active' ? 'default' : 'outline'}
            size='sm'
            onClick={() => {
              setState('active')
              setOffset(0)
            }}
          >
            {t('alerts.activeEvents')}
          </Button>
          <Button
            variant={state === 'history' ? 'default' : 'outline'}
            size='sm'
            onClick={() => {
              setState('history')
              setOffset(0)
            }}
          >
            {t('alerts.historyEvents')}
          </Button>
        </div>
      </CardHeader>
      <CardContent className='space-y-4'>
        <div className='grid gap-2 sm:grid-cols-2 lg:grid-cols-4'>
          <Select
            value={status || '__all__'}
            onValueChange={(value) => {
              setStatus(value === '__all__' ? '' : value)
              setOffset(0)
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder={t('alerts.allStatuses')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='__all__'>{t('alerts.allStatuses')}</SelectItem>
              {['pending', 'firing', 'resolved', 'silenced', 'unknown'].map(
                (value) => (
                  <SelectItem key={value} value={value}>
                    {t(`alerts.status_${value}`)}
                  </SelectItem>
                )
              )}
            </SelectContent>
          </Select>
          <Select
            value={severity || '__all__'}
            onValueChange={(value) => {
              setSeverity(value === '__all__' ? '' : value)
              setOffset(0)
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder={t('alerts.allSeverities')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='__all__'>
                {t('alerts.allSeverities')}
              </SelectItem>
              {SEVERITIES.map((value) => (
                <SelectItem key={value} value={value}>
                  {t(`alerts.severity_${value}`)}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={ruleId || '__all__'}
            onValueChange={(value) => {
              setRuleId(value === '__all__' ? '' : value)
              setOffset(0)
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder={t('alerts.allRules')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='__all__'>{t('alerts.allRules')}</SelectItem>
              {rules.map((rule) => (
                <SelectItem key={rule.id} value={rule.id}>
                  {rule.name}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Select
            value={nodeId || '__all__'}
            onValueChange={(value) => {
              setNodeId(value === '__all__' ? '' : value)
              setOffset(0)
            }}
          >
            <SelectTrigger>
              <SelectValue placeholder={t('alerts.allNodesFilter')} />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value='__all__'>
                {t('alerts.allNodesFilter')}
              </SelectItem>
              {nodes.map((node) => (
                <SelectItem key={node.id} value={node.id}>
                  {nodeDisplayName(node, t('common.unknownNode'))}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        {query.isLoading ? (
          <LoadingState />
        ) : query.isError ? (
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        ) : !query.data?.events.length ? (
          <EmptyState message={t('alerts.noEvents')} />
        ) : (
          <>
            <RackList testId='alert-events-rack'>
              {query.data.events.map((event) => {
                const acknowledged = hasTimestamp(event.acknowledged_at)
                return (
                  <RackRow
                    key={event.id}
                    testId={`alert-event-row-${event.id}`}
                    title={
                      <span title={event.rule_name}>{event.rule_name}</span>
                    }
                    subtitle={
                      <>
                        <span className='truncate'>
                          {nodeDisplayName(
                            nodes.find((node) => node.id === event.node_id) ?? {
                              hostname: event.node_hostname,
                              id: event.node_id,
                            },
                            t('common.unknownNode')
                          )}
                        </span>
                        <span aria-hidden='true'>·</span>
                        <span className='shrink-0'>
                          {t('alerts.startedAt')} {formatTime(event.started_at)}
                        </span>
                      </>
                    }
                    fields={[
                      {
                        label: t('alerts.metric'),
                        value: event.metric,
                        mono: true,
                      },
                      {
                        label: t('alerts.value'),
                        value: event.value ?? '—',
                      },
                      {
                        label: t('alerts.threshold'),
                        value: event.threshold,
                      },
                      {
                        label: t('alerts.status'),
                        value: (
                          <>
                            <StatusBadge status={event.status} />
                            {acknowledged && (
                              <span className='ms-1 text-xs text-muted-foreground'>
                                {t('alerts.acknowledged')}
                              </span>
                            )}
                          </>
                        ),
                      },
                      {
                        label: t('alerts.severity'),
                        value: <StatusBadge status={event.severity} />,
                      },
                      {
                        label: t('alerts.resolvedAt'),
                        value: formatTime(event.resolved_at),
                      },
                      {
                        label: t('alerts.silencedUntil'),
                        value: formatTime(event.silenced_until),
                      },
                    ]}
                    actions={
                      state === 'active' &&
                      ['pending', 'firing', 'silenced', 'unknown'].includes(
                        event.status
                      ) &&
                      !acknowledged &&
                      !hasTimestamp(event.resolved_at) ? (
                        <Button
                          variant='outline'
                          size='sm'
                          onClick={() => onAck(event.id)}
                        >
                          {t('alerts.ack')}
                        </Button>
                      ) : undefined
                    }
                  />
                )
              })}
            </RackList>
            <div className='flex items-center justify-between text-sm'>
              <span className='text-muted-foreground'>
                {t('alerts.pageInfo', {
                  page: Math.floor(offset / EVENT_PAGE_SIZE) + 1,
                  total: query.data.total,
                })}
              </span>
              <div className='flex gap-2'>
                <Button
                  variant='outline'
                  size='sm'
                  disabled={offset === 0}
                  onClick={() =>
                    setOffset(Math.max(0, offset - EVENT_PAGE_SIZE))
                  }
                >
                  {t('alerts.previous')}
                </Button>
                <Button
                  variant='outline'
                  size='sm'
                  disabled={offset + EVENT_PAGE_SIZE >= query.data.total}
                  onClick={() => setOffset(offset + EVENT_PAGE_SIZE)}
                >
                  {t('alerts.next')}
                </Button>
              </div>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  )
}
