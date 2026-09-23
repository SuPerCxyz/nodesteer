package hub

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/metrics"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
	"github.com/google/uuid"
)

// ErrAgentOffline Agent 离线
var ErrAgentOffline = errors.New("agent offline")

// ExecutionManager Hub 执行管理
type ExecutionManager struct {
	store        store.Store
	sessions     *SessionManager
	nodes        *NodeManager
	logger       *slog.Logger
	mu           sync.Mutex
	activeByNode map[string]int
	metrics      *metrics.Metrics
}

// SetMetrics 注入指标
func (em *ExecutionManager) SetMetrics(m *metrics.Metrics) { em.metrics = m }

// NewExecutionManager 创建执行管理器
func NewExecutionManager(st store.Store, sm *SessionManager, nm *NodeManager, logger *slog.Logger) *ExecutionManager {
	return &ExecutionManager{
		store:        st,
		sessions:     sm,
		nodes:        nm,
		logger:       logger,
		activeByNode: map[string]int{},
	}
}

// Capability 校验所需 Capability
func requiredCapability(taskType string) string {
	switch taskType {
	case models.TaskTypeAppDeploy, models.TaskTypeAppOperation:
		return models.CapApplicationDeploy
	default:
		return models.CapScript
	}
}

func executionEnvironment(task *models.Task, script *models.Script, supplied map[string]string) (map[string]string, []string, error) {
	env := map[string]string{}
	secrets := []string{}
	values := map[string]string{}
	if script != nil {
		for k, v := range script.Environment {
			env[k] = v
		}
		for _, p := range script.Parameters {
			if p.Default != "" {
				values[p.Name] = p.Default
			}
		}
	}
	for _, p := range task.Parameters {
		if p.Default != "" {
			values[p.Name] = p.Default
		}
	}
	for k, v := range task.ParamValues {
		values[k] = v
	}
	for k, v := range supplied {
		values[k] = v
	}
	for _, def := range append(append([]models.Parameter{}, task.Parameters...), scriptParameters(script)...) {
		if values[def.Name] == "" && def.Required {
			return nil, nil, fmt.Errorf("required parameter %s is missing", def.Name)
		}
	}
	for k, v := range values {
		env["NODESTEER_PARAM_"+k] = v
	}
	for _, def := range append(append([]models.Parameter{}, task.Parameters...), scriptParameters(script)...) {
		if def.Type == "secret" && values[def.Name] != "" {
			secrets = append(secrets, values[def.Name])
		}
	}
	return env, secrets, nil
}

func scriptParameters(script *models.Script) []models.Parameter {
	if script == nil {
		return nil
	}
	return script.Parameters
}

// RunManual 手动执行（面向在线 Agent）
func (em *ExecutionManager) RunManual(ctx context.Context, task *models.Task, nodeIDs []string, params map[string]string) ([]*models.Execution, error) {
	if !task.Enabled {
		return nil, fmt.Errorf("task %s is disabled", task.ID)
	}
	if err := em.ensureReferencedScriptEnabled(ctx, task); err != nil {
		return nil, err
	}
	var out []*models.Execution
	for _, nodeID := range nodeIDs {
		ex, err := em.createAndDispatch(ctx, task, nodeID, models.TriggerManual, time.Time{}, params, false)
		if err != nil {
			return nil, err
		}
		out = append(out, ex)
	}
	return out, nil
}

// ensureReferencedScriptEnabled 阻断引用了已禁用脚本的任务继续执行（缺陷 FAIL-A-006）。
// 任务创建时已校验脚本启用状态，但脚本随后被禁用时仍需在运行前拦截。
func (em *ExecutionManager) ensureReferencedScriptEnabled(ctx context.Context, task *models.Task) error {
	if task.Type != models.TaskTypeScript || task.ScriptID == "" {
		return nil
	}
	sc, err := em.store.GetScript(ctx, task.ScriptID)
	if err != nil {
		return fmt.Errorf("referenced script not found")
	}
	if !sc.Enabled {
		return fmt.Errorf("referenced script %s is disabled", sc.ID)
	}
	return nil
}

// RunScheduledHub 由 Hub 触发的调度执行
func (em *ExecutionManager) RunScheduledHub(ctx context.Context, task *models.Task, nodeIDs []string, scheduledTime time.Time) ([]*models.Execution, error) {
	if err := em.ensureReferencedScriptEnabled(ctx, task); err != nil {
		return nil, err
	}
	var out []*models.Execution
	for _, nodeID := range nodeIDs {
		ex, err := em.createAndDispatch(ctx, task, nodeID, models.TriggerSchedule, scheduledTime, nil, false)
		if err != nil {
			// 已存在的 Slot 幂等跳过
			if errors.Is(err, errSlotExists) {
				continue
			}
			return out, err
		}
		out = append(out, ex)
	}
	return out, nil
}

