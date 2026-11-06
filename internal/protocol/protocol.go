package protocol

import (
	"encoding/json"
	"time"
)

// ProtocolVersion 当前协议版本
const ProtocolVersion = 3

// InboundProtocolVersion 入站握手协议版本。
//
// HUB_HELLO/HUB_HELLO_ACK 只出现在 Hub 主动拨号的入站连接上，旧 Hub/旧 Agent
// 不会收到这些消息，因此不改变 ProtocolVersion。
const InboundProtocolVersion = 1

// 消息类型
const (
	MsgHubHello            = "HUB_HELLO"
	MsgHubHelloAck         = "HUB_HELLO_ACK"
	MsgHello               = "HELLO"
	MsgHelloAck            = "HELLO_ACK"
	MsgHeartbeat           = "HEARTBEAT"
	MsgHeartbeatAck        = "HEARTBEAT_ACK"
	MsgChangeNotif         = "CHANGE_NOTIFICATION"
	MsgRevisionCheck       = "REVISION_CHECK"
	MsgRevisionCheckAck    = "REVISION_CHECK_ACK"
	MsgSyncRequest         = "SYNC_REQUEST"
	MsgSyncResponse        = "SYNC_RESPONSE"
	MsgSyncAck             = "SYNC_ACK"
	MsgRunExecution        = "RUN_EXECUTION"
	MsgCancelExecution     = "CANCEL_EXECUTION"
	MsgExecStarted         = "EXECUTION_STARTED"
	MsgExecFinished        = "EXECUTION_FINISHED"
	MsgLogChunk            = "LOG_CHUNK"
	MsgArtifactPrefetch    = "ARTIFACT_PREFETCH"
	MsgDeployRequest       = "DEPLOY_REQUEST"
	MsgDeployResult        = "DEPLOY_RESULT"
	MsgExecutionAck        = "EXECUTION_ACK"
	MsgSettings            = "SETTINGS"
	MsgNodeStatus          = "NODE_STATUS"
	MsgRemoteState         = "REMOTE_STATE"
	MsgRemoteStateReq      = "REMOTE_STATE_REQ"
	MsgInventory           = "INVENTORY"
	MsgFileUploadRequest   = "FILE_UPLOAD_REQUEST"
	MsgFileUploadResult    = "FILE_UPLOAD_RESULT"
	MsgFileDeliveryRequest = "FILE_DELIVERY_REQUEST"
	MsgFileDeliveryResult  = "FILE_DELIVERY_RESULT"
	MsgFileTransferCancel  = "FILE_TRANSFER_CANCEL"
	MsgError               = "ERROR"
	MsgMonitorProbe        = "MONITOR_PROBE"
	MsgMonitorProbeResult  = "MONITOR_PROBE_RESULT"
	MsgNodeMetrics         = "NODE_METRICS"
	// MsgNodeMetricsAck Hub 在指标批次提交成功后确认；旧 Agent 忽略未知消息。
	MsgNodeMetricsAck = "NODE_METRICS_ACK"
)

// CapabilityNodeMetricsAck 由 Hub 在 HELLO_ACK/HUB_HELLO 中声明。
// Agent 未看到该能力时回退为“WebSocket 写成功即出队”，保持旧 Hub 兼容。
const CapabilityNodeMetricsAck = "node_metrics_ack"

// 数据面传输方式。空值表示沿用既有出站语义（Agent 主动访问 Hub HTTP 端点）。
const (
	TransportOutbound    = "outbound"
	TransportInboundPull = "inbound_pull"
	TransportInboundPush = "inbound_push"
)

