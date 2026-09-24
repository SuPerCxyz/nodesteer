package models

import "time"

// Revision 相关常量
const (
	ObjectScript      = "script"
	ObjectTask        = "task"
	ObjectSchedule    = "schedule"
	ObjectApplication = "application"
	ObjectGroup       = "group"
	ObjectNode        = "node"
)

// FileTransferStatus 文件传输状态
const (
	FileTransferPending    = "PENDING"
	FileTransferUploading  = "UPLOADING"
	FileTransferDelivering = "DELIVERING"
	FileTransferSuccess    = "SUCCESS"
	FileTransferFailed     = "FAILED"
	FileTransferCanceled   = "CANCELED"
)

// FileTransferTargetStatus 目标文件传输状态
const (
	FileTargetPending    = "PENDING"
	FileTargetDelivering = "DELIVERING"
	FileTargetSuccess    = "SUCCESS"
	FileTargetFailed     = "FAILED"
	FileTargetCanceled   = "CANCELED"
)

// DeploymentMode 节点部署模式
const (
	DeploymentModeNative        = "native"
	DeploymentModeDocker        = "docker"
	DeploymentModeDockerHostInt = "docker_host_integration"
)

// Node 状态
const (
	NodeStatusPending     = "pending"
	NodeStatusOnline      = "online"
	NodeStatusOffline     = "offline"
	NodeStatusMaintenance = "maintenance"
	NodeStatusDisabled    = "disabled"
)

// Capability 键
const (
	CapScript            = "script"
	CapLocalScheduler    = "local_scheduler"
	CapOfflineExecution  = "offline_execution"
	CapHostFilesystem    = "host_filesystem"
	CapManagedSystemd    = "managed_systemd"
	CapApplicationDeploy = "application_deploy"
	// CapAgentUpgrade Agent 自升级能力：仅 native 部署形态上报 true。
	// 仅作能力广而告知（UI/调用方可用）；升级下发的硬门禁是 deployment_mode
	// （存量旧 Agent 未上报该能力，若以其为门禁会形成"无法升级到能升级的版本"死锁）。
	CapAgentUpgrade = "agent_upgrade"
)

// Node 节点
type Node struct {
	ID              string            `json:"id"`
	AgentID         string            `json:"agent_id"`
	Hostname        string            `json:"hostname"`
	IP              string            `json:"ip"`
	OS              string            `json:"os"`
	Arch            string            `json:"arch"`
	AgentVersion    string            `json:"agent_version"`
	DeploymentMode  string            `json:"deployment_mode"`
	HostIntegration bool              `json:"host_integration"`
	Status          string            `json:"status"`
	Labels          map[string]string `json:"labels"`
	Capabilities    map[string]bool   `json:"capabilities"`
	GlobalRevision  int64             `json:"global_revision"`
	SyncStatus      string            `json:"sync_status"`
	LastSeen        time.Time         `json:"last_seen"`
	FirstSeen       time.Time         `json:"first_seen"`
	Inventory       *Inventory        `json:"inventory,omitempty"`
}

// Inventory 节点硬件信息
type Inventory struct {
	OS         string           `json:"os"`
	OSVersion  string           `json:"os_version"`
	Kernel     string           `json:"kernel"`
	Arch       string           `json:"arch"`
	CPU        []CPUInfo        `json:"cpu"`
	Memory     *MemoryInfo      `json:"memory,omitempty"`
	Filesystem []FilesystemInfo `json:"filesystem"`
	Network    []NetworkInfo    `json:"network"`
}

type CPUInfo struct {
	Model string `json:"model"`
	Cores int    `json:"cores"`
	MHz   int64  `json:"mhz"`
}

type MemoryInfo struct {
	TotalKB     uint64 `json:"total_kb"`
	AvailableKB uint64 `json:"available_kb"`
}

type FilesystemInfo struct {
	Mount   string `json:"mount"`
	Device  string `json:"device"`
	FSType  string `json:"fs_type"`
	TotalKB uint64 `json:"total_kb"`
	FreeKB  uint64 `json:"free_kb"`
}

type NetworkInfo struct {
	Interface string   `json:"interface"`
	Addresses []string `json:"addresses"`
	MAC       string   `json:"mac"`
}

