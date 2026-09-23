package hub

import (
	"context"
	"encoding/json"
	"fmt"
	"time"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/protocol"
	"github.com/cadentra/cadentra/internal/store"
	"github.com/google/uuid"
)

// AppManager Hub 应用管理
type AppManager struct {
	store     store.Store
	revisions *RevisionManager
	syncMgr   *SyncManager
	sessions  *SessionManager
	artifacts *ArtifactManager
	execMgr   *ExecutionManager
	baseURL   string
}

// NewAppManager 创建应用管理器
func NewAppManager(st store.Store, rm *RevisionManager, sm *SessionManager, am *ArtifactManager, em *ExecutionManager, baseURL string) *AppManager {
	return &AppManager{
		store:     st,
		revisions: rm,
		sessions:  sm,
		artifacts: am,
		execMgr:   em,
		baseURL:   baseURL,
	}
}

// SetSyncMgr 注入同步管理器（避免循环依赖）
func (m *AppManager) SetSyncMgr(sm *SyncManager) {
	m.syncMgr = sm
}

// Create 创建应用定义
func (m *AppManager) Create(ctx context.Context, a *models.Application) error {
	if a.ID == "" {
		a.ID = uuid.NewString()
	}
	if a.UnitName == "" {
		a.UnitName = "cadentra-" + a.Name + ".service"
	}
	if err := validateApplicationDefinition(a); err != nil {
		return err
	}
	a.Revision = 1
	var globalRev int64
	if err := runMutationTx(ctx, m.store, func(txctx context.Context) error {
		if err := m.store.CreateApplication(txctx, a); err != nil {
			return err
		}
		var err error
		globalRev, err = m.recordRevision(txctx, models.ObjectApplication, a.ID, a.Revision, "create")
		return err
	}); err != nil {
		return err
	}
	m.syncMgr.NotifyChange(ctx, models.ObjectApplication, a.ID, a.Revision, globalRev)
	return nil
}

// Update 更新应用定义
func (m *AppManager) Update(ctx context.Context, a *models.Application) error {
	existing, err := m.store.GetApplication(ctx, a.ID)
	if err != nil {
		return err
	}
	if a.UnitName == "" {
		a.UnitName = existing.UnitName
	}
	if err := validateApplicationDefinition(a); err != nil {
		return err
	}
	a.Revision = existing.Revision + 1
	a.CreatedAt = existing.CreatedAt
	var globalRev int64
	if err := runMutationTx(ctx, m.store, func(txctx context.Context) error {
		if err := m.store.UpdateApplication(txctx, a, existing.Revision); err != nil {
			return err
		}
		var err error
		globalRev, err = m.recordRevision(txctx, models.ObjectApplication, a.ID, a.Revision, "update")
		return err
	}); err != nil {
		return err
	}
	m.syncMgr.NotifyChange(ctx, models.ObjectApplication, a.ID, a.Revision, globalRev)
	return nil
}

// Delete 删除应用
func (m *AppManager) Delete(ctx context.Context, id string) error {
	appName := ""
	if app, err := m.store.GetApplication(ctx, id); err == nil {
		appName = app.Name
	}
	tasks, err := m.store.ListTasks(ctx)
	if err != nil {
		return err
	}
	for _, task := range tasks {
		if task.ApplicationID == id {
			return fmt.Errorf("application %s is referenced by task %s; delete or update the task first", id, task.ID)
		}
	}
	var rev int64
	if err := runMutationTx(ctx, m.store, func(txctx context.Context) error {
		if err := m.store.DeleteApplication(txctx, id); err != nil {
			return err
		}
		var err error
		rev, err = m.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := m.revisions.RecordChange(txctx, rev, models.ObjectApplication, id, 0, "delete"); err != nil {
			return err
		}
		if err := m.store.RecordTombstone(txctx, models.ObjectApplication, id, rev); err != nil {
			return err
		}
		return recordMutationAuditDetail(txctx, m.store, "application", id, "delete", appName)
	}); err != nil {
		return err
	}
	m.syncMgr.NotifyChange(ctx, models.ObjectApplication, id, 0, rev)
	return nil
}

