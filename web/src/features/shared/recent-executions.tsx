import { useQuery } from '@tanstack/react-query'
import { Link } from '@tanstack/react-router'
import { useTranslation } from 'react-i18next'
import { api, type Execution } from '@/lib/api'
import {
  EmptyState,
  ErrorState,
  LoadingState,
  SectionCard,
  StatusBadge,
  TimeValue,
} from './ui'

export function RecentExecutions({ scheduleId }: { scheduleId: string }) {
  const { t } = useTranslation()
  const query = useQuery({
    queryKey: ['recent-schedule-executions', scheduleId],
    queryFn: () =>
      api.get<Execution[]>(
        `/executions?schedule_id=${encodeURIComponent(scheduleId)}&limit=5`
      ),
    refetchInterval: 15000,
  })
  return (
    <SectionCard
      title={t('apps.executionHistory')}
      action={
        <Link to='/executions' search={{ schedule_id: scheduleId }}>
          {t('common.viewAll')}
        </Link>
      }
    >
      {query.isLoading ? (
        <LoadingState />
      ) : query.isError ? (
        <ErrorState error={query.error} onRetry={() => query.refetch()} />
      ) : !query.data?.length ? (
        <EmptyState message={t('executions.noExecs')} />
      ) : (
        <ul className='divide-y'>
          {query.data.map((execution) => (
            <li
              key={execution.id}
              className='flex flex-wrap items-center justify-between gap-3 py-2 text-sm'
            >
              <Link
                to='/executions/$executionId'
                params={{ executionId: execution.id }}
                className='text-primary hover:underline'
              >
                <TimeValue
                  value={execution.start_time || execution.created_at}
                />
              </Link>
              <StatusBadge status={execution.status} />
            </li>
          ))}
        </ul>
      )}
    </SectionCard>
  )
}
