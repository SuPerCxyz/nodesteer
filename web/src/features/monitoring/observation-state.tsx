import { useTranslation } from 'react-i18next'
import { observationIsStale } from './freshness'

type Target = {
  kind?: string
  interval_sec: number
  max_age_sec?: number
  enabled: boolean
}
type Observation = {
  observed_at: string
  status_code: number
  value: unknown
  error?: string
}

export function ObservationState({
  target,
  observation,
  error,
  now,
}: {
  target: Target
  observation?: Observation
  error: boolean
  now: number
}) {
  const { t } = useTranslation()
  if (!target.enabled) return t('monitoring.disabled')
  if (error) return t('monitoring.queryFailed')
  if (!observation) return t('monitoring.awaitingSample')
  if (observationIsStale(target, observation.observed_at, now))
    return t('monitoring.stale')
  if (observation.error) return t('monitoring.probeFailed')
  if (target.kind === 'node_metric') return String(observation.value ?? '—')
  if (target.kind === 'tcp' || target.kind === 'icmp')
    return t('monitoring.reachable')
  return `HTTP ${observation.status_code}`
}
