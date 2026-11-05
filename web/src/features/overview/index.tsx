import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { Activity, ArrowUpRight, CalendarClock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useAuthStore } from '@/stores/auth-store'
import {
  api,
  type Application,
  type ApplicationNodeState,
  type Execution,
  type Node,
  type Schedule,
  type Task,
} from '@/lib/api'
import { nodeDisplayName } from '@/lib/node-display-name'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Main } from '@/components/layout/main'
import { NodeSteerHeader } from '@/components/layout/nodesteer-header'
import { NodeMonitoringTable } from '@/features/monitoring/node-monitoring-table'
import type { NodeOverview } from '@/features/monitoring/types'
import { RackList, RackRow, RowText } from '@/features/shared/rack-list'
import {
  DurationValue,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
  TimeValue,
} from '@/features/shared/ui'

type DashboardData = {
  nodes: Node[]
  executions: Execution[]
  applications: Application[]
  applicationHealth: Record<string, string>
  /** 应用状态接口是否全部成功；任一失败时健康比显示为不可用，避免显示假 0 值 */
  applicationHealthAvailable: boolean
  /** 首个应用状态请求错误，用于错误态文案与重试 */
  applicationHealthError: unknown
  schedules: Schedule[]
  tasks: Task[]
  healthyApplications: number
}

/** 单个应用状态查询结果：失败不再吞成空数组，保留错误供错误态与健康比判定使用 */
type ApplicationStatesResult =
  | { ok: true; states: ApplicationNodeState[] }
  | { ok: false; error: unknown }

async function loadDashboard(): Promise<DashboardData> {
  const [nodes, executions, applications, schedules, tasks] = await Promise.all(
    [
      api.get<Node[]>('/nodes'),
      api.get<Execution[]>('/executions?limit=50'),
      api.get<Application[]>('/applications'),
      api.get<Schedule[]>('/schedules'),
      api.get<Task[]>('/tasks'),
    ]
  )
  const applicationStateResults = await Promise.all(
    applications.map(async (application): Promise<ApplicationStatesResult> => {
      try {
        return {
          ok: true,
          states: await api.get<ApplicationNodeState[]>(
            `/applications/${application.id}/state`
          ),
        }
      } catch (error) {
        return { ok: false, error }
      }
    })
  )
  const applicationHealthAvailable = applicationStateResults.every(
    (result) => result.ok
  )
  const applicationHealthError =
    applicationStateResults.find((result) => !result.ok)?.error ?? null
  const applicationHealth = Object.fromEntries(
    applications.map((application, index) => {
      const result = applicationStateResults[index]
      // 状态请求失败的应用健康未知，不得当作不健康（否则健康比错误下降）
      const health = !result.ok
        ? 'unknown'
        : result.states.some((state) => state.health === 'healthy')
          ? 'healthy'
          : result.states.some((state) => state.health === 'unhealthy')
            ? 'unhealthy'
            : 'unknown'
      return [application.id, health]
    })
  )
  const healthyApplications = Object.values(applicationHealth).filter(
    (health) => health === 'healthy'
  ).length
  return {
    nodes,
    executions,
    applications,
    applicationHealth,
    applicationHealthAvailable,
    applicationHealthError,
    schedules,
    tasks,
    healthyApplications,
  }
}