// 入站连接稳定错误码（API 409 / Execution BLOCKED 共用）。
const (
	ErrorCodeInboundDataPlaneUnavailable = "inbound_data_plane_unavailable"
	ErrorCodeInboundStageUnavailable     = "inbound_stage_unavailable"
	ErrorCodeInboundAgentUnsupported     = "inbound_agent_unsupported"
	ErrorCodeInboundEndpointInvalid      = "inbound_endpoint_invalid"
	ErrorCodeInboundPinMismatch          = "inbound_pin_mismatch"
	ErrorCodeInboundCertificateExpired   = "inbound_certificate_expired"
	ErrorCodeInboundAuthFailed           = "inbound_auth_failed"
	ErrorCodeInboundNotProvisioned       = "inbound_not_provisioned"
	ErrorCodeInboundDisabled             = "inbound_disabled"
	ErrorCodeInboundBundleAlreadyIssued  = "inbound_bundle_already_issued"
)

// NodeMetricSample 节点指标单点样本。
// Quality 为 1 表示有效值，2 表示缺失；缺失时 Value 必须为 nil，不得用 0 代替。
type NodeMetricSample struct {
	Timestamp  time.Time         `json:"timestamp"`
	Metric     string            `json:"metric"`
	Dimensions map[string]string `json:"dimensions,omitempty"`
	Value      *float64          `json:"value"`
	Quality    int               `json:"quality"` // 1=valid, 2=missing
}

// NodeMetricsPayload 节点指标批次。Sequence 在同一 Agent 进程内单调递增。
type NodeMetricsPayload struct {
	NodeID   string             `json:"node_id"`
	Sequence uint64             `json:"sequence"`
	Samples  []NodeMetricSample `json:"samples"`
}

// NodeMetricsAckPayload Hub 对指标批次的提交确认。
// OK=false 表示 Hub 拒绝该批次（校验失败）；Agent 可保留并等待人工/重试，
// 当前实现只在成功提交后发送 OK=true。
type NodeMetricsAckPayload struct {
	NodeID   string `json:"node_id"`
	Sequence uint64 `json:"sequence"`
	OK       bool   `json:"ok"`
	Error    string `json:"error,omitempty"`
}

// 节点指标质量值。
const (
	NodeMetricQualityValid   = 1
	NodeMetricQualityMissing = 2
)

type MonitorProbePayload struct {
	Target       json.RawMessage `json:"target"`
	AllowedCIDRs []string        `json:"allowed_cidrs"`
}

type MonitorProbeResultPayload struct {
	StatusCode int             `json:"status_code"`
	DurationNS int64           `json:"duration_ns"`
	Value      json.RawMessage `json:"value,omitempty"`
	HasValue   bool            `json:"has_value"`
	ErrorCode  string          `json:"error_code,omitempty"`
}

// Envelope 统一消息信封
type Envelope struct {
	Type      string          `json:"type"`
	ID        string          `json:"id"`
	Timestamp time.Time       `json:"timestamp"`
	Payload   json.RawMessage `json:"payload,omitempty"`
}

// NewEnvelope 构造消息
func NewEnvelope(msgType string, id string, payload any) Envelope {
	var raw json.RawMessage
	if payload != nil {
		b, _ := json.Marshal(payload)
		raw = b
	}
	return Envelope{
		Type:      msgType,
		ID:        id,
		Timestamp: time.Now().UTC(),
		Payload:   raw,
	}
}

// HelloPayload Agent 握手
type HelloPayload struct {
	ProtocolVersion int             `json:"protocol_version"`
	AgentID         string          `json:"agent_id,omitempty"`
	AgentVersion    string          `json:"agent_version"`
	DeploymentMode  string          `json:"deployment_mode"`
	HostIntegration bool            `json:"host_integration"`
	Capabilities    map[string]bool `json:"capabilities"`
	Hostname        string          `json:"hostname"`
	IP              string          `json:"ip"`
	OS              string          `json:"os"`
	Arch            string          `json:"arch"`
	RegistrationKey string          `json:"registration_key,omitempty"`
	AgentCredential string          `json:"agent_credential,omitempty"`
	LocalGlobalRev  int64           `json:"local_global_rev"`
	// InboundEndpoint 可选：Agent 配置的对外入站地址元数据，仅用于观测；
	// Hub 的拨号目标仍以管理员配置的节点 endpoint 为准。
	InboundEndpoint string `json:"inbound_endpoint,omitempty"`
}

