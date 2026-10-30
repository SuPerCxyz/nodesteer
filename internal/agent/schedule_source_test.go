package agent

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// seedAgentTask 在同步事务内写入本地任务缓存（与 applySync 同路径）。
func seedAgentTask(t *testing.T, a *Agent, task *models.Task) {
	t.Helper()
	raw, err := json.Marshal(task)
	if err != nil {
		t.Fatalf("marshal task: %v", err)
	}
	if err := a.store.BeginSync(a.ctx); err != nil {
		t.Fatalf("begin seed tx: %v", err)
	}
	if err := a.store.UpsertTask(a.ctx, task.ID, raw, task.Revision, task.Enabled); err != nil {
		_ = a.store.RollbackSync()
		t.Fatalf("seed task: %v", err)
	}
	if err := a.store.CommitSync(); err != nil {
		t.Fatalf("commit seed: %v", err)
	}
}

// seedAgentScript 在同步事务内写入本地脚本缓存。
func seedAgentScript(t *testing.T, a *Agent, row *ScriptRow) {
	t.Helper()
	if err := a.store.BeginSync(a.ctx); err != nil {
		t.Fatalf("begin seed tx: %v", err)
	}
	if err := a.store.UpsertScript(a.ctx, row); err != nil {
		_ = a.store.RollbackSync()
		t.Fatalf("seed script: %v", err)
	}
	if err := a.store.CommitSync(); err != nil {
		t.Fatalf("commit seed: %v", err)
	}
}

// waitTerminalExecution 等待执行进入终态并返回 Journal 记录。
func waitTerminalExecution(t *testing.T, a *Agent, id string) *LocalExecution {
	t.Helper()
	deadline := time.Now().Add(15 * time.Second)
	for {
		ex, err := a.store.GetExecution(a.ctx, id)
		if err != nil {
			t.Fatalf("get execution %s: %v", id, err)
		}
		if ex == nil {
			t.Fatalf("execution %s journal missing", id)
		}
		switch ex.Status {
		case models.ExecStatusSuccess, models.ExecStatusFailed, models.ExecStatusTimedOut,
			models.ExecStatusCanceled, models.ExecStatusBlocked, models.ExecStatusSkipped:
			return ex
		}
		if time.Now().After(deadline) {
			t.Fatalf("execution %s not terminal, status=%q", id, ex.Status)
		}
		time.Sleep(20 * time.Millisecond)
	}
}

// triggerAndWait 触发调度并按 slot 找到本地 Journal，等待终态。
func triggerAndWait(t *testing.T, a *Agent, sch *models.Schedule, at time.Time) *LocalExecution {
	t.Helper()
	if err := a.TriggerSchedule(a.ctx, sch, at); err != nil {
		t.Fatalf("trigger schedule: %v", err)
	}
	slot := at.UTC().Truncate(time.Second).Format(time.RFC3339Nano)
	ex, err := a.store.FindExecutionBySlot(a.ctx, sch.TaskID, a.nodeID, slot)
	if err != nil || ex == nil {
		t.Fatalf("execution journal missing after trigger: %v", err)
	}
	return waitTerminalExecution(t, a, ex.ID)
}

func newScheduleTestAgent(t *testing.T) *Agent {
	t.Helper()
	a := newOnStartTestAgent(t, t.TempDir())
	t.Cleanup(func() { _ = a.Close() })
	return a
}

func scheduleForTest(id, taskID, nodeID string) *models.Schedule {
	return &models.Schedule{
		ID: id, TaskID: taskID, Type: models.ScheduleTypeInterval, IntervalSec: 60,
		Target:         models.Target{Type: "node", NodeIDs: []string{nodeID}},
		Timeout:        5,
		Retry:          0,
		Timezone:       "UTC",
		ExecutionOwner: models.ExecutionOwnerAgent,
		OfflinePolicy:  models.OfflinePolicyAllowOffline,
		MisfirePolicy:  models.MisfirePolicyRunOnce,
		Enabled:        true, Revision: 1,
	}
}

