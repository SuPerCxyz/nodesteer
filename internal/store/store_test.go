package store

import (
	"context"
	"database/sql"
	"errors"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

func newTestStore(t *testing.T) *SQLiteStore {
	t.Helper()
	s, err := OpenInMemory()
	if err != nil {
		t.Fatalf("open in-memory store: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func TestMigrationApplied(t *testing.T) {
	s := newTestStore(t)
	cur, err := s.CurrentGlobalRevision(context.Background())
	if err != nil {
		t.Fatalf("current revision: %v", err)
	}
	if cur != 0 {
		t.Fatalf("expected 0, got %d", cur)
	}
}

func TestNextGlobalRevisionMonotonic(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	prev := int64(0)
	for i := 0; i < 10; i++ {
		r, err := s.NextGlobalRevision(ctx)
		if err != nil {
			t.Fatalf("next revision: %v", err)
		}
		if r <= prev {
			t.Fatalf("revision not monotonic: %d <= %d", r, prev)
		}
		prev = r
	}
}

func TestScriptCreateUpdateRevision(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	sc := &models.Script{Name: "test", Interpreter: "shell", Content: "echo hi", Revision: 1, SHA256: "abc"}
	if err := s.CreateScript(ctx, sc); err != nil {
		t.Fatalf("create: %v", err)
	}
	got, err := s.GetScript(ctx, sc.ID)
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if got.Revision != 1 {
		t.Fatalf("expected rev 1, got %d", got.Revision)
	}
	// update
	sc.Content = "echo bye"
	sc.Revision = 2
	sc.SHA256 = "def"
	if err := s.UpdateScript(ctx, sc, 1); err != nil {
		t.Fatalf("update: %v", err)
	}
	rev, err := s.GetScriptRevision(ctx, sc.ID, 1)
	if err != nil {
		t.Fatalf("get old revision: %v", err)
	}
	if rev.Content != "echo hi" {
		t.Fatalf("old revision content mismatch")
	}
}

func TestNodeUpsertAndLabels(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	n := &models.Node{
		ID: "node1", AgentID: "agent1", Hostname: "h1", Status: "online",
		Labels: map[string]string{"env": "prod"}, Capabilities: map[string]bool{"script": true},
	}
	if err := s.UpsertNode(ctx, n); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	got, err := s.GetNode(ctx, "node1")
	if err != nil {
		t.Fatalf("get: %v", err)
	}
	if got.Labels["env"] != "prod" {
		t.Fatalf("labels mismatch")
	}
	if !got.Capabilities["script"] {
		t.Fatalf("capabilities mismatch")
	}
}

func TestExecutionSlotIdempotency(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	e1 := &models.Execution{
		ID: "e1", TaskID: "task1", NodeID: "node1", TriggerType: "schedule",
		ScheduledTime: time.Now().UTC().Truncate(time.Second), Status: "PENDING",
	}
	if err := s.CreateExecution(ctx, e1); err != nil {
		t.Fatalf("create: %v", err)
	}
	slot := e1.ScheduledTime.Format(time.RFC3339Nano)
	found, err := s.FindExecutionBySlot(ctx, "task1", "node1", slot)
	if err != nil || found == nil {
		t.Fatalf("slot lookup failed: %v", err)
	}
	// 相同 slot 再创建应冲突
	e2 := &models.Execution{
		ID: "e2", TaskID: "task1", NodeID: "node1", TriggerType: "schedule",
		ScheduledTime: e1.ScheduledTime, Status: "PENDING",
	}
	if err := s.CreateExecution(ctx, e2); err == nil {
		t.Fatalf("expected UNIQUE violation for same slot")
	}
}

func TestChangeLogQuery(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	if err := s.AppendChangeLog(ctx, 1, "task", "t1", 1, "create"); err != nil {
		t.Fatalf("append: %v", err)
	}
	if err := s.AppendChangeLog(ctx, 2, "task", "t1", 2, "update"); err != nil {
		t.Fatalf("append: %v", err)
	}
	changes, err := s.GetChangesSince(ctx, 0)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(changes) != 2 {
		t.Fatalf("expected 2 changes, got %d", len(changes))
	}
	changes1, err := s.GetChangesSince(ctx, 1)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(changes1) != 1 {
		t.Fatalf("expected 1 change since 1, got %d", len(changes1))
	}
}

func TestUserPassword(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	u := &models.User{Username: "admin", PasswordHash: "hash", Role: "administrator"}
	if err := s.CreateUser(ctx, u); err != nil {
		t.Fatalf("create user: %v", err)
	}
	got, err := s.GetUserByUsername(ctx, "admin")
	if err != nil {
		t.Fatalf("get user: %v", err)
	}
	if got.Role != "administrator" {
		t.Fatalf("role mismatch")
	}
}

func TestGetUserByID(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	u := &models.User{Username: "viewer1", PasswordHash: "hash", Role: "viewer"}
	if err := s.CreateUser(ctx, u); err != nil {
		t.Fatalf("create user: %v", err)
	}
	got, err := s.GetUserByID(ctx, u.ID)
	if err != nil {
		t.Fatalf("get user by id: %v", err)
	}
	if got.ID != u.ID || got.Username != "viewer1" || got.Role != "viewer" {
		t.Fatalf("unexpected user: %+v", got)
	}
	if err := s.UpdateUserRole(ctx, u.ID, "operator"); err != nil {
		t.Fatalf("update role: %v", err)
	}
	got, err = s.GetUserByID(ctx, u.ID)
	if err != nil {
		t.Fatalf("get user by id after update: %v", err)
	}
	if got.Role != "operator" {
		t.Fatalf("role = %q, want operator", got.Role)
	}
	if _, err := s.GetUserByID(ctx, "missing-id"); err == nil {
		t.Fatal("expected error for missing user")
	}
}

// users.avatar_url 迁移对既有行兼容：迁移前写入的用户读出空 avatar，且新列可读写。
func TestUserAvatarMigrationBackfillsEmpty(t *testing.T) {
	db, err := sql.Open("sqlite", ":memory:")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	t.Cleanup(func() { db.Close() })

	// 模拟 v1（无 avatar_url 列）时代的既有库与既有用户行
	if _, err := db.Exec(`CREATE TABLE users (
		id TEXT PRIMARY KEY,
		username TEXT UNIQUE NOT NULL,
		password_hash TEXT NOT NULL,
		role TEXT NOT NULL DEFAULT 'viewer',
		created_at TEXT NOT NULL
	)`); err != nil {
		t.Fatalf("create legacy users table: %v", err)
	}
	if _, err := db.Exec(`CREATE TABLE schema_migrations (
		version INTEGER PRIMARY KEY,
		name TEXT NOT NULL,
		applied_at TEXT NOT NULL DEFAULT (datetime('now'))
	)`); err != nil {
		t.Fatalf("create schema_migrations: %v", err)
	}
	for v := 1; v <= 8; v++ {
		if _, err := db.Exec(`INSERT INTO schema_migrations (version, name) VALUES (?, 'legacy')`, v); err != nil {
			t.Fatalf("mark migration %d applied: %v", v, err)
		}
	}
	if _, err := db.Exec(`INSERT INTO users (id, username, password_hash, role, created_at)
		VALUES ('u1', 'legacy-user', 'hash', 'viewer', '2024-01-01T00:00:00Z')`); err != nil {
		t.Fatalf("insert legacy user: %v", err)
	}

	if err := Migrate(db); err != nil {
		t.Fatalf("migrate: %v", err)
	}

	s := &SQLiteStore{db: db}
	u, err := s.GetUserByUsername(context.Background(), "legacy-user")
	if err != nil {
		t.Fatalf("read legacy user after migration: %v", err)
	}
	if u.AvatarURL != "" {
		t.Fatalf("legacy user avatar = %q, want empty", u.AvatarURL)
	}

	// 迁移后可正常更新头像
	if err := s.UpdateUserAvatar(context.Background(), u.ID, "https://idp.example/a.png"); err != nil {
		t.Fatalf("update avatar: %v", err)
	}
	u, err = s.GetUserByID(context.Background(), u.ID)
	if err != nil {
		t.Fatalf("re-read user: %v", err)
	}
	if u.AvatarURL != "https://idp.example/a.png" {
		t.Fatalf("avatar = %q, want updated value", u.AvatarURL)
	}
}

// CreateUser/GetUserByID/ListUsers 全覆盖 avatar_url 读写。
func TestUserAvatarRoundTrip(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	u := &models.User{Username: "av", PasswordHash: "hash", Role: "administrator", AvatarURL: "https://idp.example/x.png"}
	if err := s.CreateUser(ctx, u); err != nil {
		t.Fatalf("create user: %v", err)
	}
	got, err := s.GetUserByID(ctx, u.ID)
	if err != nil {
		t.Fatalf("get user by id: %v", err)
	}
	if got.AvatarURL != u.AvatarURL {
		t.Fatalf("avatar by id = %q, want %q", got.AvatarURL, u.AvatarURL)
	}
	list, err := s.ListUsers(ctx)
	if err != nil {
		t.Fatalf("list users: %v", err)
	}
	if len(list) != 1 || list[0].AvatarURL != u.AvatarURL {
		t.Fatalf("list users avatar mismatch: %+v", list)
	}
}

func TestApplicationNodeState(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	state := &models.ApplicationNodeState{
		ApplicationID: "app-1", NodeID: "node-1", Version: "1.0.0",
		Operation: "deploy", Health: "healthy",
	}
	if err := s.SetApplicationNodeState(ctx, state); err != nil {
		t.Fatal(err)
	}
	state.Health = "unhealthy"
	state.Error = "health check failed"
	if err := s.SetApplicationNodeState(ctx, state); err != nil {
		t.Fatal(err)
	}
	got, err := s.ListApplicationNodeStates(ctx, "app-1")
	if err != nil || len(got) != 1 || got[0].Health != "unhealthy" {
		t.Fatalf("unexpected application state: %+v err=%v", got, err)
	}
}

func TestWithTxRollsBackDesiredStateAndRevision(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	errExpected := errors.New("abort")
	err := s.WithTx(ctx, func(txctx context.Context) error {
		script := &models.Script{ID: "tx-script", Name: "tx", Content: "echo", Revision: 1, SHA256: "sha"}
		if err := s.CreateScript(txctx, script); err != nil {
			return err
		}
		if _, err := s.NextGlobalRevision(txctx); err != nil {
			return err
		}
		return errExpected
	})
	if !errors.Is(err, errExpected) {
		t.Fatalf("expected transaction error, got %v", err)
	}
	if _, err := s.GetScript(ctx, "tx-script"); err == nil {
		t.Fatal("rolled-back script is still present")
	}
	if rev, err := s.CurrentGlobalRevision(ctx); err != nil || rev != 0 {
		t.Fatalf("rolled-back revision changed: rev=%d err=%v", rev, err)
	}
}

func TestCountExecutionsFilter(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	mk := func(id, taskID, nodeID, status string) {
		t.Helper()
		if err := s.CreateExecution(ctx, &models.Execution{
			ID: id, TaskID: taskID, NodeID: nodeID,
			TriggerType: models.TriggerManual, Status: status,
		}); err != nil {
			t.Fatalf("create execution %s: %v", id, err)
		}
	}
	// 唯一索引为 (task_id, node_id, scheduled_time)，用不同 node 区分同任务记录。
	mk("e1", "task-a", "node-1", models.ExecStatusSuccess)
	mk("e2", "task-a", "node-2", models.ExecStatusFailed)
	mk("e3", "task-b", "node-1", models.ExecStatusSuccess)
	mk("e4", "task-b", "node-2", models.ExecStatusSuccess)

	tests := []struct {
		name   string
		filter ExecutionFilter
		want   int64
	}{
		{name: "all", filter: ExecutionFilter{}, want: 4},
		{name: "ignores paging", filter: ExecutionFilter{Limit: 1, Offset: 2}, want: 4},
		{name: "by task", filter: ExecutionFilter{TaskID: "task-a"}, want: 2},
		{name: "by node", filter: ExecutionFilter{NodeID: "node-1"}, want: 2},
		{name: "by status", filter: ExecutionFilter{Status: models.ExecStatusSuccess}, want: 3},
		{name: "combined", filter: ExecutionFilter{TaskID: "task-b", Status: models.ExecStatusSuccess}, want: 2},
		{name: "no match", filter: ExecutionFilter{TaskID: "task-zzz"}, want: 0},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := s.CountExecutions(ctx, tc.filter)
			if err != nil {
				t.Fatalf("count executions: %v", err)
			}
			if got != tc.want {
				t.Fatalf("count = %d, want %d", got, tc.want)
			}
		})
	}
}

func TestCountAuditFilter(t *testing.T) {
	s := newTestStore(t)
	ctx := context.Background()
	mk := func(id, userID, action string) {
		t.Helper()
		if err := s.AddAudit(ctx, &models.AuditLog{
			ID: id, UserID: userID, Username: userID, Action: action, Resource: "test",
		}); err != nil {
			t.Fatalf("add audit %s: %v", id, err)
		}
	}
	mk("a1", "u1", "login")
	mk("a2", "u1", "update")
	mk("a3", "u2", "login")
	mk("a4", "u2", "delete")

	tests := []struct {
		name   string
		filter AuditFilter
		want   int64
	}{
		{name: "all", filter: AuditFilter{}, want: 4},
		{name: "ignores paging", filter: AuditFilter{Limit: 1, Offset: 1}, want: 4},
		{name: "by user", filter: AuditFilter{UserID: "u1"}, want: 2},
		{name: "by action", filter: AuditFilter{Action: "login"}, want: 2},
		{name: "combined", filter: AuditFilter{UserID: "u2", Action: "delete"}, want: 1},
		{name: "no match", filter: AuditFilter{Action: "zzz"}, want: 0},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			got, err := s.CountAudit(ctx, tc.filter)
			if err != nil {
				t.Fatalf("count audit: %v", err)
			}
			if got != tc.want {
				t.Fatalf("count = %d, want %d", got, tc.want)
			}
		})
	}
}
