package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

func newTestAgentStore(t *testing.T) *LocalStore {
	t.Helper()
	dir := t.TempDir()
	s, err := OpenLocalStore(dir)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func TestIdentityPersistence(t *testing.T) {
	s := newTestAgentStore(t)
	if err := s.SetIdentity("node_id", "node-1"); err != nil {
		t.Fatalf("set: %v", err)
	}
	if err := s.SetIdentity("agent_id", "agent-1"); err != nil {
		t.Fatalf("set: %v", err)
	}
	v, _ := s.GetIdentity("node_id")
	if v != "node-1" {
		t.Fatalf("node id mismatch: %s", v)
	}
	v, _ = s.GetIdentity("agent_id")
	if v != "agent-1" {
		t.Fatalf("agent id mismatch: %s", v)
	}
	// 不存在返回空
	v, _ = s.GetIdentity("nonexistent")
	if v != "" {
		t.Fatalf("expected empty, got %s", v)
	}
}

func TestGlobalRevision(t *testing.T) {
	s := newTestAgentStore(t)
	ctx := context.Background()
	rev, err := s.GetGlobalRevision()
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if rev != 0 {
		t.Fatalf("expected 0, got %d", rev)
	}
	if err := s.BeginSync(ctx); err != nil {
		t.Fatalf("begin: %v", err)
	}
	if err := s.SetGlobalRevision(42); err != nil {
		s.RollbackSync()
		t.Fatalf("set: %v", err)
	}
	if err := s.CommitSync(); err != nil {
		t.Fatalf("commit: %v", err)
	}
	rev, _ = s.GetGlobalRevision()
	if rev != 42 {
		t.Fatalf("expected 42, got %d", rev)
	}
}

func TestScriptUpsertAndDelete(t *testing.T) {
	s := newTestAgentStore(t)
	ctx := context.Background()
	row := &ScriptRow{ID: "s1", Name: "test", Interpreter: "shell", Content: "echo", Enabled: true, Revision: 1}
	if err := s.BeginSync(ctx); err != nil {
		t.Fatalf("begin: %v", err)
	}
	if err := s.UpsertScript(ctx, row); err != nil {
		s.RollbackSync()
		t.Fatalf("upsert: %v", err)
	}
	if err := s.CommitSync(); err != nil {
		t.Fatalf("commit: %v", err)
	}
	got, err := s.GetScript(ctx, "s1")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if got.Content != "echo" || !got.Enabled {
		t.Fatalf("script mismatch")
	}
	// update
	row.Revision = 2
	row.Content = "echo 2"
	if err := s.BeginSync(ctx); err != nil {
		t.Fatalf("begin2: %v", err)
	}
	if err := s.UpsertScript(ctx, row); err != nil {
		s.RollbackSync()
		t.Fatalf("upsert2: %v", err)
	}
	if err := s.CommitSync(); err != nil {
		t.Fatalf("commit2: %v", err)
	}
	got, _ = s.GetScript(ctx, "s1")
	if got.Revision != 2 {
		t.Fatalf("revision not updated")
	}
	if err := s.BeginSync(ctx); err != nil {
		t.Fatalf("begin3: %v", err)
	}
	if err := s.DeleteScript(ctx, "s1"); err != nil {
		s.RollbackSync()
		t.Fatalf("delete: %v", err)
	}
	if err := s.CommitSync(); err != nil {
		t.Fatalf("commit3: %v", err)
	}
	if _, err := s.GetScript(ctx, "s1"); err == nil {
		t.Fatalf("expected error after delete")
	}
}

func TestExecutionSlotIdempotency(t *testing.T) {
	s := newTestAgentStore(t)
	ctx := context.Background()
	slot := time.Now().UTC().Truncate(time.Second).Format(time.RFC3339Nano)
	e1 := &LocalExecution{ID: "e1", TaskID: "t1", NodeID: "n1", TriggerType: "schedule", ScheduledTime: slot, Status: "RUNNING"}
	if err := s.CreateExecution(ctx, e1); err != nil {
		t.Fatalf("create: %v", err)
	}
	e2 := &LocalExecution{ID: "e2", TaskID: "t1", NodeID: "n1", TriggerType: "schedule", ScheduledTime: slot, Status: "RUNNING"}
	if err := s.CreateExecution(ctx, e2); err == nil {
		t.Fatalf("expected UNIQUE violation for same slot")
	}
}

func TestUnsyncedAndRunningExecutions(t *testing.T) {
	s := newTestAgentStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Second).Format(time.RFC3339Nano)
	e1 := &LocalExecution{ID: "e1", TaskID: "t1", NodeID: "n1", TriggerType: "schedule", ScheduledTime: base, Status: "RUNNING", Synced: false}
	s.CreateExecution(ctx, e1)
	e2 := &LocalExecution{ID: "e2", TaskID: "t1", NodeID: "n1", TriggerType: "schedule", ScheduledTime: base + "s", Status: "SUCCESS", Synced: false}
	s.CreateExecution(ctx, e2)
	e3 := &LocalExecution{ID: "e3", TaskID: "t1", NodeID: "n1", TriggerType: "schedule", ScheduledTime: base + "t", Status: "SUCCESS", Synced: true}
	s.CreateExecution(ctx, e3)

	unsynced, _ := s.ListUnsyncedExecutions(ctx)
	if len(unsynced) != 2 {
		t.Fatalf("expected 2 unsynced, got %d", len(unsynced))
	}
	running, _ := s.ListRunningExecutions(ctx)
	if len(running) != 1 {
		t.Fatalf("expected 1 running, got %d", len(running))
	}
}

