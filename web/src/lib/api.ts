import { formatApiError } from './api-errors'

const API_BASE = '/api'

export interface ApiClientOptions {
  token?: string
}

const storage = typeof localStorage === 'undefined' ? null : localStorage
let authToken: string | null = storage?.getItem('cadentra_token') || null

export function setToken(token: string | null) {
  authToken = token
  if (token) {
    storage?.setItem('cadentra_token', token)
  } else {
    storage?.removeItem('cadentra_token')
  }
}

export function getToken(): string | null {
  return authToken
}

export class ApiError extends Error {
  status: number
  /** 后端原始错误信息（未经可读化映射） */
  rawMessage: string
  constructor(status: number, message: string) {
    super(message)
    this.status = status
    this.rawMessage = message
    this.message = formatApiError(message, status)
  }
}

async function requestWithResponse<T>(
  path: string,
  options: RequestInit = {}
): Promise<{ data: T; response: Response }> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...(options.headers as Record<string, string>),
  }
  if (authToken) {
    headers['Authorization'] = `Bearer ${authToken}`
  }
  const resp = await fetch(`${API_BASE}${path}`, { ...options, headers })
  if (resp.status === 401) {
    setToken(null)
    window.dispatchEvent(new Event('cadentra-unauthorized'))
  }
  if (!resp.ok) {
    let msg = `HTTP ${resp.status}`
    try {
      const data = await resp.json()
      if (data.error) msg = data.error
    } catch {
      /* ignore */
    }
    throw new ApiError(resp.status, msg)
  }
  if (resp.status === 204) return { data: undefined as T, response: resp }
  return { data: (await resp.json()) as T, response: resp }
}

async function request<T>(path: string, options: RequestInit = {}): Promise<T> {
  const { data } = await requestWithResponse<T>(path, options)
  return data
}

/** 分页列表响应：X-Total-Count 缺失或非法时 total 回退为当前页条数 */
export interface PageResult<T> {
  items: T[]
  total: number
}

export const api = {
  get: <T>(path: string) => request<T>(path),
  /**
   * GET 列表接口并读取 X-Total-Count 响应头。
   * 后端未提供该响应头（如旧版本）时回退为 items.length。
   */
  getPage: async <T>(path: string): Promise<PageResult<T>> => {
    const { data, response } = await requestWithResponse<T[]>(path)
    const items = Array.isArray(data) ? data : []
    const header = response.headers.get('X-Total-Count')
    const parsed = header === null ? Number.NaN : Number(header)
    return {
      items,
      total: Number.isFinite(parsed) && parsed >= 0 ? parsed : items.length,
    }
  },
  post: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    }),
  put: <T>(path: string, body?: unknown) =>
    request<T>(path, {
      method: 'PUT',
      body: body ? JSON.stringify(body) : undefined,
    }),
  del: <T>(path: string) => request<T>(path, { method: 'DELETE' }),
}

export interface Node {
  id: string
  agent_id: string
  hostname: string
  ip: string
  os: string
  arch: string
  agent_version: string
  deployment_mode: string
  host_integration: boolean
  status: string
  labels: Record<string, string>
  capabilities: Record<string, boolean>
  global_revision: number
  sync_status: string
  last_seen: string
  first_seen: string
  inventory?: Inventory
}

export interface NodeEnrollment {
  node_id?: string
  agent_id?: string
  gateway_url: string
  gateway_base_url: string
  agent_image: string
  native: string
  docker_run: string
  docker_compose: string
}

export interface FileTransferTarget {
  transfer_id: string
  node_id: string
  destination_path: string
  mode?: number
  status: string
  error?: string
  updated_at: string
}

export interface FileTransfer {
  id: string
  source_node_id: string
  source_path: string
  source_mode?: number
  size: number
  sha256?: string
  status: string
  error?: string
  created_at: string
  updated_at: string
  targets: FileTransferTarget[]
}

export interface Inventory {
  os: string
  os_version: string
  kernel: string
  arch: string
  cpu: { model: string; cores: number; mhz: number }[]
  memory?: { total_kb: number; available_kb: number }
  filesystem: {
    mount: string
    device: string
    fs_type: string
    total_kb: number
    free_kb: number
  }[]
  network: { interface: string; addresses: string[]; mac: string }[]
}

export interface Group {
  id: string
  name: string
  description: string
  type: string
  label_key: string
  label_value: string
  members: string[]
  created_at: string
}

export interface Script {
  id: string
  name: string
  description: string
  interpreter: string
  content: string
  parameters: Parameter[]
  environment: Record<string, string>
  working_dir: string
  run_user?: string
  timeout: number
  enabled: boolean
  revision: number
  sha256: string
  updated_at: string
  created_at: string
}

export interface Parameter {
  name: string
  type: string
  required: boolean
  default?: string
  description?: string
}

export interface Task {
  id: string
  name: string
  type: string
  target: Target
  parameters: Parameter[]
  param_values: Record<string, string>
  script_id?: string
  command?: string
  application_id?: string
  app_operation?: string
  condition?: Condition
  schedule?: Schedule
  timeout: number
  retry: number
  offline_policy: string
  run_user?: string
  enabled: boolean
  revision: number
  updated_at: string
  created_at: string
}

export interface Target {
  type: string
  node_ids?: string[]
  group_ids?: string[]
  label_key?: string
  label_value?: string
}

export interface Condition {
  type: string
  local?: LocalCondition
  and?: Condition[]
  remote?: RemoteCondition
}

export interface LocalCondition {
  metric: string
  operator: string
  value: string
  path?: string
  command?: string
}

export interface RemoteCondition {
  node_id: string
  property: string
  task_id?: string
  operator: string
  value: string
}

export interface Schedule {
  id: string
  task_id: string
  revision: number
  type: string
  expression: string
  interval_sec: number
  run_at: string
  timezone: string
  execution_owner: string
  offline_policy: string
  misfire_policy: string
  enabled: boolean
  created_at: string
  updated_at: string
}

export interface Artifact {
  id: string
  name: string
  version: string
  architecture: string
  filename: string
  size: number
  sha256: string
  uploaded_at: string
}

export interface Application {
  id: string
  name: string
  description: string
  artifact_id: string
  version: string
  binary_path: string
  arguments: string[]
  environment: Record<string, string>
  config: string
  config_path: string
  unit_name: string
  health_check?: HealthCheck
  revision: number
  updated_at: string
  created_at: string
}

export interface ApplicationNodeState {
  application_id: string
  node_id: string
  version?: string
  operation: string
  health: string
  error?: string
  updated_at: string
}

export interface HealthCheck {
  type: string
  target: string
  timeout: number
  attempts: number
  interval: number
}

export interface Execution {
  id: string
  task_id: string
  task_revision: number
  script_id: string
  script_revision: number
  node_id: string
  trigger_type: string
  scheduled_time: string
  start_time: string
  end_time: string
  status: string
  exit_code: number
  stdout: string
  stderr: string
  stdout_truncated: boolean
  stderr_truncated: boolean
  offline: boolean
  synced: boolean
  block_reason: string
}

export interface AuditLog {
  id: string
  user_id: string
  username: string
  action: string
  resource: string
  resource_id: string
  detail: string
  created_at: string
}

export interface User {
  id: string
  username: string
  role: string
  created_at: string
}

export interface Settings {
  [key: string]: string
}

export interface SessionInfo {
  Token: string
  UserID: string
  Username: string
  Role: string
  Expires: string
}