var errSlotExists = errors.New("execution slot already exists")

// createAndDispatch 创建 Execution 并下发
func (em *ExecutionManager) createAndDispatch(ctx context.Context, task *models.Task, nodeID, trigger string, schedTime time.Time, params map[string]string, offline bool) (*models.Execution, error) {
	node, err := em.nodes.GetNode(ctx, nodeID)
	if err != nil {
		return nil, err
	}
	if node.Status != models.NodeStatusOnline {
		return nil, fmt.Errorf("node %s is not online (status=%s)", nodeID, node.Status)
	}
	if node.Status == models.NodeStatusDisabled {
		return nil, fmt.Errorf("node %s is disabled", nodeID)
	}

	// Capability 校验
	cap := requiredCapability(task.Type)
	if cap != "" && !node.Capabilities[cap] {
		return nil, fmt.Errorf("node %s lacks capability %s", nodeID, cap)
	}

	ex := &models.Execution{
		ID:            uuid.NewString(),
		TaskID:        task.ID,
		TaskRevision:  task.Revision,
		ScriptID:      task.ScriptID,
		NodeID:        nodeID,
		TriggerType:   trigger,
		ScheduledTime: schedTime,
		Status:        models.ExecStatusPending,
		Offline:       offline,
	}

	// 幂等：调度 Slot 唯一
	if trigger == models.TriggerSchedule && !schedTime.IsZero() {
		slot := schedTime.UTC().Truncate(time.Second).Format(time.RFC3339Nano)
		existing, err := em.store.FindExecutionBySlot(ctx, task.ID, nodeID, slot)
		if err == nil && existing != nil {
			return nil, errSlotExists
		}
		ex.ScheduledTime = schedTime.UTC().Truncate(time.Second)
	}

	if err := em.store.CreateExecution(ctx, ex); err != nil {
		return nil, err
	}

	if err := em.dispatchToAgent(ctx, ex, task, params); err != nil {
		// 下发失败则标记失败
		ex.Status = models.ExecStatusFailed
		ex.EndTime = time.Now()
		ex.BlockReason = err.Error()
		em.store.UpdateExecution(ctx, ex)
		return nil, err
	}
	return ex, nil
}

// dispatchToAgent 下发执行到 Agent
func (em *ExecutionManager) dispatchToAgent(ctx context.Context, ex *models.Execution, task *models.Task, params map[string]string) error {
	conn, ok := em.sessions.Get(ex.NodeID)
	if !ok {
		return ErrAgentOffline
	}

	var scriptContent, interpreter, workingDir, runUser string
	var script *models.Script
	var scriptRev int64
	if task.ScriptID != "" {
		if sc, err := em.store.GetScript(ctx, task.ScriptID); err == nil {
			script = sc
			scriptContent = sc.Content
			interpreter = sc.Interpreter
			workingDir = sc.WorkingDir
			runUser = sc.RunUser
			scriptRev = sc.Revision
			ex.ScriptID = sc.ID
			ex.ScriptRevision = scriptRev
		}
	}

	env, secrets, err := executionEnvironment(task, script, params)
	if err != nil {
		return err
	}

	payload := protocol.RunExecutionPayload{
		ExecutionID:    ex.ID,
		TaskID:         task.ID,
		TaskRevision:   task.Revision,
		ScriptID:       ex.ScriptID,
		ScriptRevision: scriptRev,
		Type:           task.Type,
		Command:        task.Command,
		ScriptContent:  scriptContent,
		Interpreter:    interpreter,
		WorkingDir:     workingDir,
		RunUser:        task.RunUser,
		Environment:    env,
		SecretValues:   secrets,
		Timeout:        task.Timeout,
		Retry:          task.Retry,
		AppID:          task.ApplicationID,
		AppOperation:   task.AppOperation,
		TriggerType:    ex.TriggerType,
	}
	if task.ApplicationID != "" {
		if app, err := em.store.GetApplication(ctx, task.ApplicationID); err == nil {
			payload.ApplicationVersion = app.Version
		}
	}
	if payload.RunUser == "" {
		payload.RunUser = runUser
	}
	if !ex.ScheduledTime.IsZero() {
		payload.ScheduledTime = ex.ScheduledTime.Format(time.RFC3339Nano)
	}
	if task.Condition != nil {
		b, _ := json.Marshal(task.Condition)
		payload.Condition = b
	}

	if err := conn.Send(protocol.NewEnvelope(protocol.MsgRunExecution, ex.ID, payload)); err != nil {
		return err
	}
	return nil
}

