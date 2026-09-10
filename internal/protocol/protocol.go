package protocol

import (
	"encoding/json"
	"time"
)

// ProtocolVersion 当前协议版本
const ProtocolVersion = 1

// 消息类型
const (
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
)

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
}

// HeartbeatPayload 心跳
type HeartbeatPayload struct {
	NodeID       string `json:"node_id"`
	GlobalRev    int64  `json:"global_rev"`
	RunningExecs int    `json:"running_execs"`
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
	TriggerType        string            `json:"trigger_type"`
	Condition          json.RawMessage   `json:"condition,omitempty"`
	ArtifactID         string            `json:"artifact_id,omitempty"`
	TargetVersion      string            `json:"target_version,omitempty"`
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
	Status               string `json:"status"`
	ExitCode             int    `json:"exit_code"`
	Stdout               string `json:"stdout"`
	Stderr               string `json:"stderr"`
	StdoutTruncated      bool   `json:"stdout_truncated"`
	StderrTruncated      bool   `json:"stderr_truncated"`
	StartTime            string `json:"start_time,omitempty"`
	EndTime              string `json:"end_time"`
	Offline              bool   `json:"offline"`
	BlockReason          string `json:"block_reason,omitempty"`
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
}

// FileUploadRequestPayload Hub 要求源 Agent 上传文件。
type FileUploadRequestPayload struct {
	TransferID string `json:"transfer_id"`
	SourcePath string `json:"source_path"`
	UploadURL  string `json:"upload_url"`
	Offset     int64  `json:"offset"`
	MaxBytes   int64  `json:"max_bytes"`
}

// FileUploadResultPayload 源 Agent 上传结果。
type FileUploadResultPayload struct {
	TransferID string `json:"transfer_id"`
	OK         bool   `json:"ok"`
	Error      string `json:"error,omitempty"`
}

// FileDeliveryRequestPayload Hub 要求目标 Agent 下载并写入文件。
type FileDeliveryRequestPayload struct {
	TransferID      string `json:"transfer_id"`
	DownloadURL     string `json:"download_url"`
	SHA256          string `json:"sha256"`
	Size            int64  `json:"size"`
	DestinationPath string `json:"destination_path"`
	Mode            uint32 `json:"mode,omitempty"`
}

// FileDeliveryResultPayload 目标 Agent 交付结果。
type FileDeliveryResultPayload struct {
	TransferID string `json:"transfer_id"`
	OK         bool   `json:"ok"`
	Error      string `json:"error,omitempty"`
}

// FileTransferCancelPayload 取消文件传输。
type FileTransferCancelPayload struct {
	TransferID string `json:"transfer_id"`
}

// DeployRequestPayload 部署请求
type DeployRequestPayload struct {
	AppID          string            `json:"app_id"`
	AppVersion     string            `json:"app_version"`
	AppRevision    int64             `json:"app_revision"`
	ArtifactID     string            `json:"artifact_id"`
	ArtifactURL    string            `json:"artifact_url"`
	ArtifactSHA256 string            `json:"artifact_sha256"`
	BinaryPath     string            `json:"binary_path"`
	Arguments      []string          `json:"arguments"`
	Environment    map[string]string `json:"environment"`
	Config         string            `json:"config"`
	ConfigPath     string            `json:"config_path,omitempty"`
	UnitName       string            `json:"unit_name"`
	HealthCheck    json.RawMessage   `json:"health_check,omitempty"`
	Operation      string            `json:"operation"` // deploy | start | stop | restart | upgrade
}

// DeployResultPayload 部署结果
type DeployResultPayload struct {
	AppID         string `json:"app_id"`
	ExecutionID   string `json:"execution_id,omitempty"`
	TaskID        string `json:"task_id,omitempty"`
	TaskRevision  int64  `json:"task_revision,omitempty"`
	TriggerType   string `json:"trigger_type,omitempty"`
	ScheduledTime string `json:"scheduled_time,omitempty"`
	Version       string `json:"version,omitempty"`
	StartTime     string `json:"start_time,omitempty"`
	Operation     string `json:"operation"`
	OK            bool   `json:"ok"`
	Health        string `json:"health,omitempty"` // healthy | unhealthy | stopped
	Rollback      bool   `json:"rollback,omitempty"`
	Error         string `json:"error,omitempty"`
}

// RemoteStateUnknown 远程状态不可解析标记
const RemoteStateUnknown = "UNKNOWN"

// RemoteStatePayload 远程状态
type RemoteStatePayload struct {
	NodeID     string `json:"node_id"`
	Property   string `json:"property"`
	Value      string `json:"value"`
	ObservedAt string `json:"observed_at"`
	TTL        int64  `json:"ttl_sec"`
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
	OS         string           `json:"os"`
	OSVersion  string           `json:"os_version"`
	Kernel     string           `json:"kernel"`
	Arch       string           `json:"arch"`
	CPU        []InventoryCPU   `json:"cpu"`
	Memory     *InventoryMemory `json:"memory,omitempty"`
	Filesystem []InventoryFS    `json:"filesystem"`
	Network    []InventoryNet   `json:"network"`
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
