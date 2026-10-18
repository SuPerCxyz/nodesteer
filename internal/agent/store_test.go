package agent

import (
	"context"
	"database/sql"
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

// TestSyncTxCommitFailureRollbackIsNoop 复现 Bug：Commit 失败后（事务已结束、写锁已释放），
// defer 的 RollbackSync 必须是 no-op；旧实现在 tx==nil 分支重复 Unlock 触发
// fatal panic: sync: unlock of unlocked mutex。
func TestSyncTxCommitFailureRollbackIsNoop(t *testing.T) {
	s := newTestAgentStore(t)
	ctx := context.Background()
	if err := s.BeginSync(ctx); err != nil {
		t.Fatal(err)
	}
	// 让底层事务先结束：等价于 Commit 返回错误后的状态（database/sql 语义：
	// Commit 失败后该 Tx 已结束，不能再 Rollback）。
	if err := s.tx.Rollback(); err != nil {
		t.Fatal(err)
	}
	if err := s.CommitSync(); err == nil {
		t.Fatal("expected commit failure on ended transaction")
	}
	if err := s.RollbackSync(); err != nil {
		t.Fatalf("rollback after failed commit must be a no-op, got %v", err)
	}
	if s.InSyncTx() {
		t.Fatal("sync tx must be released after failed commit")
	}
	// 失败后必须还能重新同步（写锁已正确释放一次）。
	if err := s.BeginSync(ctx); err != nil {
		t.Fatal(err)
	}
	if err := s.SetGlobalRevision(3); err != nil {
		s.RollbackSync()
		t.Fatal(err)
	}
	if err := s.CommitSync(); err != nil {
		t.Fatal(err)
	}
	if rev, err := s.GetGlobalRevision(); err != nil || rev != 3 {
		t.Fatalf("revision after retry = %d err=%v, want 3", rev, err)
	}
}

// TestSyncTxCallsWithoutBeginAreNoops 无 BeginSync 的 Commit/Rollback 必须
// 返回 nil 且不释放未持有的写锁（旧实现会 Unlock 未持有的 mutex → panic）。
func TestSyncTxCallsWithoutBeginAreNoops(t *testing.T) {
	s := newTestAgentStore(t)
	if s.InSyncTx() {
		t.Fatal("new store must not hold sync tx")
	}
	if err := s.CommitSync(); err != nil {
		t.Fatalf("commit without begin = %v, want nil", err)
	}
	if err := s.RollbackSync(); err != nil {
		t.Fatalf("rollback without begin = %v, want nil", err)
	}
	// 连续调用幂等且不影响后续真实事务。
	if err := s.CommitSync(); err != nil {
		t.Fatal(err)
	}
	if err := s.RollbackSync(); err != nil {
		t.Fatal(err)
	}
	ctx := context.Background()
	if err := s.BeginSync(ctx); err != nil {
		t.Fatal(err)
	}
	if err := s.CommitSync(); err != nil {
		t.Fatal(err)
	}
	if s.InSyncTx() {
		t.Fatal("tx must be released")
	}
}

// TestDeploymentBindingRoundTrip 部署上下文绑定（app revision + 三路径）可持久化并通过
// 活跃/按应用查询读回；旧行缺省值为空/0（读取不报错，恢复侧按未知处理）。
func TestDeploymentBindingRoundTrip(t *testing.T) {
	s := newTestAgentStore(t)
	ctx := context.Background()
	now := time.Now().UTC().Format(time.RFC3339Nano)
	legacy := &DeploymentRow{ID: "legacy", ApplicationID: "app", Phase: PhasePreparing, StartedAt: now, UpdatedAt: now}
	bound := &DeploymentRow{
		ID: "bound", ApplicationID: "app", Phase: PhaseVerifying,
		AppRevision: 7, BinaryPath: "/opt/app/bin", ConfigPath: "/opt/app/conf", UnitName: "app.service",
		StartedAt: now, UpdatedAt: now,
	}
	for _, d := range []*DeploymentRow{legacy, bound} {
		if err := s.CreateDeployment(ctx, d); err != nil {
			t.Fatalf("create %s: %v", d.ID, err)
		}
	}
	if err := s.SetDeploymentBinding(ctx, bound.ID, 8, "/opt/app/bin2", "/opt/app/conf2", "app2.service"); err != nil {
		t.Fatal(err)
	}
	active, err := s.GetActiveDeployments(ctx)
	if err != nil || len(active) != 2 {
		t.Fatalf("active = %v err=%v", active, err)
	}
	byID := map[string]*DeploymentRow{}
	for _, d := range active {
		byID[d.ID] = d
	}
	if got := byID["bound"]; got.AppRevision != 8 || got.BinaryPath != "/opt/app/bin2" || got.ConfigPath != "/opt/app/conf2" || got.UnitName != "app2.service" {
		t.Fatalf("binding not persisted: %+v", got)
	}
	if got := byID["legacy"]; got.AppRevision != 0 || got.BinaryPath != "" || got.ConfigPath != "" || got.UnitName != "" {
		t.Fatalf("legacy row must decode empty binding: %+v", got)
	}
	history, err := s.ListDeploymentsByApplication(ctx, "app")
	if err != nil || len(history) != 2 {
		t.Fatalf("history = %v err=%v", history, err)
	}
}

// TestDeploymentCompletedKindRoundTrip 终态成因列可持久化并通过按应用查询读回；
// 旧行（未写成因）读为空值（未知），CompleteDeployment 一次写入 DONE 与成因。
func TestDeploymentCompletedKindRoundTrip(t *testing.T) {
	s := newTestAgentStore(t)
	ctx := context.Background()
	now := time.Now().UTC().Format(time.RFC3339Nano)
	legacy := &DeploymentRow{ID: "legacy", ApplicationID: "app", Phase: PhaseDone, StartedAt: now, UpdatedAt: now}
	deployed := &DeploymentRow{ID: "deployed", ApplicationID: "app", Phase: PhaseDone, CompletedKind: CompletedKindDeployed, StartedAt: now, UpdatedAt: now}
	halfDone := &DeploymentRow{ID: "half-done", ApplicationID: "app", Phase: PhaseVerifying, StartedAt: now, UpdatedAt: now}
	for _, d := range []*DeploymentRow{legacy, deployed, halfDone} {
		if err := s.CreateDeployment(ctx, d); err != nil {
			t.Fatalf("create %s: %v", d.ID, err)
		}
	}
	if err := s.CompleteDeployment(ctx, halfDone.ID, CompletedKindRolledBack, "backup"); err != nil {
		t.Fatal(err)
	}
	history, err := s.ListDeploymentsByApplication(ctx, "app")
	if err != nil || len(history) != 3 {
		t.Fatalf("history = %v err=%v", history, err)
	}
	byID := map[string]*DeploymentRow{}
	for _, d := range history {
		byID[d.ID] = d
	}
	if got := byID["legacy"]; got.CompletedKind != "" || got.Phase != PhaseDone {
		t.Fatalf("legacy row must decode empty completed kind: %+v", got)
	}
	if got := byID["deployed"]; got.CompletedKind != CompletedKindDeployed {
		t.Fatalf("completed kind not persisted: %+v", got)
	}
	if got := byID["half-done"]; got.Phase != PhaseDone || got.CompletedKind != CompletedKindRolledBack || got.BackupPath != "backup" {
		t.Fatalf("CompleteDeployment must write DONE + kind together: %+v", got)
	}
	if active, err := s.GetActiveDeployments(ctx); err != nil || len(active) != 0 {
		t.Fatalf("completed rows must leave the active set: %+v err=%v", active, err)
	}
	if all, err := s.ListAllDeployments(ctx); err != nil || len(all) != 3 {
		t.Fatalf("ListAllDeployments = %d err=%v, want 3", len(all), err)
	}
}

// TestDeploymentCompletedKindMigration 旧库（deployments 无 completed_kind 列）打开时
// 幂等加列：旧行原样保留、该列读为空（未知）；再次打开不报错（非破坏性迁移）。
func TestDeploymentCompletedKindMigration(t *testing.T) {
	dir := t.TempDir()
	raw, err := sql.Open("sqlite", filepath.Join(dir, "state.db"))
	if err != nil {
		t.Fatal(err)
	}
	if _, err := raw.Exec(`
		CREATE TABLE deployments (
			id TEXT PRIMARY KEY,
			application_id TEXT NOT NULL,
			from_version TEXT NOT NULL DEFAULT '',
			to_version TEXT NOT NULL DEFAULT '',
			phase TEXT NOT NULL,
			backup_path TEXT NOT NULL DEFAULT '',
			backup_config_path TEXT NOT NULL DEFAULT '',
			backup_unit_path TEXT NOT NULL DEFAULT '',
			app_revision INTEGER NOT NULL DEFAULT 0,
			binary_path TEXT NOT NULL DEFAULT '',
			config_path TEXT NOT NULL DEFAULT '',
			unit_name TEXT NOT NULL DEFAULT '',
			started_at TEXT NOT NULL,
			updated_at TEXT NOT NULL
		);
		INSERT INTO deployments (id, application_id, phase, app_revision, binary_path, started_at, updated_at)
		VALUES ('legacy-done', 'app', 'DONE', 7, '/opt/app/bin', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z');`); err != nil {
		t.Fatal(err)
	}
	if err := raw.Close(); err != nil {
		t.Fatal(err)
	}
	s, err := OpenLocalStore(dir)
	if err != nil {
		t.Fatalf("open legacy store: %v", err)
	}
	rows, err := s.ListDeploymentsByApplication(context.Background(), "app")
	if err != nil || len(rows) != 1 {
		t.Fatalf("legacy rows = %d err=%v, want 1", len(rows), err)
	}
	if got := rows[0]; got.ID != "legacy-done" || got.Phase != PhaseDone || got.CompletedKind != "" || got.AppRevision != 7 || got.BinaryPath != "/opt/app/bin" {
		t.Fatalf("legacy row changed by migration: %+v", got)
	}
	if err := s.Close(); err != nil {
		t.Fatal(err)
	}
	// 再次打开：迁移幂等，旧行与空成因保持不变
	s2, err := OpenLocalStore(dir)
	if err != nil {
		t.Fatalf("reopen migrated store: %v", err)
	}
	defer s2.Close()
	rows, err = s2.ListDeploymentsByApplication(context.Background(), "app")
	if err != nil || len(rows) != 1 || rows[0].CompletedKind != "" {
		t.Fatalf("legacy row lost after reopen: %+v err=%v", rows, err)
	}
}