// HelloAckPayload 握手响应
type HelloAckPayload struct {
	Accepted         bool              `json:"accepted"`
	NodeID           string            `json:"node_id,omitempty"`
	AgentID          string            `json:"agent_id,omitempty"`
	AgentCredential  string            `json:"agent_credential,omitempty"`
	DesiredGlobalRev int64             `json:"desired_global_rev"`
	Settings         map[string]string `json:"settings,omitempty"`
	Message          string            `json:"message,omitempty"`
	NodeStatus       string            `json:"node_status,omitempty"`
	// Capabilities 是 Hub 侧可选能力声明（如 node_metrics_ack）；旧 Agent 忽略。
	Capabilities map[string]bool `json:"capabilities,omitempty"`
}

// HubHelloPayload Hub 主动拨号时的首帧（Hub -> Agent）。
//
// 嵌入 HelloAckPayload 复用 accepted/node_id/agent_id/desired_global_rev/
// settings/message/node_status 语义；JSON 字段平铺，旧客户端不会收到该消息。
type HubHelloPayload struct {
	HelloAckPayload
	HubID      string    `json:"hub_id"`
	HubVersion string    `json:"hub_version"`
	Nonce      string    `json:"nonce"`
	IssuedAt   time.Time `json:"issued_at"`
	// DataPlane 表示 Hub 侧已具备入站数据面编排能力。
	DataPlane bool `json:"data_plane"`
	// Test 为 true 时 Agent 只完成握手探测，不注册业务会话。
	Test bool `json:"test,omitempty"`
}

// HubHelloAckPayload Agent 对 HubHelloPayload 的响应（Agent -> Hub）。
//
// TokenProof 为 HMAC-SHA256(node_token, "nodesteer-inbound-v1" | hub_nonce |
// agent_nonce | node_id)，令牌本身不在连接上传输。
type HubHelloAckPayload struct {
	HelloAckPayload
	Nonce      string `json:"nonce"`
	AgentNonce string `json:"agent_nonce"`
	TokenProof string `json:"token_proof"`
	DataPlane  bool   `json:"data_plane"`
	SessionID  string `json:"session_id,omitempty"`
}

// HeartbeatPayload 心跳
type HeartbeatPayload struct {
	NodeID       string `json:"node_id"`
	GlobalRev    int64  `json:"global_rev"`
	RunningExecs int    `json:"running_execs"`
	// 以下为 Agent 运行统计，旧 Hub 忽略未知字段。
	MetricsBufferBatches    int    `json:"metrics_buffer_batches,omitempty"`
	MetricsBufferBytes      int    `json:"metrics_buffer_bytes,omitempty"`
	MetricsOldestPendingSec int64  `json:"metrics_oldest_pending_sec,omitempty"`
	MetricsDroppedTotal     uint64 `json:"metrics_dropped_total,omitempty"`
	MetricsAckTimeouts      uint64 `json:"metrics_ack_timeouts,omitempty"`
	WSQueueDepth            int    `json:"ws_queue_depth,omitempty"`
	WSQueueBytes            int    `json:"ws_queue_bytes,omitempty"`
}

// ChangeNotificationPayload 变更通知
type ChangeNotificationPayload struct {
	ObjectType     string `json:"object_type"`
	ObjectID       string `json:"object_id"`
	ObjectRevision int64  `json:"object_revision"`
	GlobalRevision int64  `json:"global_revision"`
}

// SyncRequestPayload 同步请求
type SyncRequestPayload struct {
	Since int64 `json:"since"`
}

// SyncResponsePayload 同步响应
type SyncResponsePayload struct {
	FullResync bool          `json:"full_resync"`
	Snapshot   bool          `json:"snapshot"`
	GlobalRev  int64         `json:"global_rev"`
	Scripts    []ObjectEntry `json:"scripts,omitempty"`
	Tasks      []ObjectEntry `json:"tasks,omitempty"`
	Schedules  []ObjectEntry `json:"schedules,omitempty"`
	Apps       []ObjectEntry `json:"apps,omitempty"`
	Tombstones []Tombstone   `json:"tombstones,omitempty"`
}