// TestTriggerScheduleMergesTaskAndScheduleConditions Agent-owned 调度需要合并
// Task 本地条件与 Schedule 远程条件：本地满足、远程未知时应 BLOCKED，
// 同时 Journal 记录 Schedule Revision。
func TestTriggerScheduleMergesTaskAndScheduleConditions(t *testing.T) {
	a := newScheduleTestAgent(t)
	marker := filepath.Join(t.TempDir(), "present")
	if err := os.WriteFile(marker, []byte("ok"), 0o600); err != nil {
		t.Fatal(err)
	}
	task := &models.Task{
		ID: "task-merged-cond", Type: models.TaskTypeCommand, Command: "true",
		Enabled: true, Revision: 1,
		Condition: &models.Condition{Type: "local", Local: &models.LocalCondition{
			Metric: "file_exists", Operator: "==", Value: "true", Path: marker,
		}},
	}
	seedAgentTask(t, a, task)
	sch := scheduleForTest("sch-merged-cond", task.ID, a.nodeID)
	sch.Revision = 7
	sch.Condition = &models.Condition{Type: "remote", Remote: &models.RemoteCondition{
		NodeID: "other-node", Property: "online", Operator: "==", Value: "online",
	}}
	ex := triggerAndWait(t, a, sch, time.Now().UTC())
	if ex.Status != models.ExecStatusBlocked {
		t.Fatalf("merged condition status = %s, want BLOCKED", ex.Status)
	}
	if ex.ScheduleRevision != 7 {
		t.Fatalf("journal schedule_revision = %d, want 7", ex.ScheduleRevision)
	}
}

// TestTriggerScheduleUsesScheduleStrategy Agent-owned 调度必须使用 Schedule 上的
// Timeout/Retry/RunUser（Task 已不持有这些字段），Script.RunUser 仅作兜底。
func TestTriggerScheduleUsesScheduleStrategy(t *testing.T) {
	t.Run("retry from schedule", func(t *testing.T) {
		a := newScheduleTestAgent(t)
		marker := filepath.Join(t.TempDir(), "attempt")
		task := &models.Task{
			ID: "task-retry", Type: models.TaskTypeCommand, Enabled: true, Revision: 1,
			Command: fmt.Sprintf(`if [ -f %q ]; then exit 0; else touch %q; exit 1; fi`, marker, marker),
		}
		seedAgentTask(t, a, task)
		sch := scheduleForTest("sch-retry", task.ID, a.nodeID)
		sch.Retry = 1

		ex := triggerAndWait(t, a, sch, time.Now())
		if ex.Status != models.ExecStatusSuccess {
			t.Fatalf("schedule retry=1 must re-run after first failure: status=%q reason=%q", ex.Status, ex.BlockReason)
		}
	})

	t.Run("timeout from schedule", func(t *testing.T) {
		a := newScheduleTestAgent(t)
		task := &models.Task{
			ID: "task-timeout", Type: models.TaskTypeCommand, Enabled: true, Revision: 1,
			Command: "sleep 5",
		}
		seedAgentTask(t, a, task)
		sch := scheduleForTest("sch-timeout", task.ID, a.nodeID)
		sch.Timeout = 1

		ex := triggerAndWait(t, a, sch, time.Now())
		if ex.Status != models.ExecStatusTimedOut {
			t.Fatalf("schedule timeout=1 must time out: status=%q reason=%q", ex.Status, ex.BlockReason)
		}
	})

	t.Run("run user from schedule", func(t *testing.T) {
		a := newScheduleTestAgent(t)
		task := &models.Task{
			ID: "task-runuser", Type: models.TaskTypeCommand, Enabled: true, Revision: 1,
			Command: "true",
		}
		seedAgentTask(t, a, task)
		sch := scheduleForTest("sch-runuser", task.ID, a.nodeID)
		sch.RunUser = "nodesteer-user-does-not-exist"

		ex := triggerAndWait(t, a, sch, time.Now())
		if ex.Status != models.ExecStatusFailed {
			t.Fatalf("invalid schedule run_user must fail execution: status=%q", ex.Status)
		}
		if !strings.Contains(ex.BlockReason, "lookup run user") {
			t.Fatalf("run_user from schedule not applied, reason=%q", ex.BlockReason)
		}
	})

	t.Run("script run user fallback", func(t *testing.T) {
		a := newScheduleTestAgent(t)
		seedAgentScript(t, a, &ScriptRow{
			ID: "script-runuser", Name: "s", Interpreter: "shell", Content: "true",
			RunUser: "nodesteer-user-does-not-exist", Enabled: true, Revision: 1,
		})
		task := &models.Task{
			ID: "task-script-runuser", Type: models.TaskTypeScript, Enabled: true, Revision: 1,
			ScriptID: "script-runuser",
		}
		seedAgentTask(t, a, task)
		sch := scheduleForTest("sch-script-runuser", task.ID, a.nodeID)
		// Schedule.RunUser 为空时才回退到 Script.RunUser。
		sch.RunUser = ""

		ex := triggerAndWait(t, a, sch, time.Now())
		if ex.Status != models.ExecStatusFailed || !strings.Contains(ex.BlockReason, "lookup run user") {
			t.Fatalf("script run_user fallback not applied: status=%q reason=%q", ex.Status, ex.BlockReason)
		}
	})
}