func TestExecutionAckMarksSynced(t *testing.T) {
	s := newTestAgentStore(t)
	ex := &LocalExecution{ID: "ack-1", TaskID: "t1", NodeID: "n1", TriggerType: "manual", Status: "SUCCESS", Synced: false}
	if err := s.CreateExecution(context.Background(), ex); err != nil {
		t.Fatal(err)
	}
	a := &Agent{store: s, ctx: context.Background()}
	payload, _ := json.Marshal(protocol.ExecutionAckPayload{ExecutionID: ex.ID, OK: true})
	a.OnExecutionAck(protocol.Envelope{Payload: payload})
	got, err := s.GetExecution(context.Background(), ex.ID)
	if err != nil || got == nil || !got.Synced {
		t.Fatalf("execution ack did not mark synced: %+v err=%v", got, err)
	}
}

func TestRedactSecretOutput(t *testing.T) {
	got := redact("token=secret-value and secret-value", []string{"secret-value"})
	if got != "token=[REDACTED] and [REDACTED]" {
		t.Fatalf("unexpected redaction: %q", got)
	}
}

func TestDeploymentJournal(t *testing.T) {
	s := newTestAgentStore(t)
	ctx := context.Background()
	d := &DeploymentRow{ID: "d1", ApplicationID: "a1", ToVersion: "1.0", Phase: "PREPARING", StartedAt: time.Now().UTC().Format(time.RFC3339Nano), UpdatedAt: time.Now().UTC().Format(time.RFC3339Nano)}
	if err := s.CreateDeployment(ctx, d); err != nil {
		t.Fatalf("create: %v", err)
	}
	s.UpdateDeploymentPhase(ctx, "d1", "STARTED", "")
	active, _ := s.GetActiveDeployments(ctx)
	if len(active) != 1 {
		t.Fatalf("expected 1 active deployment, got %d", len(active))
	}
	if active[0].Phase != "STARTED" {
		t.Fatalf("phase mismatch: %s", active[0].Phase)
	}
}

func TestArtifactRegister(t *testing.T) {
	s := newTestAgentStore(t)
	if s.ArtifactExists("sha-1") {
		t.Fatalf("should not exist yet")
	}
	if err := s.RegisterArtifact("sha-1", "/var/lib/nodesteer/artifacts/sha-1"); err != nil {
		t.Fatalf("register: %v", err)
	}
	if !s.ArtifactExists("sha-1") {
		t.Fatalf("should exist after register")
	}
}

func TestPruneObjectsKeepsOnlySnapshot(t *testing.T) {
	s := newTestAgentStore(t)
	ctx := context.Background()
	if err := s.BeginSync(ctx); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"keep", "drop"} {
		if err := s.UpsertTask(ctx, id, []byte(`{"id":"`+id+`"}`), 1, true); err != nil {
			s.RollbackSync()
			t.Fatal(err)
		}
	}
	if err := s.PruneObjects(ctx, nil, []string{"keep"}, nil, nil); err != nil {
		s.RollbackSync()
		t.Fatal(err)
	}
	if err := s.CommitSync(); err != nil {
		t.Fatal(err)
	}
	if _, err := s.GetTask(ctx, "keep"); err != nil {
		t.Fatalf("snapshot object removed: %v", err)
	}
	if _, err := s.GetTask(ctx, "drop"); err == nil {
		t.Fatal("stale object should be pruned")
	}
}

// TestSyncTxConcurrentExecutionWrite 复现 Bug B：同步事务进行期间，
// scheduler goroutine 并发写 execution，不得报 transaction already committed。
func TestSyncTxConcurrentExecutionWrite(t *testing.T) {
	s := newTestAgentStore(t)
	ctx := context.Background()

	done := make(chan struct{})
	stop := make(chan struct{})

	// writer goroutine 模拟 scheduler：持续写 execution（普通连接池路径）
	go func() {
		defer close(done)
		n := 0
		for {
			select {
			case <-stop:
				return
			default:
			}
			e := &LocalExecution{
				ID:            fmt.Sprintf("exec-%d", n),
				TaskID:        "t1",
				NodeID:        "n1",
				TriggerType:   "schedule",
				ScheduledTime: time.Now().UTC().Format(time.RFC3339Nano),
				Status:        "RUNNING",
			}
			if err := s.CreateExecution(ctx, e); err != nil {
				t.Errorf("create execution: %v", err)
				return
			}
			n++
		}
	}()

	// 主 goroutine 模拟 applySync：反复 Begin → 写配置 → Commit
	for i := 0; i < 200; i++ {
		if err := s.BeginSync(ctx); err != nil {
			t.Fatalf("begin: %v", err)
		}
		row := &ScriptRow{ID: fmt.Sprintf("s%d", i), Name: "t", Content: "echo", Enabled: true, Revision: 1}
		if err := s.UpsertScript(ctx, row); err != nil {
			s.RollbackSync()
			t.Fatalf("upsert: %v", err)
		}
		if err := s.SetGlobalRevision(int64(i)); err != nil {
			s.RollbackSync()
			t.Fatalf("set rev: %v", err)
		}
		if err := s.CommitSync(); err != nil {
			t.Fatalf("commit: %v", err)
		}
	}
	close(stop)
	<-done
}

var _ = os.Getenv
var _ = filepath.Join
var _ = models.Inventory{}