// ObjectEntry 同步对象条目（含定义 JSON）
type ObjectEntry struct {
	ID       string          `json:"id"`
	Revision int64           `json:"revision"`
	Deleted  bool            `json:"deleted,omitempty"`
	Data     json.RawMessage `json:"data,omitempty"`
}

// Tombstone 删除标记
type Tombstone struct {
	ObjectType string `json:"object_type"`
	ObjectID   string `json:"object_id"`
	Revision   int64  `json:"revision"`
}

// SyncAckPayload 同步确认
type SyncAckPayload struct {
	AppliedRev int64  `json:"applied_rev"`
	OK         bool   `json:"ok"`
	Error      string `json:"error,omitempty"`
}

// RunExecutionPayload 执行下发
type RunExecutionPayload struct {
	ExecutionID        string            `json:"execution_id"`
	TaskID             string            `json:"task_id"`
	TaskRevision       int64             `json:"task_revision"`
	ScriptID           string            `json:"script_id,omitempty"`
	ScriptRevision     int64             `json:"script_revision,omitempty"`
	Type               string            `json:"type"`
	Command            string            `json:"command,omitempty"`
	ScriptContent      string            `json:"script_content,omitempty"`
	Interpreter        string            `json:"interpreter,omitempty"`
	Parameters         map[string]string `json:"parameters,omitempty"`
	SecretValues       []string          `json:"secret_values,omitempty"`
	Environment        map[string]string `json:"environment,omitempty"`
	WorkingDir         string            `json:"working_dir,omitempty"`
	RunUser            string            `json:"run_user,omitempty"`
	Timeout            int               `json:"timeout"`
	Retry              int               `json:"retry"`
	AppID              string            `json:"app_id,omitempty"`
	ApplicationVersion string            `json:"application_version,omitempty"`
	AppOperation       string            `json:"app_operation,omitempty"`
	ScheduledTime      string            `json:"scheduled_time,omitempty"`
	// ScheduleID 调度触发时的来源 Schedule；Manual Run 为空。
	ScheduleID       string          `json:"schedule_id,omitempty"`
	ScheduleRevision int64           `json:"schedule_revision,omitempty"`
	CreatedAt        string          `json:"created_at,omitempty"`
	TriggerType      string          `json:"trigger_type"`
	Condition        json.RawMessage `json:"condition,omitempty"`
	ArtifactID       string          `json:"artifact_id,omitempty"`
	TargetVersion    string          `json:"target_version,omitempty"`
	// ArtifactTransport 为空表示沿用既有出站下载；入站模式为 inbound_push。
	ArtifactTransport string `json:"artifact_transport,omitempty"`
	// AgentBinaryStaged/AgentBinarySHA256 仅入站自升级使用：Hub 已把二进制推送到
	// Agent 固定 staging 路径，Agent 不再依赖 DownloadURL。
	AgentBinaryStaged bool   `json:"agent_binary_staged,omitempty"`
	AgentBinarySHA256 string `json:"agent_binary_sha256,omitempty"`
	AgentBinarySize   int64  `json:"agent_binary_size,omitempty"`
	// DownloadURL Agent 自升级（type=agent_upgrade）的二进制下载地址，
	// 由 Hub 指定（既有 GET /api/agent/binary 端点）；为空时 Agent 侧按 HubURL 派生兜底。
	DownloadURL string `json:"download_url,omitempty"`
}

// CancelExecutionPayload 取消
type CancelExecutionPayload struct {
	ExecutionID string `json:"execution_id"`
}

// ExecutionStartedPayload 执行开始
type ExecutionStartedPayload struct {
	ExecutionID string `json:"execution_id"`
	StartTime   string `json:"start_time"`
}

