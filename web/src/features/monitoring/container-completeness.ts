export type LatestMetric = {
  metric: string
  series: string
  value: number | null
  state: string
  observed_at: string
}

export function containerCompleteness(metrics: LatestMetric[]) {
  const names = [
    'container.collection.discovered_count',
    'container.collection.sampled_count',
    'container.collection.limit',
  ]
  const values = names.map((name) =>
    metrics.find((item) => item.metric === name && item.series === name)
  )
  if (
    values.some(
      (item) =>
        !item ||
        item.state !== 'ready' ||
        !Number.isSafeInteger(item.value) ||
        (item.value as number) < 0
    )
  )
    return null
  const [discovered, sampled, limit] = values.map(
    (item) => item!.value as number
  )
  if (
    !Number.isFinite(Date.parse(values[0]!.observed_at)) ||
    values.some((item) => item!.observed_at !== values[0]!.observed_at) ||
    limit <= 0 ||
    sampled > Math.min(discovered, limit)
  )
    return null
  const observed = Date.parse(values[0]!.observed_at)
  if (
    observed > Date.now() ||
    metrics.some((item) => Date.parse(item.observed_at) > observed)
  )
    return null
  return { discovered, sampled, limit, partial: sampled < discovered }
}