// Group 节点组
type Group struct {
	ID          string    `json:"id"`
	Name        string    `json:"name"`
	Description string    `json:"description"`
	Type        string    `json:"type"` // static | label
	LabelKey    string    `json:"label_key,omitempty"`
	LabelValue  string    `json:"label_value,omitempty"`
	Members     []string  `json:"members"`
	CreatedAt   time.Time `json:"created_at"`
}

// User 用户
type User struct {
	ID           string    `json:"id"`
	Username     string    `json:"username"`
	PasswordHash string    `json:"-"`
	AvatarURL    string    `json:"avatar,omitempty"` // OIDC picture claim 同步的头像，空表示无
	Role         string    `json:"role"`             // administrator | operator | viewer
	CreatedAt    time.Time `json:"created_at"`
}

// Role 常量
const (
	RoleAdministrator = "administrator"
	RoleOperator      = "operator"
	RoleViewer        = "viewer"
)

// Script 脚本
type Script struct {
	ID          string            `json:"id"`
	Name        string            `json:"name"`
	Description string            `json:"description"`
	Interpreter string            `json:"interpreter"` // shell | bash | python
	Content     string            `json:"content"`
	Parameters  []Parameter       `json:"parameters"`
	Environment map[string]string `json:"environment"`
	WorkingDir  string            `json:"working_dir"`
	RunUser     string            `json:"run_user,omitempty"`
	Timeout     int               `json:"timeout"`
	Enabled     bool              `json:"enabled"`
	Revision    int64             `json:"revision"`
	SHA256      string            `json:"sha256"`
	UpdatedAt   time.Time         `json:"updated_at"`
	CreatedAt   time.Time         `json:"created_at"`
}

type Parameter struct {
	Name        string `json:"name"`
	Type        string `json:"type"` // string | number | bool | secret
	Required    bool   `json:"required"`
	Default     string `json:"default,omitempty"`
	Description string `json:"description,omitempty"`
}

// Task 任务
type Task struct {
	ID            string            `json:"id"`
	Name          string            `json:"name"`
	Type          string            `json:"type"` // command | script | app_deploy | app_operation
	Target        Target            `json:"target"`
	Parameters    []Parameter       `json:"parameters"`
	ParamValues   map[string]string `json:"param_values"`
	ScriptID      string            `json:"script_id,omitempty"`
	Command       string            `json:"command,omitempty"`
	ApplicationID string            `json:"application_id,omitempty"`
	AppOperation  string            `json:"app_operation,omitempty"` // start | stop | restart | upgrade
	Condition     *Condition        `json:"condition,omitempty"`
	Schedule      *Schedule         `json:"schedule,omitempty"`
	Timeout       int               `json:"timeout"`
	Retry         int               `json:"retry"`
	OfflinePolicy string            `json:"offline_policy"` // hub_online_required | allow_offline
	RunUser       string            `json:"run_user,omitempty"`
	Enabled       bool              `json:"enabled"`
	Revision      int64             `json:"revision"`
	UpdatedAt     time.Time         `json:"updated_at"`
	CreatedAt     time.Time         `json:"created_at"`
}

// 任务类型常量
const (
	TaskTypeCommand      = "command"
	TaskTypeScript       = "script"
	TaskTypeAppDeploy    = "app_deploy"
	TaskTypeAppOperation = "app_operation"
	// TaskTypeAgentUpgrade Agent 自升级执行类型：仅由 Hub 升级 API 下发，
	// 不是用户可创建的 Task 类型（definitions 校验对未知类型直接拒绝）。
	TaskTypeAgentUpgrade = "agent_upgrade"
)

// AgentUpgradeTaskID 自升级执行的系统 Task 标识（升级没有用户 Task 对象，
// 以该常量填充执行记录的 task_id NOT NULL 约束；Hub/Agent 执行表均无外键，
// 无需 schema 迁移）。执行历史以 task_id == AgentUpgradeTaskID 识别升级记录。
const AgentUpgradeTaskID = "agent_upgrade"

// Offline 策略
const (
	OfflinePolicyHubOnlineRequired = "hub_online_required"
	OfflinePolicyAllowOffline      = "allow_offline"
)

// Target 任务目标
type Target struct {
	Type       string   `json:"type"` // node | group | label
	NodeIDs    []string `json:"node_ids,omitempty"`
	GroupIDs   []string `json:"group_ids,omitempty"`
	LabelKey   string   `json:"label_key,omitempty"`
	LabelValue string   `json:"label_value,omitempty"`
}