export function Dashboard() {
  const { t, i18n } = useTranslation()
  const isAdmin = useAuthStore(
    (state) => state.auth.user?.role.includes('administrator') ?? false
  )
  const query = useQuery({
    queryKey: ['dashboard'],
    queryFn: loadDashboard,
    refetchInterval: 15000,
  })
  const overview = useQuery({
    queryKey: ['monitoring-nodes-overview'],
    queryFn: () =>
      api.get<{ nodes: NodeOverview[] }>('/monitoring/nodes/overview'),
    enabled: isAdmin,
    refetchInterval: 30000,
    staleTime: 30000,
  })
  const data = query.data
  const taskName = useMemo(
    () => new Map((data?.tasks || []).map((task) => [task.id, task.name])),
    [data?.tasks]
  )
  const nodeName = useMemo(
    () =>
      new Map(
        (data?.nodes || []).map((node) => [
          node.id,
          nodeDisplayName(node, t('common.unknownNode')),
        ])
      ),
    [data?.nodes, t]
  )
  const failed = (data?.executions || []).filter((execution) =>
    ['FAILED', 'TIMED_OUT'].includes(execution.status)
  ).length
  const terminal = (data?.executions || []).filter((execution) =>
    ['SUCCESS', 'FAILED', 'TIMED_OUT'].includes(execution.status)
  ).length
  const successRate = terminal
    ? `${Math.round(((data?.executions || []).filter((execution) => execution.status === 'SUCCESS').length / terminal) * 1000) / 10}%`
    : '-'
  const running = (data?.executions || [])
    .filter((execution) => execution.status === 'RUNNING')
    .slice(0, 6)
  const recent = (data?.executions || []).slice(0, 8)
  const schedules = (data?.schedules || [])
    .filter((schedule) => schedule.enabled)
    .slice(0, 5)
  const online =
    data?.nodes.filter((node) => node.status === 'online').length || 0
  const overviewByNode = new Map(
    (overview.data?.nodes || []).map((item) => [item.node_id, item])
  )
  const monitoringAvailable = overview.isSuccess && !overview.isError
  const monitoredNodes = monitoringAvailable
    ? (data?.nodes || []).filter((node) => overviewByNode.has(node.id)).length
    : '—'
  const syncCounts = ['synced', 'outdated', 'error'].map((status) => ({
    status,
    count:
      data?.nodes.filter((node) => node.sync_status?.toLowerCase() === status)
        .length || 0,
  }))

  if (query.isLoading)
    return (
      <>
        <NodeSteerHeader
          title={t('dashboard.title')}
          description={t('dashboard.description')}
        />
        <Main>
          <LoadingState />
        </Main>
      </>
    )
  if (query.isError || !data)
    return (
      <>
        <NodeSteerHeader title={t('dashboard.title')} />
        <Main>
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        </Main>
      </>
    )

  return (
    <>
      <NodeSteerHeader
        title={t('dashboard.title')}
        description={t('dashboard.description')}
      />
      <Main className='flex flex-1 flex-col'>
        <div className='flex w-full flex-col gap-6'>
          <div className='grid grid-cols-1 items-stretch gap-4'>
            <Card
              data-testid='dashboard-automation-card'
              className='flex h-full flex-col'
            >
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-1'>
                <CardTitle>{t('dashboard.automationTitle')}</CardTitle>
                <Link
                  className='text-xs text-muted-foreground hover:text-foreground'
                  to='/executions'
                >
                  {t('common.viewAll')}{' '}
                  <ArrowUpRight className='inline size-3.5' />
                </Link>
              </CardHeader>
              <CardContent className='flex flex-1 flex-col gap-4'>
                <div className='grid grid-cols-3 gap-2'>
                  <CompactMetric
                    testId='automation-running'
                    label={t('dashboard.runningNow')}
                    value={
                      data.executions.filter(
                        (execution) => execution.status === 'RUNNING'
                      ).length
                    }
                  />
                  <CompactMetric
                    testId='automation-failed'
                    label={t('dashboard.execFailed')}
                    value={failed}
                    detail={t('dashboard.recentWindow')}
                  />
                  <CompactMetric
                    testId='automation-success-rate'
                    label={t('dashboard.successRate')}
                    value={successRate}
                    detail={`${terminal} ${t('dashboard.completedRuns')}`}
                  />
                </div>
                <div className='flex flex-1 flex-col gap-2'>
                  <h3 className='text-sm font-medium'>
                    {t('dashboard.runningExecutions')}
                  </h3>
                  {running.length === 0 ? (
                    <div className='flex flex-1 items-center justify-center'>
                      <EmptyState
                        className='min-h-20 py-4'
                        message={t('dashboard.noRunning')}
                      />
                    </div>
                  ) : (
                    <div className='divide-y'>
                      {running.map((execution) => (
                        <a
                          key={execution.id}
                          href={`/executions/${execution.id}`}
                          className='flex min-h-[60px] min-w-0 items-center gap-3 py-2.5 hover:bg-muted/30'
                        >
                          <Activity className='size-4 shrink-0 text-muted-foreground' />
                          <div className='min-w-0 flex-1'>
                            <RowText
                              primary={
                                <span className='block truncate text-sm font-medium'>
                                  {taskName.get(execution.task_id) ||
                                    t('common.unknownTask')}
                                </span>
                              }
                              secondary={
                                <span className='text-xs text-muted-foreground'>
                                  {nodeName.get(execution.node_id) ||
                                    t('common.unknownNode')}
                                </span>
                              }
                            />
                          </div>
                          <DurationValue start={execution.start_time} running />
                          <StatusBadge status={execution.status} />
                        </a>
                      ))}
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>

            {isAdmin && (
              <Card data-testid='dashboard-monitoring-overview'>
                <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-1'>
                  <CardTitle>{t('dashboard.monitoringOverview')}</CardTitle>
                  <Link
                    className='text-xs text-muted-foreground hover:text-foreground'
                    to='/monitoring'
                  >
                    {t('dashboard.viewMonitoring')}{' '}
                    <ArrowUpRight className='inline size-3.5' />
                  </Link>
                </CardHeader>
                <CardContent className='space-y-4'>
                  {isAdmin && (
                    <div
                      data-testid='dashboard-monitoring-card'
                      className='grid grid-cols-3 gap-2'
                    >
                      <CompactMetric
                        testId='monitoring-online'
                        label={t('dashboard.online')}
                        value={online}
                      />
                      <CompactMetric
                        testId='monitoring-monitored'
                        label={t('dashboard.monitoredNodes')}
                        value={monitoredNodes}
                      />
                      <CompactMetric
                        testId='monitoring-alerts'
                        label={t('dashboard.activeAlerts')}
                        value={t('webFix.alertsPending')}
                      />
                    </div>
                  )}
                  <NodeMonitoringTable
                    nodes={data.nodes}
                    overviewByNode={overviewByNode}
                    loading={overview.isLoading}
                    error={overview.error}
                    onRetry={() => overview.refetch()}
                    rowNavigation
                  />
                </CardContent>
              </Card>
            )}
          </div>

          <div className='grid grid-cols-1 items-stretch gap-4 lg:grid-cols-7'>
            <Card className='h-full lg:col-span-4'>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-1'>
                <CardTitle>{t('dashboard.applicationStatus')}</CardTitle>
                <div className='flex items-center gap-3'>
                  <span className='text-xs text-muted-foreground'>
                    {data.applicationHealthAvailable
                      ? `${data.healthyApplications}/${data.applications.length} ${t('dashboard.appsHealthy').toLowerCase()}`
                      : t('dashboard.appsHealthUnavailable')}
                  </span>
                  <Link
                    className='text-xs text-muted-foreground hover:text-foreground'
                    to='/applications'
                  >
                    {t('common.viewAll')}{' '}
                    <ArrowUpRight className='inline size-3.5' />
                  </Link>
                </div>
              </CardHeader>
              <CardContent>
                {!data.applicationHealthAvailable ? (
                  <div className='mb-3'>
                    <ErrorState
                      error={data.applicationHealthError}
                      onRetry={() => query.refetch()}
                    />
                  </div>
                ) : null}
                {data.applications.length === 0 ? (
                  <EmptyState
                    className='min-h-20 py-4'
                    message={t('apps.noApps')}
                  />
                ) : (
                  <div className='grid gap-x-6 gap-y-1 sm:grid-cols-2 lg:grid-cols-3'>
                    {data.applications.map((application) => (
                      <a
                        key={application.id}
                        href={`/applications/${application.id}`}
                        className='flex min-w-0 items-center justify-between gap-3 border-b py-2 text-sm last:border-0 hover:bg-muted/30'
                      >
                        <span className='min-w-0 truncate font-medium'>
                          {application.name}
                        </span>
                        <StatusBadge
                          status={
                            data.applicationHealth[application.id] || 'unknown'
                          }
                        />
                      </a>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <Card className='h-full lg:col-span-3'>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-1'>
                <CardTitle>{t('dashboard.syncStatus')}</CardTitle>
                <Link
                  className='text-xs text-muted-foreground hover:text-foreground'
                  to='/agents'
                >
                  {t('common.viewAll')}{' '}
                  <ArrowUpRight className='inline size-3.5' />
                </Link>
              </CardHeader>
              <CardContent className='grid flex-1 grid-cols-3 items-center gap-2'>
                {syncCounts.map(({ status, count }) => (
                  <div
                    className='flex min-w-0 flex-col items-start gap-2'
                    key={status}
                  >
                    <StatusBadge status={status} />
                    <strong className='text-sm font-medium'>{count}</strong>
                  </div>
                ))}
              </CardContent>
            </Card>
          </div>

          <div className='flex flex-col gap-4'>
            <Card>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-1'>
                <CardTitle>{t('dashboard.recentExecutions')}</CardTitle>
                <Link
                  className='text-xs text-muted-foreground hover:text-foreground'
                  to='/executions'
                >
                  {t('common.viewAll')}{' '}
                  <ArrowUpRight className='inline size-3.5' />
                </Link>
              </CardHeader>
              <CardContent>
                {recent.length === 0 ? (
                  <EmptyState message={t('dashboard.noExecutions')} />
                ) : (
                  <RackList testId='dashboard-recent-executions'>
                    {recent.map((execution) => (
                      <RackRow
                        key={execution.id}
                        testId={`dashboard-recent-execution-${execution.id}`}
                        title={
                          <a
                            className='hover:underline'
                            href={`/executions/${execution.id}`}
                            title={
                              taskName.get(execution.task_id) ||
                              t('common.unknownTask')
                            }
                          >
                            {taskName.get(execution.task_id) ||
                              t('common.unknownTask')}
                          </a>
                        }
                        fields={[
                          {
                            label: t('dashboard.node'),
                            value:
                              nodeName.get(execution.node_id) ||
                              t('common.unknownNode'),
                          },
                          {
                            label: t('common.status'),
                            value: <StatusBadge status={execution.status} />,
                          },
                          {
                            label: t('dashboard.trigger'),
                            value:
                              execution.trigger_type === 'manual'
                                ? i18n.language === 'en'
                                  ? 'Manual'
                                  : '手动'
                                : i18n.language === 'en'
                                  ? 'Schedule'
                                  : '调度',
                          },
                          {
                            label: t('dashboard.end'),
                            value: (
                              <TimeValue
                                value={
                                  execution.end_time || execution.start_time
                                }
                              />
                            ),
                          },
                        ]}
                      />
                    ))}
                  </RackList>
                )}
              </CardContent>
            </Card>
            <Card>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-1'>
                <CardTitle>{t('dashboard.upcomingSchedules')}</CardTitle>
                <Link
                  className='text-xs text-muted-foreground hover:text-foreground'
                  to='/schedules'
                >
                  {t('common.viewAll')}{' '}
                  <ArrowUpRight className='inline size-3.5' />
                </Link>
              </CardHeader>
              <CardContent>
                {schedules.length === 0 ? (
                  <EmptyState message={t('schedules.noSchedules')} />
                ) : (
                  <div className='divide-y'>
                    {schedules.map((schedule) => (
                      <a
                        key={schedule.id}
                        href={`/schedules/${schedule.id}`}
                        className='flex items-center gap-3 py-3 first:pt-0 last:pb-0 hover:bg-muted/30'
                      >
                        <CalendarClock className='size-4 shrink-0 text-muted-foreground' />
                        <span className='min-w-0 flex-1'>
                          <span className='block truncate text-sm font-medium'>
                            {taskName.get(schedule.task_id) ||
                              t('common.unknownTask')}
                          </span>
                          <span className='font-mono text-xs text-muted-foreground'>
                            {schedule.type === 'interval'
                              ? `${i18n.language === 'en' ? 'Every ' : '每 '}${schedule.interval_sec}${i18n.language === 'en' ? 's' : ' 秒'}`
                              : schedule.expression || schedule.type}
                          </span>
                        </span>
                        <ArrowUpRight className='size-3.5 text-muted-foreground' />
                      </a>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </div>
      </Main>
    </>
  )
}

function CompactMetric({
  label,
  value,
  detail,
  testId,
}: {
  label: string
  value: string | number
  detail?: string
  testId?: string
}) {
  return (
    <div data-testid={testId} className='rounded-md border bg-muted/30 p-3'>
      <div className='text-xs text-muted-foreground'>{label}</div>
      <div className='text-xl font-semibold tracking-tight'>{value}</div>
      {detail ? (
        <p className='text-xs text-muted-foreground'>{detail}</p>
      ) : null}
    </div>
  )
}