// TestTriggerScheduleRejectsManualAndInvalidTarget manual 与缺少/未命中目标都不得自动执行。
func TestTriggerScheduleRejectsManualAndInvalidTarget(t *testing.T) {
	a := newScheduleTestAgent(t)
	task := &models.Task{
		ID: "task-reject", Type: models.TaskTypeCommand, Command: "true", Enabled: true, Revision: 1,
	}
	seedAgentTask(t, a, task)

	manual := scheduleForTest("sch-manual", task.ID, a.nodeID)
	manual.Type = models.ScheduleTypeManual
	if err := a.TriggerSchedule(a.ctx, manual, time.Now()); err == nil {
		t.Fatal("manual schedule must be rejected by TriggerSchedule")
	}

	other := scheduleForTest("sch-other", task.ID, "other-node")
	if err := a.TriggerSchedule(a.ctx, other, time.Now()); err == nil {
		t.Fatal("schedule targeting another node must be rejected")
	}

	noTarget := scheduleForTest("sch-no-target", task.ID, a.nodeID)
	noTarget.Target = models.Target{}
	if err := a.TriggerSchedule(a.ctx, noTarget, time.Now()); err == nil {
		t.Fatal("schedule without target must be rejected")
	}

	if n := countTaskExecutions(t, a, task.ID); n != 0 {
		t.Fatalf("rejected schedules must not create executions, got %d", n)
	}
}

// TestTriggerScheduleJournalAndReportSource 调度 Journal 写入 schedule_id/created_at，
// 终态上报载荷原样携带来源；手动执行允许为空。
func TestTriggerScheduleJournalAndReportSource(t *testing.T) {
	a := newScheduleTestAgent(t)
	task := &models.Task{
		ID: "task-source", Type: models.TaskTypeCommand, Command: "true", Enabled: true, Revision: 1,
	}
	seedAgentTask(t, a, task)
	sch := scheduleForTest("sch-source", task.ID, a.nodeID)

	ex := triggerAndWait(t, a, sch, time.Now())
	if ex.ScheduleID != sch.ID {
		t.Fatalf("journal schedule_id = %q, want %q", ex.ScheduleID, sch.ID)
	}
	if ex.CreatedAt == "" {
		t.Fatal("journal created_at must be set for schedule execution")
	}
	if ex.TriggerType != models.TriggerSchedule {
		t.Fatalf("journal trigger_type = %q, want %q", ex.TriggerType, models.TriggerSchedule)
	}

	payload := executionFinishedPayload(ex)
	if payload.ScheduleID != sch.ID || payload.CreatedAt != ex.CreatedAt {
		t.Fatalf("finished payload source mismatch: schedule_id=%q created_at=%q", payload.ScheduleID, payload.CreatedAt)
	}

	manual := executionFinishedPayload(&LocalExecution{ID: "manual-1"})
	if manual.ScheduleID != "" || manual.CreatedAt != "" {
		t.Fatalf("manual execution must report empty source, got schedule_id=%q created_at=%q", manual.ScheduleID, manual.CreatedAt)
	}
}