// ExecutionFinishedPayload 执行完成
type ExecutionFinishedPayload struct {
	ExecutionID          string `json:"execution_id"`
	TaskID               string `json:"task_id,omitempty"`
	TaskRevision         int64  `json:"task_revision,omitempty"`
	ScriptID             string `json:"script_id,omitempty"`
	ScriptRevision       int64  `json:"script_revision,omitempty"`
	ApplicationID        string `json:"application_id,omitempty"`
	ApplicationVersion   string `json:"application_version,omitempty"`
	ApplicationOperation string `json:"application_operation,omitempty"`
	ApplicationHealth    string `json:"application_health,omitempty"`
	NodeID               string `json:"node_id,omitempty"`
	TriggerType          string `json:"trigger_type,omitempty"`
	ScheduledTime        string `json:"scheduled_time,omitempty"`
	// ScheduleID 来源调度；手动执行与旧 Agent 为空。
	ScheduleID       string `json:"schedule_id,omitempty"`
	ScheduleRevision int64  `json:"schedule_revision,omitempty"`
	// CreatedAt Hub 创建执行的时间（Manual Run 由 Hub 下发，调度触发由调度方写入）。
	CreatedAt       string `json:"created_at,omitempty"`
	Status          string `json:"status"`
	ExitCode        int    `json:"exit_code"`
	Stdout          string `json:"stdout"`
	Stderr          string `json:"stderr"`
	StdoutTruncated bool   `json:"stdout_truncated"`
	StderrTruncated bool   `json:"stderr_truncated"`
	StartTime       string `json:"start_time,omitempty"`
	EndTime         string `json:"end_time"`
	Offline         bool   `json:"offline"`
	BlockReason     string `json:"block_reason,omitempty"`
}

// ExecutionAckPayload 执行结果接收确认
type ExecutionAckPayload struct {
	ExecutionID string `json:"execution_id"`
	OK          bool   `json:"ok"`
	Error       string `json:"error,omitempty"`
}

// SettingsPayload Agent 运行期设置
type SettingsPayload struct {
	HeartbeatSec     int `json:"heartbeat_sec,omitempty"`
	RevisionCheckSec int `json:"revision_check_sec,omitempty"`
	MaxLogBytes      int `json:"max_log_bytes,omitempty"`
}

// NodeStatusPayload Hub 下发的节点生命周期状态。
type NodeStatusPayload struct {
	Status string `json:"status"`
}

// LogChunkPayload 日志分片
type LogChunkPayload struct {
	ExecutionID string `json:"execution_id"`
	Stream      string `json:"stream"` // stdout | stderr
	Chunk       string `json:"chunk"`
	Seq         int64  `json:"seq"`
}

// ArtifactPrefetchPayload 制品预取
type ArtifactPrefetchPayload struct {
	ArtifactID string `json:"artifact_id"`
	URL        string `json:"url"`
	SHA256     string `json:"sha256"`
	Size       int64  `json:"size"`
	// Transport 为空/outbound 表示 Agent 按 URL 下载；inbound_push 表示 Hub 会通过
	// 入站数据面推送，URL 为空。
	Transport string `json:"transport,omitempty"`
}

// FileUploadRequestPayload Hub 要求源 Agent 上传文件。
//
// Attempt 是 Hub 为本次上传尝试分配的世代号（单调递增）。Agent 必须在结果回执中
// 原样回传，Hub 只接受「当前世代」的结果；断连重连后 ResumeNode 重投会分配新世代，
// 被取代的旧尝试的取消/失败回执据此被忽略（缺陷 SOAK-20261018-01）。
type FileUploadRequestPayload struct {
	TransferID string `json:"transfer_id"`
	SourcePath string `json:"source_path"`
	UploadURL  string `json:"upload_url"`
	Offset     int64  `json:"offset"`
	MaxBytes   int64  `json:"max_bytes"`
	Attempt    uint64 `json:"attempt,omitempty"`
	// Transport 为空/outbound 表示 Agent 向 UploadURL 上传；inbound_pull 表示
	// Hub 通过入站数据面按 offset 拉取，UploadURL 为空。
	Transport string `json:"transport,omitempty"`
}