// MarkStarted 执行开始回调
func (em *ExecutionManager) MarkStarted(ctx context.Context, nodeID, execID, startTime string) {
	ex, err := em.store.GetExecution(ctx, execID)
	if err != nil {
		return
	}
	ex.Status = models.ExecStatusRunning
	if startTime != "" {
		ex.StartTime = parseRFC3339(startTime)
	} else {
		ex.StartTime = time.Now()
	}
	em.store.UpdateExecution(ctx, ex)
	em.mu.Lock()
	em.activeByNode[nodeID]++
	em.mu.Unlock()
}

// MarkFinished 执行完成回调
func (em *ExecutionManager) MarkFinished(ctx context.Context, nodeID string, p protocol.ExecutionFinishedPayload) error {
	ex, err := em.store.GetExecution(ctx, p.ExecutionID)
	if err != nil {
		// Agent 侧创建的执行（如 Agent-owned Schedule），Hub 需幂等创建
		if errors.Is(err, sql.ErrNoRows) {
			newEx := &models.Execution{
				ID:              p.ExecutionID,
				TaskID:          p.TaskID,
				TaskRevision:    p.TaskRevision,
				ScriptID:        p.ScriptID,
				ScriptRevision:  p.ScriptRevision,
				NodeID:          p.NodeID,
				TriggerType:     p.TriggerType,
				Status:          p.Status,
				ExitCode:        p.ExitCode,
				Stdout:          truncateLog(p.Stdout),
				Stderr:          truncateLog(p.Stderr),
				StdoutTruncated: p.StdoutTruncated,
				StderrTruncated: p.StderrTruncated,
				EndTime:         parseRFC3339OrNow(p.EndTime),
				Offline:         p.Offline,
				Synced:          true,
				BlockReason:     p.BlockReason,
			}
			if p.StartTime != "" {
				newEx.StartTime = parseRFC3339(p.StartTime)
			}
			if p.ScheduledTime != "" {
				newEx.ScheduledTime = parseRFC3339OrNow(p.ScheduledTime)
			}
			if newEx.NodeID == "" {
				newEx.NodeID = nodeID
			}
			if err := em.store.CreateExecution(ctx, newEx); err != nil {
				em.logger.Warn("create execution from agent failed", "id", p.ExecutionID, "error", err)
				return err
			}
			if err := em.recordApplicationExecutionState(ctx, p, nodeID); err != nil {
				return err
			}
			return nil
		}
		em.logger.Warn("mark finished: execution lookup failed", "id", p.ExecutionID, "error", err)
		return err
	}
	if isTerminalExecution(ex.Status) {
		return nil
	}
	ex.Status = p.Status
	ex.ExitCode = p.ExitCode
	if p.TaskID != "" {
		ex.TaskID = p.TaskID
	}
	if p.TaskRevision != 0 {
		ex.TaskRevision = p.TaskRevision
	}
	if p.ScriptID != "" {
		ex.ScriptID = p.ScriptID
	}
	if p.ScriptRevision != 0 {
		ex.ScriptRevision = p.ScriptRevision
	}
	if p.StartTime != "" {
		ex.StartTime = parseRFC3339(p.StartTime)
	}
	ex.Stdout = truncateLog(p.Stdout)
	ex.Stderr = truncateLog(p.Stderr)
	ex.StdoutTruncated = p.StdoutTruncated
	ex.StderrTruncated = p.StderrTruncated
	ex.Synced = true
	if p.EndTime != "" {
		ex.EndTime = parseRFC3339(p.EndTime)
	} else {
		ex.EndTime = time.Now()
	}
	if err := em.store.UpdateExecution(ctx, ex); err != nil {
		return err
	}
	if err := em.recordApplicationExecutionState(ctx, p, nodeID); err != nil {
		return err
	}
	em.mu.Lock()
	em.activeByNode[nodeID]--
	if em.activeByNode[nodeID] < 0 {
		em.activeByNode[nodeID] = 0
	}
	em.mu.Unlock()
	if em.metrics != nil {
		em.metrics.AddExecution(p.Status)
	}
	return nil
}

