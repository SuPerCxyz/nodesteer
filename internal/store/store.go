package store

import (
	"context"
	"errors"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

// ErrInvalidTask 无效任务
func ErrInvalidTask(msg string) error {
	return errors.New("invalid task: " + msg)
}

// Store Hub 数据层接口
type Store interface {
	// Users
	CreateUser(ctx context.Context, u *models.User) error
	GetUserByID(ctx context.Context, id string) (*models.User, error)
	GetUserByUsername(ctx context.Context, username string) (*models.User, error)
	ListUsers(ctx context.Context) ([]*models.User, error)
	UpdateUserRole(ctx context.Context, id, role string) error
	UpdateUserPassword(ctx context.Context, id, passwordHash string) error
	DeleteSetting(ctx context.Context, key string) error

	// Nodes
	UpsertNode(ctx context.Context, n *models.Node) error
	GetNode(ctx context.Context, id string) (*models.Node, error)
	GetNodeByAgentID(ctx context.Context, agentID string) (*models.Node, error)
	ListNodes(ctx context.Context) ([]*models.Node, error)
	UpdateNodeStatus(ctx context.Context, id, status string) error
	UpdateNodeSyncState(ctx context.Context, id string, rev int64, syncStatus string) error
	UpdateNodeHeartbeat(ctx context.Context, id string, lastSeen time.Time) error
	SetNodeLabels(ctx context.Context, id string, labels map[string]string) error
	DeleteNode(ctx context.Context, id string) error

	// Groups
	CreateGroup(ctx context.Context, g *models.Group) error
	GetGroup(ctx context.Context, id string) (*models.Group, error)
	ListGroups(ctx context.Context) ([]*models.Group, error)
	UpdateGroup(ctx context.Context, g *models.Group) error
	DeleteGroup(ctx context.Context, id string) error
	AddGroupMember(ctx context.Context, groupID, nodeID string) error
	RemoveGroupMember(ctx context.Context, groupID, nodeID string) error
	GroupMemberIDs(ctx context.Context, groupID string) ([]string, error)

	// Scripts
	CreateScript(ctx context.Context, s *models.Script) error
	UpdateScript(ctx context.Context, s *models.Script, prevRevision int64) error
	GetScript(ctx context.Context, id string) (*models.Script, error)
	ListScripts(ctx context.Context) ([]*models.Script, error)
	DeleteScript(ctx context.Context, id string) error
	GetScriptRevision(ctx context.Context, id string, revision int64) (*ScriptRevisionEntry, error)
	ListScriptRevisions(ctx context.Context, id string) ([]*ScriptRevisionEntry, error)
	ListApplicationRevisions(ctx context.Context, id string) ([]*ApplicationRevisionEntry, error)

	// Tasks
	CreateTask(ctx context.Context, t *models.Task) error
	UpdateTask(ctx context.Context, t *models.Task, prevRevision int64) error
	GetTask(ctx context.Context, id string) (*models.Task, error)
	ListTasks(ctx context.Context) ([]*models.Task, error)
	DeleteTask(ctx context.Context, id string) error

	// Schedules
	CreateSchedule(ctx context.Context, s *models.Schedule) error
	UpdateSchedule(ctx context.Context, s *models.Schedule, prevRevision int64) error
	GetSchedule(ctx context.Context, id string) (*models.Schedule, error)
	ListSchedules(ctx context.Context) ([]*models.Schedule, error)
	DeleteSchedule(ctx context.Context, id string) error

	// Artifacts
	CreateArtifact(ctx context.Context, a *models.Artifact) error
	GetArtifact(ctx context.Context, id string) (*models.Artifact, error)
	ListArtifacts(ctx context.Context) ([]*models.Artifact, error)
	DeleteArtifact(ctx context.Context, id string) error

	// File Transfers
	CreateFileTransfer(ctx context.Context, t *models.FileTransfer) error
	GetFileTransfer(ctx context.Context, id string) (*models.FileTransfer, error)
	ListFileTransfers(ctx context.Context) ([]*models.FileTransfer, error)
	UpdateFileTransfer(ctx context.Context, t *models.FileTransfer) error
	UpdateFileTransferTarget(ctx context.Context, t *models.FileTransferTarget) error

	// Applications
	CreateApplication(ctx context.Context, a *models.Application) error
	UpdateApplication(ctx context.Context, a *models.Application, prevRevision int64) error
	GetApplication(ctx context.Context, id string) (*models.Application, error)
	ListApplications(ctx context.Context) ([]*models.Application, error)
	DeleteApplication(ctx context.Context, id string) error
	SetApplicationAssignment(ctx context.Context, appID, nodeID string, assigned bool) error
	GetApplicationNodes(ctx context.Context, appID string) ([]string, error)
	SetApplicationNodeState(ctx context.Context, state *models.ApplicationNodeState) error
	DeleteApplicationNodeState(ctx context.Context, appID, nodeID string) error
	ListApplicationNodeStates(ctx context.Context, appID string) ([]*models.ApplicationNodeState, error)

	// Executions
	CreateExecution(ctx context.Context, e *models.Execution) error
	UpdateExecution(ctx context.Context, e *models.Execution) error
	GetExecution(ctx context.Context, id string) (*models.Execution, error)
	ListExecutions(ctx context.Context, filter ExecutionFilter) ([]*models.Execution, error)
	CountExecutions(ctx context.Context, filter ExecutionFilter) (int64, error)
	FindExecutionBySlot(ctx context.Context, taskID, nodeID string, scheduledTime string) (*models.Execution, error)
	CountExecutionsByStatus(ctx context.Context) (map[string]int, error)

	// Remote State
	SetRemoteState(ctx context.Context, nodeID, property, value string, observedAt time.Time, ttl int64) error
	GetRemoteState(ctx context.Context, nodeID, property string) (value string, observedAt time.Time, expiresAt time.Time, err error)

	// Sync / Revision
	NextGlobalRevision(ctx context.Context) (int64, error)
	CurrentGlobalRevision(ctx context.Context) (int64, error)
	AppendChangeLog(ctx context.Context, globalRev int64, objectType, objectID string, objectRev int64, operation string) error
	GetChangesSince(ctx context.Context, since int64) ([]*ChangeLogEntry, error)
	PruneChangeLog(ctx context.Context, keepFrom int64) error
	GetAgentSyncState(ctx context.Context, nodeID string) (*AgentSyncState, error)

	// Tombstones
	RecordTombstone(ctx context.Context, objectType, objectID string, globalRev int64) error
	ListTombstones(ctx context.Context) ([]Tombstone, error)
	DeleteTombstone(ctx context.Context, objectType, objectID string) error

	// Log Chunks
	AppendLogChunk(ctx context.Context, executionID, stream string, seq int64, chunk string) error
	ListLogChunks(ctx context.Context, executionID string) ([]LogChunk, error)

	// Audit
	AddAudit(ctx context.Context, a *models.AuditLog) error
	ListAudit(ctx context.Context, filter AuditFilter) ([]*models.AuditLog, error)
	CountAudit(ctx context.Context, filter AuditFilter) (int64, error)

	// Settings
	GetSetting(ctx context.Context, key string) (string, error)
	SetSetting(ctx context.Context, key, value string) error

	Close() error
}

// ExecutionFilter 执行过滤
type ExecutionFilter struct {
	NodeID string
	TaskID string
	Status string
	Limit  int
	Offset int
}

// AuditFilter 审计过滤
type AuditFilter struct {
	UserID string
	Action string
	Limit  int
	Offset int
}

// AgentSyncState Agent 同步状态
type AgentSyncState struct {
	NodeID         string
	GlobalRevision int64
	LastSync       time.Time
	SyncStatus     string
}

// ChangeLogEntry Change Log 条目
type ChangeLogEntry struct {
	GlobalRevision int64
	ObjectType     string
	ObjectID       string
	ObjectRevision int64
	Operation      string
	CreatedAt      time.Time
}

// Tombstone 删除记录
type Tombstone struct {
	ObjectType     string
	ObjectID       string
	GlobalRevision int64
	DeletedAt      time.Time
}

// LogChunk 执行日志分片
type LogChunk struct {
	ExecutionID string `json:"execution_id"`
	Stream      string `json:"stream"`
	Seq         int64  `json:"seq"`
	Chunk       string `json:"chunk"`
}

// ScriptRevisionEntry 脚本历史版本
type ScriptRevisionEntry struct {
	Revision  int64     `json:"revision"`
	Content   string    `json:"content"`
	SHA256    string    `json:"sha256"`
	ChangedAt time.Time `json:"changed_at"`
}

// ApplicationRevisionEntry 应用历史版本
type ApplicationRevisionEntry struct {
	Revision  int64     `json:"revision"`
	Content   string    `json:"content"`
	ChangedAt time.Time `json:"changed_at"`
}
