package hub

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"strings"

	"github.com/cadentra/cadentra/internal/models"
	"github.com/cadentra/cadentra/internal/store"
	"github.com/google/uuid"
)

// ScriptManager Hub 脚本管理
type ScriptManager struct {
	store     store.Store
	revisions *RevisionManager
	syncMgr   *SyncManager
}

// NewScriptManager 创建脚本管理器
func NewScriptManager(st store.Store, rm *RevisionManager, sm *SyncManager) *ScriptManager {
	return &ScriptManager{store: st, revisions: rm, syncMgr: sm}
}

// computeSHA 计算内容哈希
func computeSHA(content string) string {
	h := sha256.Sum256([]byte(content))
	return hex.EncodeToString(h[:])
}

// Create 创建脚本
func (m *ScriptManager) Create(ctx context.Context, s *models.Script) error {
	if s.ID == "" {
		s.ID = uuid.NewString()
	}
	if err := validateParameters(s.Parameters); err != nil {
		return err
	}
	scripts, err := m.store.ListScripts(ctx)
	if err != nil {
		return err
	}
	if err := validateUniqueName(scripts, s.Name, ""); err != nil {
		return err
	}
	s.Revision = 1
	s.SHA256 = computeSHA(s.Content)
	var rev int64
	if err := runMutationTx(ctx, m.store, func(txctx context.Context) error {
		if err := m.store.CreateScript(txctx, s); err != nil {
			return err
		}
		var err error
		rev, err = m.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := m.revisions.RecordChange(txctx, rev, models.ObjectScript, s.ID, s.Revision, "create"); err != nil {
			return err
		}
		return recordMutationAudit(txctx, m.store, "script", s.ID, "create")
	}); err != nil {
		return err
	}
	m.syncMgr.NotifyChange(ctx, models.ObjectScript, s.ID, s.Revision, rev)
	return nil
}

// Update 更新脚本
func (m *ScriptManager) Update(ctx context.Context, s *models.Script) error {
	if err := validateParameters(s.Parameters); err != nil {
		return err
	}
	scripts, err := m.store.ListScripts(ctx)
	if err != nil {
		return err
	}
	if err := validateUniqueName(scripts, s.Name, s.ID); err != nil {
		return err
	}
	existing, err := m.store.GetScript(ctx, s.ID)
	if err != nil {
		return err
	}
	s.Revision = existing.Revision + 1
	s.CreatedAt = existing.CreatedAt
	s.SHA256 = computeSHA(s.Content)
	var rev int64
	if err := runMutationTx(ctx, m.store, func(txctx context.Context) error {
		if err := m.store.UpdateScript(txctx, s, existing.Revision); err != nil {
			return err
		}
		var err error
		rev, err = m.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := m.revisions.RecordChange(txctx, rev, models.ObjectScript, s.ID, s.Revision, "update"); err != nil {
			return err
		}
		return recordMutationAudit(txctx, m.store, "script", s.ID, "update")
	}); err != nil {
		return err
	}
	m.syncMgr.NotifyChange(ctx, models.ObjectScript, s.ID, s.Revision, rev)
	return nil
}

// Delete 删除脚本
func (m *ScriptManager) Delete(ctx context.Context, id string) error {
	name := ""
	if sc, err := m.store.GetScript(ctx, id); err == nil {
		name = sc.Name
	}
	tasks, err := m.store.ListTasks(ctx)
	if err != nil {
		return err
	}
	for _, task := range tasks {
		if task.ScriptID == id {
			return fmt.Errorf("script %s is referenced by task %s", id, task.ID)
		}
	}
	var rev int64
	if err := runMutationTx(ctx, m.store, func(txctx context.Context) error {
		if err := m.store.DeleteScript(txctx, id); err != nil {
			return err
		}
		var err error
		rev, err = m.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := m.revisions.RecordChange(txctx, rev, models.ObjectScript, id, 0, "delete"); err != nil {
			return err
		}
		if err := m.store.RecordTombstone(txctx, models.ObjectScript, id, rev); err != nil {
			return err
		}
		return recordMutationAuditDetail(txctx, m.store, "script", id, "delete", name)
	}); err != nil {
		return err
	}
	m.syncMgr.NotifyChange(ctx, models.ObjectScript, id, 0, rev)
	return nil
}

// Get 获取
func (m *ScriptManager) Get(ctx context.Context, id string) (*models.Script, error) {
	return m.store.GetScript(ctx, id)
}

// List 列表
func (m *ScriptManager) List(ctx context.Context) ([]*models.Script, error) {
	return m.store.ListScripts(ctx)
}

// GetRevision 历史版本
func (m *ScriptManager) GetRevision(ctx context.Context, id string, revision int64) (*store.ScriptRevisionEntry, error) {
	return m.store.GetScriptRevision(ctx, id, revision)
}