func (em *ExecutionManager) recordApplicationExecutionState(ctx context.Context, p protocol.ExecutionFinishedPayload, nodeID string) error {
	if p.ApplicationID == "" {
		return nil
	}
	health := p.ApplicationHealth
	if health == "" {
		if p.Status == models.ExecStatusSuccess {
			health = "healthy"
		} else {
			health = "unhealthy"
		}
	}
	return em.store.SetApplicationNodeState(ctx, &models.ApplicationNodeState{
		ApplicationID: p.ApplicationID,
		NodeID:        nodeID,
		Version:       p.ApplicationVersion,
		Operation:     p.ApplicationOperation,
		Health:        health,
		Error:         p.BlockReason,
	})
}

// HandleDeployResult 处理部署结果并终结对应 Execution
func (em *ExecutionManager) HandleDeployResult(ctx context.Context, nodeID string, p protocol.DeployResultPayload) error {
	if p.ExecutionID == "" {
		em.logger.Warn("deploy result without execution id", "app", p.AppID)
		return errors.New("deploy result missing execution id")
	}
	if p.Health == "" {
		if p.OK {
			p.Health = "healthy"
		} else {
			p.Health = "unhealthy"
		}
	}
	if err := em.store.SetApplicationNodeState(ctx, &models.ApplicationNodeState{
		ApplicationID: p.AppID,
		NodeID:        nodeID,
		Version:       p.Version,
		Operation:     p.Operation,
		Health:        p.Health,
		Error:         p.Error,
	}); err != nil {
		return err
	}
	ex, err := em.store.GetExecution(ctx, p.ExecutionID)
	if err != nil {
		// Agent-owned 调度等场景：Hub 无此 execution，按 AppID 幂等创建并终结
		status := models.ExecStatusSuccess
		if !p.OK {
			status = models.ExecStatusFailed
		}
		taskID := p.TaskID
		if taskID == "" {
			taskID = p.AppID
		}
		trigger := p.TriggerType
		if trigger == "" {
			trigger = models.TriggerSchedule
		}
		newEx := &models.Execution{
			ID:            p.ExecutionID,
			TaskID:        taskID,
			TaskRevision:  p.TaskRevision,
			NodeID:        nodeID,
			TriggerType:   trigger,
			ScheduledTime: parseRFC3339OrZero(p.ScheduledTime),
			Status:        status,
			EndTime:       time.Now(),
			BlockReason:   p.Error,
			Offline:       false,
			Synced:        true,
		}
		if p.StartTime != "" {
			newEx.StartTime = parseRFC3339(p.StartTime)
		}
		if err := em.store.CreateExecution(ctx, newEx); err != nil {
			em.logger.Warn("deploy result: create failed", "id", p.ExecutionID, "error", err)
			return err
		}
		if em.metrics != nil {
			em.metrics.AddExecution(status)
		}
		em.logger.Info("deployment finished (created from agent)", "id", p.ExecutionID, "app", p.AppID,
			"operation", p.Operation, "ok", p.OK, "health", p.Health, "rollback", p.Rollback)
		return nil
	}
	if isTerminalExecution(ex.Status) {
		return nil
	}
	ex.EndTime = time.Now()
	if p.StartTime != "" {
		ex.StartTime = parseRFC3339(p.StartTime)
	}
	if p.OK {
		ex.Status = models.ExecStatusSuccess
	} else {
		ex.Status = models.ExecStatusFailed
		ex.BlockReason = p.Error
	}
	if err := em.store.UpdateExecution(ctx, ex); err != nil {
		em.logger.Warn("deploy result: update failed", "id", p.ExecutionID, "error", err)
		return err
	}
	em.mu.Lock()
	em.activeByNode[nodeID]--
	if em.activeByNode[nodeID] < 0 {
		em.activeByNode[nodeID] = 0
	}
	em.mu.Unlock()
	if em.metrics != nil {
		em.metrics.AddExecution(ex.Status)
	}
	em.logger.Info("deployment finished", "id", p.ExecutionID, "app", p.AppID,
		"operation", p.Operation, "ok", p.OK, "health", p.Health, "rollback", p.Rollback)
	return nil
}

func isTerminalExecution(status string) bool {
	switch status {
	case models.ExecStatusSuccess, models.ExecStatusFailed, models.ExecStatusSkipped,
		models.ExecStatusCanceled, models.ExecStatusTimedOut, models.ExecStatusBlocked:
		return true
	default:
		return false
	}
}