// FileUploadResultPayload 源 Agent 上传结果。
type FileUploadResultPayload struct {
	TransferID string `json:"transfer_id"`
	OK         bool   `json:"ok"`
	Error      string `json:"error,omitempty"`
	// Attempt 回传 FileUploadRequestPayload.Attempt，用于世代匹配。
	Attempt uint64 `json:"attempt,omitempty"`
}

// FileDeliveryRequestPayload Hub 要求目标 Agent 下载并写入文件。
//
// Attempt 语义同 FileUploadRequestPayload：目标交付同样按世代号去重，
// 被取代的旧下载尝试的取消回执不得把目标/传输拉回 FAILED。
type FileDeliveryRequestPayload struct {
	TransferID      string `json:"transfer_id"`
	DownloadURL     string `json:"download_url"`
	SHA256          string `json:"sha256"`
	Size            int64  `json:"size"`
	DestinationPath string `json:"destination_path"`
	Mode            uint32 `json:"mode,omitempty"`
	Attempt         uint64 `json:"attempt,omitempty"`
	// Transport 为空/outbound 表示 Agent 从 DownloadURL 下载；inbound_push 表示
	// Hub 通过入站数据面推送，DownloadURL 为空。
	Transport string `json:"transport,omitempty"`
}

// FileDeliveryResultPayload 目标 Agent 交付结果。
type FileDeliveryResultPayload struct {
	TransferID string `json:"transfer_id"`
	OK         bool   `json:"ok"`
	Error      string `json:"error,omitempty"`
	// Attempt 回传 FileDeliveryRequestPayload.Attempt，用于世代匹配。
	Attempt uint64 `json:"attempt,omitempty"`
}

// FileTransferCancelPayload 取消文件传输。
type FileTransferCancelPayload struct {
	TransferID string `json:"transfer_id"`
}

// DeployRequestPayload 部署请求
type DeployRequestPayload struct {
	AppID          string `json:"app_id"`
	AppVersion     string `json:"app_version"`
	AppRevision    int64  `json:"app_revision"`
	ArtifactID     string `json:"artifact_id"`
	ArtifactURL    string `json:"artifact_url"`
	ArtifactSHA256 string `json:"artifact_sha256"`
	// ArtifactTransport 为空表示沿用既有出站下载；inbound_push 表示 Hub 已推送
	// Artifact、ArtifactURL 为空，Agent 只从本地缓存取用。
	ArtifactTransport string            `json:"artifact_transport,omitempty"`
	BinaryPath        string            `json:"binary_path"`
	Arguments         []string          `json:"arguments"`
	Environment       map[string]string `json:"environment"`
	Config            string            `json:"config"`
	ConfigPath        string            `json:"config_path,omitempty"`
	UnitName          string            `json:"unit_name"`
	HealthCheck       json.RawMessage   `json:"health_check,omitempty"`
	Operation         string            `json:"operation"` // deploy | start | stop | restart | upgrade
	TriggerType       string            `json:"trigger_type,omitempty"`
	ScheduleID        string            `json:"schedule_id,omitempty"`
	ScheduleRevision  int64             `json:"schedule_revision,omitempty"`
	CreatedAt         string            `json:"created_at,omitempty"`
	Timeout           int               `json:"timeout,omitempty"`
	Retry             int               `json:"retry,omitempty"`
	RunUser           string            `json:"run_user,omitempty"`
	OfflinePolicy     string            `json:"offline_policy,omitempty"`
}