// TestOnRunExecutionJournalSource Hub 下发 Manual Run 写入 created_at、不写 schedule_id；
// 调度下发（Hub-owned）保留 schedule_id/created_at。
func TestOnRunExecutionJournalSource(t *testing.T) {
	a := newScheduleTestAgent(t)
	a.ready = true

	manualCreatedAt := "2026-01-02T03:04:05Z"
	a.OnRunExecution(protocol.NewEnvelope(protocol.MsgRunExecution, "exec-manual", protocol.RunExecutionPayload{
		ExecutionID: "exec-manual", TaskID: "task-manual", Type: models.TaskTypeCommand,
		Command: "true", TriggerType: models.TriggerManual, CreatedAt: manualCreatedAt,
	}))
	manual := waitTerminalExecution(t, a, "exec-manual")
	if manual.ScheduleID != "" {
		t.Fatalf("manual run must not write schedule_id, got %q", manual.ScheduleID)
	}
	if manual.CreatedAt != manualCreatedAt {
		t.Fatalf("manual run created_at = %q, want %q", manual.CreatedAt, manualCreatedAt)
	}

	scheduleCreatedAt := "2026-01-02T04:05:06Z"
	a.OnRunExecution(protocol.NewEnvelope(protocol.MsgRunExecution, "exec-schedule", protocol.RunExecutionPayload{
		ExecutionID: "exec-schedule", TaskID: "task-schedule", Type: models.TaskTypeCommand,
		Command: "true", TriggerType: models.TriggerSchedule,
		ScheduleID: "sch-hub-owned", CreatedAt: scheduleCreatedAt,
	}))
	scheduled := waitTerminalExecution(t, a, "exec-schedule")
	if scheduled.ScheduleID != "sch-hub-owned" {
		t.Fatalf("schedule run schedule_id = %q", scheduled.ScheduleID)
	}
	if scheduled.CreatedAt != scheduleCreatedAt {
		t.Fatalf("schedule run created_at = %q, want %q", scheduled.CreatedAt, scheduleCreatedAt)
	}
}

// TestOnDeployRequestJournalSource 部署下发读取 payload 的 schedule_id/created_at，
// 结果回调从 Journal 回传同样的来源。
func TestOnDeployRequestJournalSource(t *testing.T) {
	a := newScheduleTestAgent(t)
	a.ready = true
	createdAt := "2026-02-03T04:05:06Z"
	a.OnDeployRequest(protocol.NewEnvelope(protocol.MsgDeployRequest, "deploy-1", protocol.DeployRequestPayload{
		AppID: "app-1", AppVersion: "1.0", Operation: "start",
		ScheduleID: "sch-deploy", CreatedAt: createdAt,
	}))

	ex := waitTerminalExecution(t, a, "deploy-1")
	if ex.ScheduleID != "sch-deploy" {
		t.Fatalf("deploy journal schedule_id = %q", ex.ScheduleID)
	}
	if ex.CreatedAt != createdAt {
		t.Fatalf("deploy journal created_at = %q, want %q", ex.CreatedAt, createdAt)
	}
	if ex.TriggerType != models.TriggerSchedule {
		t.Fatalf("deploy journal trigger_type = %q, want %q", ex.TriggerType, models.TriggerSchedule)
	}
}