// ListRevisions 列出全部历史版本
func (m *ScriptManager) ListRevisions(ctx context.Context, id string) ([]*store.ScriptRevisionEntry, error) {
	return m.store.ListScriptRevisions(ctx, id)
}

// TaskManager Hub 任务管理
type TaskManager struct {
	store     store.Store
	revisions *RevisionManager
	syncMgr   *SyncManager
}

// NewTaskManager 创建任务管理器
func NewTaskManager(st store.Store, rm *RevisionManager, sm *SyncManager) *TaskManager {
	return &TaskManager{store: st, revisions: rm, syncMgr: sm}
}

// Validate 校验任务定义
func (m *TaskManager) Validate(ctx context.Context, t *models.Task) error {
	if err := validateParameters(t.Parameters); err != nil {
		return err
	}
	tasks, err := m.store.ListTasks(ctx)
	if err != nil {
		return err
	}
	if err := validateUniqueTaskName(tasks, t.Name, t.ID); err != nil {
		return err
	}
	switch t.Type {
	case models.TaskTypeScript:
		if t.ScriptID == "" {
			return store.ErrInvalidTask("script task requires script_id")
		}
		sc, err := m.store.GetScript(ctx, t.ScriptID)
		if err != nil {
			return store.ErrInvalidTask("referenced script not found")
		}
		if !sc.Enabled {
			return store.ErrInvalidTask("referenced script is disabled")
		}
	case models.TaskTypeAppDeploy, models.TaskTypeAppOperation:
		if t.ApplicationID == "" {
			return store.ErrInvalidTask("application task requires application_id")
		}
		if _, err := m.store.GetApplication(ctx, t.ApplicationID); err != nil {
			return store.ErrInvalidTask("referenced application not found")
		}
	case models.TaskTypeCommand:
		if t.Command == "" {
			return store.ErrInvalidTask("command task requires command")
		}
	case "":
		return store.ErrInvalidTask("type is required")
	default:
		return store.ErrInvalidTask("unknown task type")
	}
	if err := validateTarget(ctx, m.store, t.Target); err != nil {
		return err
	}
	if t.Timeout == 0 {
		t.Timeout = 300
	}
	if t.Timeout < 0 {
		return store.ErrInvalidTask("timeout cannot be negative")
	}
	if t.OfflinePolicy == "" {
		t.OfflinePolicy = models.OfflinePolicyHubOnlineRequired
	}
	if t.OfflinePolicy != models.OfflinePolicyHubOnlineRequired && t.OfflinePolicy != models.OfflinePolicyAllowOffline {
		return store.ErrInvalidTask("invalid offline_policy")
	}
	if t.Retry < 0 {
		return store.ErrInvalidTask("retry cannot be negative")
	}
	if t.Type == models.TaskTypeAppDeploy || t.Type == models.TaskTypeAppOperation {
		if t.AppOperation == "" {
			if t.Type == models.TaskTypeAppDeploy {
				t.AppOperation = "deploy"
			} else {
				t.AppOperation = "start"
			}
		}
		if t.Type == models.TaskTypeAppDeploy && t.AppOperation != "deploy" && t.AppOperation != "upgrade" {
			return store.ErrInvalidTask("app_deploy operation must be deploy or upgrade")
		}
		if t.Type == models.TaskTypeAppOperation && !validApplicationOperation(t.AppOperation) {
			return store.ErrInvalidTask("invalid application operation")
		}
	}
	// 条件类型归一化：远程/本地条件必须带正确 type，
	// 否则 Agent 评估会走 default 分支直接放行（Bug E）。
	if t.Condition != nil && t.Condition.Type == "" {
		if t.Condition.Remote != nil {
			t.Condition.Type = "remote"
		} else if t.Condition.Local != nil {
			t.Condition.Type = "local"
		}
	}
	if err := validateCondition(ctx, m.store, t.Condition); err != nil {
		return err
	}
	return nil
}

func validApplicationOperation(op string) bool {
	switch op {
	case "start", "stop", "restart", "upgrade":
		return true
	default:
		return false
	}
}

// Create 创建任务
func (m *TaskManager) Create(ctx context.Context, t *models.Task) error {
	if err := m.Validate(ctx, t); err != nil {
		return err
	}
	if t.ID == "" {
		t.ID = uuid.NewString()
	}
	t.Revision = 1
	var rev int64
	if err := runMutationTx(ctx, m.store, func(txctx context.Context) error {
		if err := m.store.CreateTask(txctx, t); err != nil {
			return err
		}
		var err error
		rev, err = m.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := m.revisions.RecordChange(txctx, rev, models.ObjectTask, t.ID, t.Revision, "create"); err != nil {
			return err
		}
		return recordMutationAudit(txctx, m.store, "task", t.ID, "create")
	}); err != nil {
		return err
	}
	m.syncMgr.NotifyChange(ctx, models.ObjectTask, t.ID, t.Revision, rev)
	return nil
}