// DeployResultPayload 部署结果
type DeployResultPayload struct {
	AppID            string `json:"app_id"`
	ExecutionID      string `json:"execution_id,omitempty"`
	TaskID           string `json:"task_id,omitempty"`
	TaskRevision     int64  `json:"task_revision,omitempty"`
	TriggerType      string `json:"trigger_type,omitempty"`
	ScheduledTime    string `json:"scheduled_time,omitempty"`
	ScheduleID       string `json:"schedule_id,omitempty"`
	ScheduleRevision int64  `json:"schedule_revision,omitempty"`
	CreatedAt        string `json:"created_at,omitempty"`
	Version          string `json:"version,omitempty"`
	StartTime        string `json:"start_time,omitempty"`
	Operation        string `json:"operation"`
	OK               bool   `json:"ok"`
	Health           string `json:"health,omitempty"` // healthy | unhealthy | stopped
	Rollback         bool   `json:"rollback,omitempty"`
	Error            string `json:"error,omitempty"`
	// Blocked 表示未执行且原因属于明确阻塞（如 inbound artifact 未暂存）。
	Blocked bool `json:"blocked,omitempty"`
}

// RemoteStateUnknown 远程状态不可解析标记
const RemoteStateUnknown = "UNKNOWN"

// RemoteStatePayload 远程状态。
// TTLRemainingSec 为自 Hub 判定时刻起的剩余有效秒数（向上取整），是 Agent 新鲜度
// 判定的唯一依据：字段缺失或 <= 0 一律视为未知并 Fail Closed；判定不做跨主机时钟
// 比较，不承诺与不下发该字段的旧 Hub 兼容。
// ObservedAt 与 TTL 保留但仅作诊断/展示用途（TTL 在 Agent 上报缓存时仍由 Hub 用于
// 有效期计算），不参与 Agent 新鲜度判定。
type RemoteStatePayload struct {
	NodeID          string `json:"node_id"`
	Property        string `json:"property"`
	Value           string `json:"value"`
	ObservedAt      string `json:"observed_at"`                 // 仅诊断：观测时刻
	TTL             int64  `json:"ttl_sec"`                     // 观测窗口长度（秒）：上报用于 Hub 缓存有效期，下发仅诊断
	TTLRemainingSec *int64 `json:"ttl_remaining_sec,omitempty"` // 新鲜度判定唯一依据，缺失/<=0 视为未知
}

// RemoteStateReqPayload 远程状态查询
type RemoteStateReqPayload struct {
	TargetNodeID string `json:"target_node_id"`
	Property     string `json:"property"`
	TaskID       string `json:"task_id,omitempty"`
}

// ErrorPayload 错误
type ErrorPayload struct {
	Code    string `json:"code"`
	Message string `json:"message"`
}

// InventoryPayload Inventory 上报
type InventoryPayload struct {
	NodeID    string     `json:"node_id"`
	Inventory *Inventory `json:"inventory,omitempty"`
	Available bool       `json:"available"`
}

// Inventory 宿主机信息
type Inventory struct {
	Hostname     string                 `json:"hostname,omitempty"`
	OS           string                 `json:"os"`
	OSVersion    string                 `json:"os_version"`
	Kernel       string                 `json:"kernel"`
	Arch         string                 `json:"arch"`
	CPU          []InventoryCPU         `json:"cpu"`
	Memory       *InventoryMemory       `json:"memory,omitempty"`
	Filesystem   []InventoryFS          `json:"filesystem"`
	Network      []InventoryNet         `json:"network"`
	BlockDevices []InventoryBlockDevice `json:"block_devices,omitempty"`
}

type InventoryBlockDevice struct {
	Name string `json:"name"`
	Kind string `json:"kind"`
}

type InventoryCPU struct {
	Model string `json:"model"`
	Cores int    `json:"cores"`
	MHz   int64  `json:"mhz"`
}

type InventoryMemory struct {
	TotalKB     uint64 `json:"total_kb"`
	AvailableKB uint64 `json:"available_kb"`
}

type InventoryFS struct {
	Mount   string `json:"mount"`
	Device  string `json:"device"`
	FSType  string `json:"fs_type"`
	TotalKB uint64 `json:"total_kb"`
	FreeKB  uint64 `json:"free_kb"`
}

type InventoryNet struct {
	Interface string   `json:"interface"`
	Addresses []string `json:"addresses"`
	MAC       string   `json:"mac"`
}