// recordRevision 记录修订并通知
func (m *AppManager) recordRevision(ctx context.Context, objType, id string, rev int64, op string) (int64, error) {
	globalRev, err := m.revisions.Next(ctx)
	if err != nil {
		return 0, err
	}
	if err := m.revisions.RecordChange(ctx, globalRev, objType, id, rev, op); err != nil {
		return 0, err
	}
	if err := recordMutationAudit(ctx, m.store, "application", id, op); err != nil {
		return 0, err
	}
	return globalRev, nil
}

// Assign 分配节点
func (m *AppManager) Assign(ctx context.Context, appID string, nodeIDs []string, assigned bool) error {
	// 分配属于 Desired State 变更，推进 Global Revision
	app, err := m.store.GetApplication(ctx, appID)
	if err != nil {
		return err
	}
	for _, nodeID := range nodeIDs {
		if _, err := m.store.GetNode(ctx, nodeID); err != nil {
			return fmt.Errorf("node %s not found", nodeID)
		}
	}
	var rev int64
	if err := runMutationTx(ctx, m.store, func(txctx context.Context) error {
		for _, nid := range nodeIDs {
			if err := m.store.SetApplicationAssignment(txctx, appID, nid, assigned); err != nil {
				return err
			}
			if !assigned {
				if err := m.store.DeleteApplicationNodeState(txctx, appID, nid); err != nil {
					return err
				}
			}
		}
		var err error
		rev, err = m.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := m.revisions.RecordChange(txctx, rev, models.ObjectApplication, appID, app.Revision, "assign"); err != nil {
			return err
		}
		return recordMutationAudit(txctx, m.store, "application", appID, "assign")
	}); err != nil {
		return err
	}
	m.syncMgr.NotifyChange(ctx, models.ObjectApplication, appID, app.Revision, rev)
	return nil
}

// Get 获取应用
func (m *AppManager) Get(ctx context.Context, id string) (*models.Application, error) {
	return m.store.GetApplication(ctx, id)
}

// List 应用列表
func (m *AppManager) List(ctx context.Context) ([]*models.Application, error) {
	return m.store.ListApplications(ctx)
}

// GetNodes 应用分配的节点
func (m *AppManager) GetNodes(ctx context.Context, appID string) ([]string, error) {
	return m.store.GetApplicationNodes(ctx, appID)
}

// Deploy 触发部署（应用 Task 执行）
func (m *AppManager) Deploy(ctx context.Context, appID string, nodeIDs []string, operation string) ([]*models.Execution, error) {
	if !validApplicationOperation(operation) && operation != "deploy" {
		return nil, fmt.Errorf("invalid application operation: %s", operation)
	}
	app, err := m.store.GetApplication(ctx, appID)
	if err != nil {
		return nil, err
	}
	// 校验 Artifact
	if operation == "deploy" || operation == "upgrade" {
		if app.ArtifactID != "" {
			if _, err := m.store.GetArtifact(ctx, app.ArtifactID); err != nil {
				return nil, fmt.Errorf("artifact %s not found", app.ArtifactID)
			}
		}
	}
	// 先 Prefetch
	if (operation == "deploy" || operation == "upgrade") && app.ArtifactID != "" {
		m.artifacts.PrefetchToNodes(ctx, nodeIDs, app.ArtifactID, m.baseURL)
	}

	var out []*models.Execution
	for _, nodeID := range nodeIDs {
		// 节点状态与 Capability 校验（仿 ExecutionManager）
		node, err := m.store.GetNode(ctx, nodeID)
		if err != nil {
			return out, err
		}
		if node.Status != models.NodeStatusOnline {
			return out, fmt.Errorf("node %s is not online (status=%s)", nodeID, node.Status)
		}
		if !node.Capabilities[models.CapApplicationDeploy] {
			return out, fmt.Errorf("node %s lacks capability %s", nodeID, models.CapApplicationDeploy)
		}
		ex := &models.Execution{
			ID:           uuid.NewString(),
			TaskID:       appID,
			TaskRevision: app.Revision,
			NodeID:       nodeID,
			TriggerType:  models.TriggerManual,
			Status:       models.ExecStatusPending,
		}
		if err := m.store.CreateExecution(ctx, ex); err != nil {
			return out, err
		}
		if err := m.dispatchDeploy(ctx, ex, app, nodeID, operation); err != nil {
			ex.Status = models.ExecStatusFailed
			ex.EndTime = time.Now()
			ex.BlockReason = err.Error()
			m.store.UpdateExecution(ctx, ex)
			return out, err
		}
		out = append(out, ex)
	}
	return out, nil
}

