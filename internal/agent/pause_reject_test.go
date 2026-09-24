package agent

import (
	"io"
	"log/slog"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// newPauseTestAgent 构造 ready 态测试 Agent（未连接真实 Hub）。
func newPauseTestAgent(t *testing.T) *Agent {
	t.Helper()
	a, err := New(Config{
		HubURL:            "ws://127.0.0.1:8443",
		RegistrationToken: "test-token",
		NodeName:          "pause-test-node",
		DataDir:           t.TempDir(),
		DeploymentMode:    models.DeploymentModeNative,
		AgentVersion:      "v0.0.0-test",
	}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if err != nil {
		t.Fatalf("new agent: %v", err)
	}
	t.Cleanup(func() { _ = a.Close() })
	a.ready = true
	return a
}

// TestRunExecutionRejectedWhenPaused T7：paused 收到 RUN_EXECUTION → 拒收并回报
// （journal 终态 BLOCKED + 原因 node paused），不启动本地执行。
func TestRunExecutionRejectedWhenPaused(t *testing.T) {
	a := newPauseTestAgent(t)
	a.sch.SetPaused(true)

	const execID = "exec-paused-run"
	a.OnRunExecution(protocol.NewEnvelope(protocol.MsgRunExecution, execID, protocol.RunExecutionPayload{
		ExecutionID: execID,
		TaskID:      "task-paused",
		Type:        models.TaskTypeCommand,
		Command:     "echo should-not-run",
		TriggerType: models.TriggerManual,
	}))

	ex, err := a.store.GetExecution(a.ctx, execID)
	if err != nil {
		t.Fatalf("journal must exist for rejection report: %v", err)
	}
	if ex.Status != models.ExecStatusBlocked {
		t.Fatalf("status = %q, want BLOCKED (rejected)", ex.Status)
	}
	if ex.BlockReason != "node paused" {
		t.Fatalf("reason = %q, want %q", ex.BlockReason, "node paused")
	}
}

// TestDeployRequestRejectedWhenPaused T7：paused 收到 DEPLOY_REQUEST → 拒收并回报
// （journal 终态 FAILED + 原因 node paused），不启动本地部署。
func TestDeployRequestRejectedWhenPaused(t *testing.T) {
	a := newPauseTestAgent(t)
	a.sch.SetPaused(true)

	const execID = "exec-paused-deploy"
	a.OnDeployRequest(protocol.NewEnvelope(protocol.MsgDeployRequest, execID, protocol.DeployRequestPayload{
		AppID:     "app-paused",
		Operation: "deploy",
	}))

	ex, err := a.store.GetExecution(a.ctx, execID)
	if err != nil {
		t.Fatalf("journal must exist for rejection report: %v", err)
	}
	if ex.Status != models.ExecStatusFailed {
		t.Fatalf("status = %q, want FAILED (rejected)", ex.Status)
	}
	if ex.BlockReason != "node paused" {
		t.Fatalf("reason = %q, want %q", ex.BlockReason, "node paused")
	}
	if ex.ApplicationID != "app-paused" {
		t.Fatalf("app id = %q, want app-paused", ex.ApplicationID)
	}
}

// TestRunExecutionProceedsWhenNotPaused 回归：未暂停时执行指令正常启动并完成。
func TestRunExecutionProceedsWhenNotPaused(t *testing.T) {
	a := newPauseTestAgent(t)
	if a.sch.IsPaused() {
		t.Fatal("fresh agent must not be paused")
	}

	const execID = "exec-unpaused-run"
	a.OnRunExecution(protocol.NewEnvelope(protocol.MsgRunExecution, execID, protocol.RunExecutionPayload{
		ExecutionID: execID,
		TaskID:      "task-unpaused",
		Type:        models.TaskTypeCommand,
		Command:     "echo unpaused-ok",
		TriggerType: models.TriggerManual,
	}))

	// 拒收是同步路径：调用返回时状态不得为 BLOCKED
	if ex, err := a.store.GetExecution(a.ctx, execID); err == nil && ex.Status == models.ExecStatusBlocked {
		t.Fatalf("unpaused execution must not be rejected, reason=%q", ex.BlockReason)
	}
	// 正常执行应完成
	deadline := time.Now().Add(5 * time.Second)
	for {
		ex, err := a.store.GetExecution(a.ctx, execID)
		if err != nil {
			t.Fatalf("get execution: %v", err)
		}
		if ex.Status == models.ExecStatusSuccess {
			break
		}
		if ex.Status == models.ExecStatusFailed || ex.Status == models.ExecStatusBlocked || ex.Status == models.ExecStatusTimedOut {
			t.Fatalf("unpaused execution ended badly: status=%q reason=%q", ex.Status, ex.BlockReason)
		}
		if time.Now().After(deadline) {
			t.Fatalf("execution did not finish in time, status=%q", ex.Status)
		}
		time.Sleep(20 * time.Millisecond)
	}
}
