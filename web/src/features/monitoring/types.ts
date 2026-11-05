export type NodeOverview = {
  node_id: string
  hostname: string
  display_name?: string
  status: string
  last_seen?: string
  cpu?: number | null
  memory?: number | null
  load1?: number | null
  load5?: number | null
  load15?: number | null
  disk?: number | null
  network_rx_bps?: number | null
  network_tx_bps?: number | null
  network_total_bps?: number | null
  alerts?: number
  observed_at?: string
  temperature_c?: number | null
  uptime_seconds?: number | null
  container_metrics_available?: boolean
  state?: string
}

export type Series = { seriesKey: string; dimensions: Record<string, string> }

export type MonitoringTarget = {
  id: string
  name: string
  kind?: 'http' | 'tcp' | 'icmp' | 'node_metric'
  source_node_id?: string
  monitored_node_id?: string
  metric?: string
  series?: string
  max_age_sec?: number
  url: string
  json_path: string[]
  interval_sec: number
  timeout_sec: number
  enabled: boolean
  credential_env?: string
  query_params?: { name: string; value?: string; secret_env?: string }[]
}

export type MonitoringRule = {
  id: string
  name: string
  target_id: string
  schedule_id: string
  operator: string
  expected: string
  duration_sec: number
  cooldown_sec: number
  batch_size?: number
  verify_schedule_id?: string
  compensate_schedule_id?: string
  enabled: boolean
}

export type MonitoringObservation = {
  id: number
  observed_at: string
  status_code: number
  value: unknown
  error?: string
  duration_ns?: number
}

export type MonitoringRuleEvent = {
  id: string
  status: string
  observed_at: string
  execution_ids: string[]
  error?: string
  steps?: {
    execution_id: string
    node_id: string
    phase: string
    batch_index: number
    status: string
  }[]
}

export type History = {
  state: string
  tier?: string
  unit?: string
  points: {
    timestamp: string
    value: number | null
    quality?: number
    count?: number
  }[]
  gaps: { start: string; end: string }[]
  stepNs: number
}
