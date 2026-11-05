type FreshnessTarget = {
  kind?: string
  interval_sec: number
  max_age_sec?: number
}

// Keep in sync with Target.MetricMaxAge and source timestamp validation.
export function observationIsStale(
  target: FreshnessTarget,
  observedAt: string,
  now: number
): boolean {
  const timestamp = Date.parse(observedAt)
  const seconds =
    target.kind === 'node_metric'
      ? target.max_age_sec && target.max_age_sec > 0
        ? target.max_age_sec
        : Math.min(target.interval_sec * 2, 300)
      : target.interval_sec * 2
  return (
    !Number.isFinite(timestamp) ||
    timestamp > now ||
    now - timestamp > seconds * 1000
  )
}