// DeployTask 通过任务执行链路触发应用操作，确保 Execution.TaskID 指向真实 Task。
func (m *AppManager) DeployTask(ctx context.Context, task *models.Task, nodeIDs []string, operation string) ([]*models.Execution, error) {
	if task == nil || task.ApplicationID == "" {
		return nil, fmt.Errorf("application task requires application_id")
	}
	if !validApplicationOperation(operation) && operation != "deploy" {
		return nil, fmt.Errorf("invalid application operation: %s", operation)
	}
	app, err := m.store.GetApplication(ctx, task.ApplicationID)
	if err != nil {
		return nil, err
	}
	if operation == "deploy" || operation == "upgrade" {
		if app.ArtifactID != "" {
			if _, err := m.store.GetArtifact(ctx, app.ArtifactID); err != nil {
				return nil, fmt.Errorf("artifact %s not found", app.ArtifactID)
			}
		}
	}
	var out []*models.Execution
	for _, nodeID := range nodeIDs {
		node, err := m.store.GetNode(ctx, nodeID)
		if err != nil {
			return out, err
		}
		if node.Status != models.NodeStatusOnline {
			return out, fmt.Errorf("node %s is not online (status=%s)", nodeID, node.Status)
		}
		if !node.Capabilities[models.CapApplicationDeploy] {
			return out, fmt.Errorf("node %s lacks capability %s", nodeID, models.CapApplicationDeploy)
		}
		ex := &models.Execution{ID: uuid.NewString(), TaskID: task.ID, TaskRevision: task.Revision,
			NodeID: nodeID, TriggerType: models.TriggerManual, Status: models.ExecStatusPending}
		if err := m.store.CreateExecution(ctx, ex); err != nil {
			return out, err
		}
		if err := m.dispatchDeploy(ctx, ex, app, nodeID, operation); err != nil {
			ex.Status = models.ExecStatusFailed
			ex.EndTime = time.Now()
			ex.BlockReason = err.Error()
			_ = m.store.UpdateExecution(ctx, ex)
			return out, err
		}
		out = append(out, ex)
	}
	return out, nil
}

// dispatchDeploy 下发部署请求
func (m *AppManager) dispatchDeploy(ctx context.Context, ex *models.Execution, app *models.Application, nodeID, operation string) error {
	conn, ok := m.sessions.Get(nodeID)
	if !ok {
		return ErrAgentOffline
	}
	var hc json.RawMessage
	if app.HealthCheck != nil {
		b, _ := json.Marshal(app.HealthCheck)
		hc = b
	}
	var artifactURL, sha string
	if app.ArtifactID != "" {
		art, err := m.store.GetArtifact(ctx, app.ArtifactID)
		if err == nil {
			artifactURL = fmt.Sprintf("%s/api/artifacts/%s/download", m.baseURL, art.ID)
			sha = art.SHA256
		}
	}
	payload := protocol.DeployRequestPayload{
		AppID:          app.ID,
		AppVersion:     app.Version,
		AppRevision:    app.Revision,
		ArtifactID:     app.ArtifactID,
		ArtifactURL:    artifactURL,
		ArtifactSHA256: sha,
		BinaryPath:     app.BinaryPath,
		Arguments:      app.Arguments,
		Environment:    app.Environment,
		Config:         app.Config,
		ConfigPath:     app.ConfigPath,
		UnitName:       app.UnitName,
		HealthCheck:    hc,
		Operation:      operation,
	}
	if err := conn.Send(protocol.NewEnvelope(protocol.MsgDeployRequest, ex.ID, payload)); err != nil {
		return err
	}
	return nil
}

// OperationTask 应用操作 Task 触发（经 Task 执行管理器）
func (m *AppManager) OperationTask(ctx context.Context, task *models.Task, nodeIDs []string) ([]*models.Execution, error) {
	op := task.AppOperation
	if op == "" {
		op = "start"
	}
	return m.DeployTask(ctx, task, nodeIDs, op)
}