// Schedule 调度
type Schedule struct {
	ID             string    `json:"id"`
	TaskID         string    `json:"task_id"`
	Revision       int64     `json:"revision"`
	Type           string    `json:"type"` // cron | interval | one_time | on_start
	Expression     string    `json:"expression"`
	IntervalSec    int64     `json:"interval_sec,omitempty"`
	RunAt          time.Time `json:"run_at,omitempty"`
	Timezone       string    `json:"timezone"`
	ExecutionOwner string    `json:"execution_owner"` // hub | agent
	OfflinePolicy  string    `json:"offline_policy"`
	MisfirePolicy  string    `json:"misfire_policy"` // skip | run_once
	Enabled        bool      `json:"enabled"`
	CreatedAt      time.Time `json:"created_at"`
	UpdatedAt      time.Time `json:"updated_at"`
}

// 调度类型与策略
const (
	ScheduleTypeCron     = "cron"
	ScheduleTypeInterval = "interval"
	ScheduleTypeOneTime  = "one_time"
	// ScheduleTypeOnStart 目标节点上的 agent 进程每次启动后触发一次
	// （重启再触发，仅网络重连不触发；无任何触发时间字段，由 Agent 启动路径触发）。
	ScheduleTypeOnStart = "on_start"

	ExecutionOwnerHub   = "hub"
	ExecutionOwnerAgent = "agent"

	MisfirePolicySkip    = "skip"
	MisfirePolicyRunOnce = "run_once"
)

// Condition 条件
type Condition struct {
	Type   string           `json:"type"` // and | remote
	Local  *LocalCondition  `json:"local,omitempty"`
	And    []Condition      `json:"and,omitempty"`
	Remote *RemoteCondition `json:"remote,omitempty"`
}

// LocalCondition 本地条件
type LocalCondition struct {
	Metric   string `json:"metric"`   // cpu_usage | memory_usage | disk_usage | file_exists | dir_exists | process_exists | port_listening | command_result
	Operator string `json:"operator"` // == | != | > | < | >= | <=
	Value    string `json:"value"`
	Path     string `json:"path,omitempty"`
	Command  string `json:"command,omitempty"`
}

// RemoteCondition 远程条件
type RemoteCondition struct {
	NodeID   string `json:"node_id"`
	Property string `json:"property"` // online | last_execution
	TaskID   string `json:"task_id,omitempty"`
	Operator string `json:"operator"`
	Value    string `json:"value"`
}

// Execution 执行
type Execution struct {
	ID              string    `json:"id"`
	TaskID          string    `json:"task_id"`
	TaskRevision    int64     `json:"task_revision"`
	ScriptID        string    `json:"script_id,omitempty"`
	ScriptRevision  int64     `json:"script_revision,omitempty"`
	NodeID          string    `json:"node_id"`
	TriggerType     string    `json:"trigger_type"` // manual | schedule
	ScheduledTime   time.Time `json:"scheduled_time,omitempty"`
	StartTime       time.Time `json:"start_time,omitempty"`
	EndTime         time.Time `json:"end_time,omitempty"`
	Status          string    `json:"status"`
	ExitCode        int       `json:"exit_code,omitempty"`
	Stdout          string    `json:"stdout"`
	Stderr          string    `json:"stderr"`
	StdoutTruncated bool      `json:"stdout_truncated"`
	StderrTruncated bool      `json:"stderr_truncated"`
	Offline         bool      `json:"offline"`
	Synced          bool      `json:"synced"`
	BlockReason     string    `json:"block_reason,omitempty"`
}

// 执行状态
const (
	ExecStatusPending  = "PENDING"
	ExecStatusRunning  = "RUNNING"
	ExecStatusSuccess  = "SUCCESS"
	ExecStatusFailed   = "FAILED"
	ExecStatusSkipped  = "SKIPPED"
	ExecStatusCanceled = "CANCELED"
	ExecStatusTimedOut = "TIMED_OUT"
	ExecStatusBlocked  = "BLOCKED"
)

// Trigger 类型
const (
	TriggerManual   = "manual"
	TriggerSchedule = "schedule"
)

