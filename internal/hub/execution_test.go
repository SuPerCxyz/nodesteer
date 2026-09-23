package hub

import (
	"context"
	"errors"
	"io"
	"log/slog"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func newTestStore(t *testing.T) store.Store {
	t.Helper()
	s, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func newTestExecMgr(t *testing.T, st store.Store) *ExecutionManager {
	t.Helper()
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	return NewExecutionManager(st, NewSessionManager(), NewNodeManager(st, NewRevisionManager(st)), logger)
}

func TestUpsertFromAgentIdempotent(t *testing.T) {
	st := newTestStore(t)
	em := newTestExecMgr(t, st)
	ctx := context.Background()

	// 离线执行创建
	e := &models.Execution{
		ID: "offline-exec-1", TaskID: "t1", NodeID: "n1",
		Status: "FAILED", ExitCode: 3, TriggerType: "schedule",
		ScheduledTime: time.Now().UTC().Truncate(time.Second),
		EndTime:       time.Now(), Stdout: "out", Offline: true,
	}
	if err := em.UpsertFromAgent(ctx, e); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	got, err := st.GetExecution(ctx, e.ID)
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if got.Status != "FAILED" || got.ExitCode != 3 {
		t.Fatalf("execution not persisted correctly")
	}
	// 重复上传（幂等）
	if err := em.UpsertFromAgent(ctx, e); err != nil {
		t.Fatalf("re-upload should be idempotent: %v", err)
	}
}

func TestMarkFinishedCreatesFromAgent(t *testing.T) {
	st := newTestStore(t)
	em := newTestExecMgr(t, st)
	ctx := context.Background()

	// Agent-owned schedule 产生的执行（Hub 无记录）
	p := protocol.ExecutionFinishedPayload{
		ExecutionID:   "agent-created-1",
		TaskID:        "t1",
		NodeID:        "n1",
		TriggerType:   "schedule",
		ScheduledTime: time.Now().UTC().Truncate(time.Second).Format(time.RFC3339Nano),
		Status:        "SUCCESS",
		ExitCode:      0,
		Stdout:        "hello",
		EndTime:       time.Now().Format(time.RFC3339Nano),
	}
	em.MarkFinished(ctx, "n1", p)
	ex, err := st.GetExecution(ctx, "agent-created-1")
	if err != nil {
		t.Fatalf("execution not created: %v", err)
	}
	if ex.Status != "SUCCESS" {
		t.Fatalf("status mismatch")
	}
	if ex.TaskID != "t1" {
		t.Fatalf("task id mismatch")
	}
}

func TestMarkFinishedCompletesRunningExecution(t *testing.T) {
	st := newTestStore(t)
	em := newTestExecMgr(t, st)
	ctx := context.Background()
	start := time.Now().Add(-time.Second).UTC().Truncate(time.Millisecond)
	ex := &models.Execution{ID: "running-1", TaskID: "t1", NodeID: "n1", Status: models.ExecStatusRunning, TriggerType: models.TriggerSchedule, StartTime: start}
	if err := st.CreateExecution(ctx, ex); err != nil {
		t.Fatal(err)
	}
	if err := em.MarkFinished(ctx, "n1", protocol.ExecutionFinishedPayload{
		ExecutionID: "running-1", TaskID: "t1", NodeID: "n1", TriggerType: models.TriggerSchedule,
		Status: models.ExecStatusSuccess, StartTime: start.Format(time.RFC3339Nano), EndTime: time.Now().UTC().Format(time.RFC3339Nano),
	}); err != nil {
		t.Fatal(err)
	}
	got, err := st.GetExecution(ctx, "running-1")
	if err != nil || got.Status != models.ExecStatusSuccess || !got.Synced {
		t.Fatalf("running execution was not completed: got=%+v err=%v", got, err)
	}
}

func TestExecutionEnvironmentMergesScriptAndParameters(t *testing.T) {
	task := &models.Task{
		Parameters:  []models.Parameter{{Name: "name", Required: true}, {Name: "count", Default: "2"}},
		ParamValues: map[string]string{"name": "task"},
	}
	script := &models.Script{
		Environment: map[string]string{"BASE": "yes"},
		Parameters:  []models.Parameter{{Name: "token", Type: "secret", Required: true}},
	}
	env, secrets, err := executionEnvironment(task, script, map[string]string{"name": "manual", "token": "secret"})
	if err != nil {
		t.Fatal(err)
	}
	if env["BASE"] != "yes" || env["NODESTEER_PARAM_name"] != "manual" || env["NODESTEER_PARAM_count"] != "2" {
		t.Fatalf("unexpected environment: %+v", env)
	}
	if len(secrets) != 1 || secrets[0] != "secret" {
		t.Fatalf("unexpected secret values: %+v", secrets)
	}
}

func TestManualRunRequiresOnline(t *testing.T) {
	st := newTestStore(t)
	em := newTestExecMgr(t, st)
	ctx := context.Background()

	// 注册离线节点
	n := &models.Node{ID: "n-offline", AgentID: "a1", Status: "offline", Capabilities: map[string]bool{"script": true}}
	if err := st.UpsertNode(ctx, n); err != nil {
		t.Fatalf("upsert node: %v", err)
	}
	task := &models.Task{ID: "t1", Name: "t", Type: "command", Command: "echo", Enabled: true, Target: models.Target{Type: "node"}}
	_, err := em.RunManual(ctx, task, []string{"n-offline"}, nil)
	if err == nil {
		t.Fatalf("expected error for offline node")
	}
	if !errors.Is(err, ErrAgentOffline) && !strings.Contains(err.Error(), "not online") {
		t.Fatalf("unexpected error: %v", err)
	}
}