// ListLogChunks 查询执行日志分片
func (em *ExecutionManager) ListLogChunks(ctx context.Context, executionID string) ([]store.LogChunk, error) {
	return em.store.ListLogChunks(ctx, executionID)
}

// AppendLog 追加日志分片（Realtime Log）
// Realtime 分片仅存 log_chunks 表；执行最终 stdout/stderr 由 ExecutionFinished 整包写入（权威）。
func (em *ExecutionManager) AppendLog(ctx context.Context, nodeID string, p protocol.LogChunkPayload) {
	if _, err := em.store.GetExecution(ctx, p.ExecutionID); err != nil {
		return
	}
	// 由 Hub 维护 seq 顺序，忽略 Agent 侧（并发安全、顺序稳定）
	chunks, _ := em.store.ListLogChunks(ctx, p.ExecutionID)
	seq := int64(len(chunks))
	if err := em.store.AppendLogChunk(ctx, p.ExecutionID, p.Stream, seq, p.Chunk); err != nil {
		em.logger.Warn("append log chunk failed", "id", p.ExecutionID, "error", err)
	}
}

// Cancel 取消执行
func (em *ExecutionManager) Cancel(ctx context.Context, execID string) error {
	ex, err := em.store.GetExecution(ctx, execID)
	if err != nil {
		return err
	}
	conn, ok := em.sessions.Get(ex.NodeID)
	if !ok {
		return ErrAgentOffline
	}
	if err := conn.Send(protocol.NewEnvelope(protocol.MsgCancelExecution, execID, protocol.CancelExecutionPayload{
		ExecutionID: execID,
	})); err != nil {
		return err
	}
	return nil
}

// UpsertFromAgent Agent 重连后上报执行结果（幂等）
func (em *ExecutionManager) UpsertFromAgent(ctx context.Context, e *models.Execution) error {
	existing, err := em.store.GetExecution(ctx, e.ID)
	if err == nil {
		// 已存在的 PENDING/RUNNING 记录可由 Agent 的最终结果补全；终态不回退。
		if !isTerminalExecution(existing.Status) && e.Status != models.ExecStatusPending {
			existing.Status = e.Status
			existing.TaskID = e.TaskID
			existing.TaskRevision = e.TaskRevision
			existing.ScriptID = e.ScriptID
			existing.ScriptRevision = e.ScriptRevision
			existing.StartTime = e.StartTime
			existing.ExitCode = e.ExitCode
			existing.Stdout = truncateLog(e.Stdout)
			existing.Stderr = truncateLog(e.Stderr)
			existing.EndTime = e.EndTime
			existing.Offline = e.Offline
			existing.Synced = true
			existing.BlockReason = e.BlockReason
			e.Synced = true
			return em.store.UpdateExecution(ctx, existing)
		}
		return nil
	}
	e.Synced = true
	return em.store.CreateExecution(ctx, e)
}

// GetExecution 获取执行
func (em *ExecutionManager) GetExecution(ctx context.Context, id string) (*models.Execution, error) {
	return em.store.GetExecution(ctx, id)
}

// ListExecutions 列表
func (em *ExecutionManager) ListExecutions(ctx context.Context, filter store.ExecutionFilter) ([]*models.Execution, error) {
	return em.store.ListExecutions(ctx, filter)
}

// ReconcileAgentExecutions Agent 重连时上报的未同步执行
func (em *ExecutionManager) ReconcileAgentExecutions(ctx context.Context, nodeID string, execs []*models.Execution) error {
	for _, e := range execs {
		if e == nil {
			continue
		}
		if err := em.UpsertFromAgent(ctx, e); err != nil {
			em.logger.Warn("reconcile execution failed", "id", e.ID, "error", err)
		}
	}
	return nil
}

// parseRFC3339OrNow 解析时间，失败返回当前时间
func parseRFC3339OrNow(s string) time.Time {
	if s == "" {
		return time.Now()
	}
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return time.Now()
	}
	return t
}

func parseRFC3339OrZero(s string) time.Time {
	if s == "" {
		return time.Time{}
	}
	return parseRFC3339(s)
}

// parseRFC3339 解析时间
func parseRFC3339(s string) time.Time {
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return time.Now()
	}
	return t
}

// truncateLog 日志截断（Hub 端保险）
func truncateLog(s string) string {
	const maxLog = 1 << 20 // 1MB
	if len(s) > maxLog {
		return s[:maxLog]
	}
	return s
}