// Artifact 制品
type Artifact struct {
	ID           string    `json:"id"`
	Name         string    `json:"name"`
	Version      string    `json:"version"`
	Architecture string    `json:"architecture"` // amd64 | arm64
	Filename     string    `json:"filename"`
	Size         int64     `json:"size"`
	SHA256       string    `json:"sha256"`
	StoragePath  string    `json:"-"`
	UploadedAt   time.Time `json:"uploaded_at"`
}

// FileTransfer Hub 中继文件传输任务。
type FileTransfer struct {
	ID           string                `json:"id"`
	SourceNodeID string                `json:"source_node_id"`
	SourcePath   string                `json:"source_path"`
	SourceMode   uint32                `json:"source_mode,omitempty"`
	Size         int64                 `json:"size"`
	SHA256       string                `json:"sha256,omitempty"`
	Status       string                `json:"status"`
	Error        string                `json:"error,omitempty"`
	CreatedAt    time.Time             `json:"created_at"`
	UpdatedAt    time.Time             `json:"updated_at"`
	Targets      []*FileTransferTarget `json:"targets,omitempty"`
}

// FileTransferTarget 单个目标 Agent 的交付状态。
type FileTransferTarget struct {
	TransferID      string    `json:"transfer_id"`
	NodeID          string    `json:"node_id"`
	DestinationPath string    `json:"destination_path"`
	Mode            uint32    `json:"mode,omitempty"`
	Status          string    `json:"status"`
	Error           string    `json:"error,omitempty"`
	UpdatedAt       time.Time `json:"updated_at"`
}

// Application 托管应用
type Application struct {
	ID          string            `json:"id"`
	Name        string            `json:"name"`
	Description string            `json:"description"`
	ArtifactID  string            `json:"artifact_id"`
	Version     string            `json:"version"`
	BinaryPath  string            `json:"binary_path"`
	Arguments   []string          `json:"arguments"`
	Environment map[string]string `json:"environment"`
	Config      string            `json:"config"`
	ConfigPath  string            `json:"config_path,omitempty"`
	UnitName    string            `json:"unit_name"`
	HealthCheck *HealthCheck      `json:"health_check"`
	// ArtifactURL / ArtifactSHA256 为同步时由 Hub 填充的派生字段（Agent-owned 调度部署时下载用）
	ArtifactURL    string    `json:"artifact_url,omitempty"`
	ArtifactSHA256 string    `json:"artifact_sha256,omitempty"`
	Revision       int64     `json:"revision"`
	UpdatedAt      time.Time `json:"updated_at"`
	CreatedAt      time.Time `json:"created_at"`
}

// ApplicationNodeState 应用在节点上的最近状态
type ApplicationNodeState struct {
	ApplicationID string    `json:"application_id"`
	NodeID        string    `json:"node_id"`
	Version       string    `json:"version,omitempty"`
	Operation     string    `json:"operation"`
	Health        string    `json:"health"`
	Error         string    `json:"error,omitempty"`
	UpdatedAt     time.Time `json:"updated_at"`
}

// HealthCheck 健康检查
type HealthCheck struct {
	Type     string `json:"type"` // systemd | tcp | http | command
	Target   string `json:"target"`
	Timeout  int    `json:"timeout"`
	Attempts int    `json:"attempts"`
	Interval int    `json:"interval"`
}

// 健康检查类型
const (
	HealthTypeSystemd = "systemd"
	HealthTypeTCP     = "tcp"
	HealthTypeHTTP    = "http"
	HealthTypeCommand = "command"
)

// AuditLog 审计日志
type AuditLog struct {
	ID         string    `json:"id"`
	UserID     string    `json:"user_id"`
	Username   string    `json:"username"`
	Action     string    `json:"action"`
	Resource   string    `json:"resource"`
	ResourceID string    `json:"resource_id"`
	Detail     string    `json:"detail,omitempty"`
	CreatedAt  time.Time `json:"created_at"`
}

// Settings 设置
type Settings struct {
	Key   string `json:"key"`
	Value string `json:"value"`
}

// AgentSyncState Agent 同步状态
type AgentSyncState struct {
	NodeID         string    `json:"node_id"`
	GlobalRevision int64     `json:"global_revision"`
	LastSync       time.Time `json:"last_sync"`
	SyncStatus     string    `json:"sync_status"`
}

// ScriptRevisionEntry 脚本历史版本
type ScriptRevisionEntry struct {
	Revision int64  `json:"revision"`
	Content  string `json:"content"`
	SHA256   string `json:"sha256"`
}