// Update 更新任务
func (m *TaskManager) Update(ctx context.Context, t *models.Task) error {
	// 目标节点可能已被删除：更新时自动剔除失效节点，避免任务无法编辑/启停（缺陷 FAIL-A-009）
	if t.Target.Type == "node" && len(t.Target.NodeIDs) > 0 {
		alive := make([]string, 0, len(t.Target.NodeIDs))
		for _, id := range t.Target.NodeIDs {
			if _, err := m.store.GetNode(ctx, id); err == nil {
				alive = append(alive, id)
			}
		}
		t.Target.NodeIDs = alive
	}
	if err := m.Validate(ctx, t); err != nil {
		return err
	}
	existing, err := m.store.GetTask(ctx, t.ID)
	if err != nil {
		return err
	}
	t.Revision = existing.Revision + 1
	t.CreatedAt = existing.CreatedAt
	var rev int64
	if err := runMutationTx(ctx, m.store, func(txctx context.Context) error {
		if err := m.store.UpdateTask(txctx, t, existing.Revision); err != nil {
			return err
		}
		var err error
		rev, err = m.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := m.revisions.RecordChange(txctx, rev, models.ObjectTask, t.ID, t.Revision, "update"); err != nil {
			return err
		}
		return recordMutationAudit(txctx, m.store, "task", t.ID, "update")
	}); err != nil {
		return err
	}
	m.syncMgr.NotifyChange(ctx, models.ObjectTask, t.ID, t.Revision, rev)
	return nil
}

// Delete 删除任务
func (m *TaskManager) Delete(ctx context.Context, id string) error {
	name := ""
	if t, err := m.store.GetTask(ctx, id); err == nil {
		name = t.Name
	}
	schedules, err := m.store.ListSchedules(ctx)
	if err != nil {
		return err
	}
	for _, schedule := range schedules {
		if schedule.TaskID == id {
			return fmt.Errorf("task %s is referenced by schedule %s", id, schedule.ID)
		}
	}
	var rev int64
	if err := runMutationTx(ctx, m.store, func(txctx context.Context) error {
		if err := m.store.DeleteTask(txctx, id); err != nil {
			return err
		}
		var err error
		rev, err = m.revisions.Next(txctx)
		if err != nil {
			return err
		}
		if err := m.revisions.RecordChange(txctx, rev, models.ObjectTask, id, 0, "delete"); err != nil {
			return err
		}
		if err := m.store.RecordTombstone(txctx, models.ObjectTask, id, rev); err != nil {
			return err
		}
		return recordMutationAuditDetail(txctx, m.store, "task", id, "delete", name)
	}); err != nil {
		return err
	}
	m.syncMgr.NotifyChange(ctx, models.ObjectTask, id, 0, rev)
	return nil
}

// Get 获取
func (m *TaskManager) Get(ctx context.Context, id string) (*models.Task, error) {
	return m.store.GetTask(ctx, id)
}

// List 列表
func (m *TaskManager) List(ctx context.Context) ([]*models.Task, error) {
	return m.store.ListTasks(ctx)
}

// validateUniqueName 校验同类对象名称唯一（忽略大小写与首尾空白），selfID 为空时用于创建。
func validateUniqueName(items []*models.Script, name, selfID string) error {
	name = strings.TrimSpace(name)
	if name == "" {
		return fmt.Errorf("script name is required")
	}
	for _, other := range items {
		if other.ID != selfID && strings.EqualFold(strings.TrimSpace(other.Name), name) {
			return fmt.Errorf("script name already exists: %s", name)
		}
	}
	return nil
}

// validateUniqueTaskName 校验任务名称唯一。
func validateUniqueTaskName(items []*models.Task, name, selfID string) error {
	name = strings.TrimSpace(name)
	if name == "" {
		return fmt.Errorf("task name is required")
	}
	for _, other := range items {
		if other.ID != selfID && strings.EqualFold(strings.TrimSpace(other.Name), name) {
			return fmt.Errorf("task name already exists: %s", name)
		}
	}
	return nil
}

// validateParameters 校验参数名称非空且不重复（缺陷 FAIL-A-014）。
func validateParameters(params []models.Parameter) error {
	seen := map[string]bool{}
	for _, p := range params {
		name := strings.TrimSpace(p.Name)
		if name == "" {
			return fmt.Errorf("parameter name is required")
		}
		key := strings.ToLower(name)
		if seen[key] {
			return fmt.Errorf("duplicate parameter name: %s", name)
		}
		seen[key] = true
	}
	return nil
}
