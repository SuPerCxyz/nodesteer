import { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import {
  Activity,
  ArrowUpRight,
  CalendarClock,
  CheckCircle2,
  CircleAlert,
  Server,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import {
  api,
  type Application,
  type ApplicationNodeState,
  type Execution,
  type Node,
  type Schedule,
  type Task,
} from '@/lib/api'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { CadentraHeader } from '@/components/layout/cadentra-header'
import { Main } from '@/components/layout/main'
import {
  DurationValue,
  EmptyState,
  ErrorState,
  LoadingState,
  StatusBadge,
  TableCard,
  TimeValue,
} from '@/features/shared/ui'

type DashboardData = {
  nodes: Node[]
  executions: Execution[]
  applications: Application[]
  applicationHealth: Record<string, string>
  schedules: Schedule[]
  tasks: Task[]
  healthyApplications: number
}

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
  const applicationStates = await Promise.all(
    applications.map((application) =>
      api
        .get<ApplicationNodeState[]>(`/applications/${application.id}/state`)
        .catch(() => [])
    )
  )
  const applicationHealth = Object.fromEntries(
    applications.map((application, index) => {
      const states = applicationStates[index]
      const health = states.some((state) => state.health === 'healthy')
        ? 'healthy'
        : states.some((state) => state.health === 'unhealthy')
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
    schedules,
    tasks,
    healthyApplications,
  }
}

export function Dashboard() {
  const { t, i18n } = useTranslation()
  const query = useQuery({
    queryKey: ['dashboard'],
    queryFn: loadDashboard,
    refetchInterval: 15000,
  })
  const data = query.data
  const taskName = useMemo(
    () => new Map((data?.tasks || []).map((task) => [task.id, task.name])),
    [data?.tasks]
  )
  const nodeName = useMemo(
    () => new Map((data?.nodes || []).map((node) => [node.id, node.hostname])),
    [data?.nodes]
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
  const offline =
    data?.nodes.filter((node) => node.status === 'offline').length || 0
  const degraded = (data?.nodes.length || 0) - online - offline
  const syncCounts = ['synced', 'outdated', 'error'].map((status) => ({
    status,
    count:
      data?.nodes.filter((node) => node.sync_status?.toLowerCase() === status)
        .length || 0,
  }))

  if (query.isLoading)
    return (
      <>
        <CadentraHeader
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
        <CadentraHeader title={t('dashboard.title')} />
        <Main>
          <ErrorState error={query.error} onRetry={() => query.refetch()} />
        </Main>
      </>
    )

  return (
    <>
      <CadentraHeader
        title={t('dashboard.title')}
        description={t('dashboard.description')}
      />
      <Main fluid className='flex flex-1 flex-col'>
        <div className='mx-auto flex w-full max-w-[1600px] flex-col gap-6'>
          <div className='grid gap-4 sm:grid-cols-2 lg:grid-cols-4'>
            <MetricCard
              label={t('dashboard.totalNodes')}
              value={data.nodes.length}
              detail={`${online} ${t('dashboard.online').toLowerCase()}`}
              icon={Server}
            />
            <MetricCard
              label={t('dashboard.runningNow')}
              value={
                data.executions.filter(
                  (execution) => execution.status === 'RUNNING'
                ).length
              }
              detail={t('dashboard.runningExecutions')}
              icon={Activity}
            />
            <MetricCard
              label={t('dashboard.execFailed')}
              value={failed}
              detail={t('dashboard.recentWindow')}
              icon={CircleAlert}
            />
            <MetricCard
              label={t('dashboard.successRate')}
              value={successRate}
              detail={`${terminal} ${t('dashboard.completedRuns')}`}
              icon={CheckCircle2}
            />
          </div>

          <div className='grid grid-cols-1 items-stretch gap-4 lg:grid-cols-7'>
            <Card className='h-full gap-3 py-4 lg:col-span-4'>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 px-5 pb-1'>
                <CardTitle className='text-sm'>
                  {t('dashboard.runningExecutions')}
                </CardTitle>
                <Link
                  className='text-xs text-muted-foreground hover:text-foreground'
                  to='/executions'
                >
                  {t('common.viewAll')}{' '}
                  <ArrowUpRight className='inline size-3.5' />
                </Link>
              </CardHeader>
              <CardContent
                className={
                  running.length === 0
                    ? 'flex flex-1 items-center justify-center px-5'
                    : 'px-5'
                }
              >
                {running.length === 0 ? (
                  <EmptyState
                    className='min-h-20 py-4'
                    message={t('dashboard.noRunning')}
                  />
                ) : (
                  <div className='divide-y'>
                    {running.map((execution) => (
                      <a
                        key={execution.id}
                        href={`/executions/${execution.id}`}
                        className='flex items-center gap-3 py-3 first:pt-0 last:pb-0 hover:bg-muted/30'
                      >
                        <Activity className='size-4 shrink-0 text-muted-foreground' />
                        <span className='min-w-0 flex-1'>
                          <span className='block truncate text-sm font-medium'>
                            {taskName.get(execution.task_id) ||
                              t('common.unknownTask')}
                          </span>
                          <span className='truncate text-xs text-muted-foreground'>
                            {nodeName.get(execution.node_id) ||
                              t('common.unknownNode')}
                          </span>
                        </span>
                        <DurationValue start={execution.start_time} running />
                        <StatusBadge status={execution.status} />
                      </a>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>
            <Card className='h-full gap-3 py-4 lg:col-span-3'>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 px-5 pb-1'>
                <CardTitle className='text-sm'>
                  {t('dashboard.agentHealth')}
                </CardTitle>
                <Link
                  className='text-xs text-muted-foreground hover:text-foreground'
                  to='/agents'
                >
                  {t('common.viewAll')}{' '}
                  <ArrowUpRight className='inline size-3.5' />
                </Link>
              </CardHeader>
              <CardContent className='flex flex-1 flex-col justify-center px-5'>
                <div className='grid grid-cols-3 gap-2'>
                  {[
                    ['online', online, 'success'],
                    ['offline', offline, 'danger'],
                    ['degraded', degraded, 'warning'],
                  ].map(([label, value, tone]) => (
                    <div className='min-w-0 space-y-1' key={label as string}>
                      <div className='flex h-4 items-center gap-1.5 text-xs leading-4 text-muted-foreground'>
                        <span
                          className={`size-1.5 shrink-0 self-center rounded-full ${tone === 'success' ? 'bg-emerald-500' : tone === 'danger' ? 'bg-destructive' : 'bg-amber-500'}`}
                        />
                        <span className='truncate'>
                          {t(`dashboard.${label}`)}
                        </span>
                      </div>
                      <strong className='text-sm font-medium'>{value}</strong>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>

          <div className='grid grid-cols-1 items-stretch gap-4 lg:grid-cols-7'>
            <Card className='h-full gap-3 py-4 lg:col-span-4'>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 px-5 pb-1'>
                <CardTitle className='text-sm'>
                  {t('dashboard.applicationStatus')}
                </CardTitle>
                <div className='flex items-center gap-3'>
                  <span className='text-xs text-muted-foreground'>
                    {data.healthyApplications}/{data.applications.length}{' '}
                    {t('dashboard.appsHealthy').toLowerCase()}
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
              <CardContent className='px-5'>
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

            <Card className='h-full gap-3 py-4 lg:col-span-3'>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 px-5 pb-1'>
                <CardTitle className='text-sm'>
                  {t('dashboard.syncStatus')}
                </CardTitle>
                <Link
                  className='text-xs text-muted-foreground hover:text-foreground'
                  to='/agents'
                >
                  {t('common.viewAll')}{' '}
                  <ArrowUpRight className='inline size-3.5' />
                </Link>
              </CardHeader>
              <CardContent className='grid flex-1 grid-cols-3 items-center gap-2 px-5'>
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

          <div className='grid grid-cols-1 items-start gap-4 lg:grid-cols-7'>
            <Card className='lg:col-span-4'>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-3'>
                <CardTitle className='text-sm'>
                  {t('dashboard.recentExecutions')}
                </CardTitle>
                <Link
                  className='text-xs text-muted-foreground hover:text-foreground'
                  to='/executions'
                >
                  {t('common.viewAll')}{' '}
                  <ArrowUpRight className='inline size-3.5' />
                </Link>
              </CardHeader>
              <CardContent className='px-0'>
                <TableCard>
                  <Table className='min-w-[700px] table-fixed'>
                    <colgroup>
                      <col className='w-[30%]' />
                      <col className='w-[15%]' />
                      <col className='w-[14%]' />
                      <col className='w-[17%]' />
                      <col className='w-[24%]' />
                    </colgroup>
                    <TableHeader>
                      <TableRow>
                        <TableHead>{t('dashboard.task')}</TableHead>
                        <TableHead>{t('dashboard.node')}</TableHead>
                        <TableHead className='text-center'>
                          {t('common.status')}
                        </TableHead>
                        <TableHead>{t('dashboard.trigger')}</TableHead>
                        <TableHead>{t('dashboard.end')}</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {recent.map((execution) => (
                        <TableRow key={execution.id}>
                          <TableCell>
                            <a
                              className='font-medium hover:underline'
                              href={`/executions/${execution.id}`}
                            >
                              {taskName.get(execution.task_id) ||
                                t('common.unknownTask')}
                            </a>
                          </TableCell>
                          <TableCell className='truncate text-xs'>
                            {nodeName.get(execution.node_id) ||
                              t('common.unknownNode')}
                          </TableCell>
                          <TableCell className='text-center'>
                            <StatusBadge status={execution.status} />
                          </TableCell>
                          <TableCell>
                            {execution.trigger_type === 'manual'
                              ? i18n.language === 'en'
                                ? 'Manual'
                                : '手动'
                              : i18n.language === 'en'
                                ? 'Schedule'
                                : '调度'}
                          </TableCell>
                          <TableCell>
                            <TimeValue
                              value={execution.end_time || execution.start_time}
                            />
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </TableCard>
              </CardContent>
            </Card>
            <Card className='lg:col-span-3'>
              <CardHeader className='flex flex-row items-center justify-between space-y-0 pb-3'>
                <CardTitle className='text-sm'>
                  {t('dashboard.upcomingSchedules')}
                </CardTitle>
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

function MetricCard({
  label,
  value,
  detail,
  icon: Icon,
}: {
  label: string
  value: string | number
  detail: string
  icon: typeof Server
}) {
  return (
    <Card className='gap-3 py-4'>
      <CardHeader className='flex flex-row items-center justify-between space-y-0 px-5 pb-1'>
        <CardTitle className='text-sm font-medium'>{label}</CardTitle>
        <Icon className='size-4 text-muted-foreground' />
      </CardHeader>
      <CardContent className='px-5'>
        <div className='text-2xl font-semibold tracking-tight'>{value}</div>
        <p className='text-xs text-muted-foreground'>{detail}</p>
      </CardContent>
    </Card>
  )
}
