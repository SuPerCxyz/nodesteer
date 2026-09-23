package hubserver

import (
	"context"
	"log/slog"
	"os"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestAuthPermissions(t *testing.T) {
	st := mustStore(t)
	ctx := context.Background()
	am := auth.New(st, time.Hour)
	if err := am.EnsureDefaultAdmin(ctx, "admin", "pass"); err != nil {
		t.Fatalf("ensure admin: %v", err)
	}
	sess, err := am.Login(ctx, "admin", "pass")
	if err != nil {
		t.Fatalf("login: %v", err)
	}
	if sess.Role != "administrator" {
		t.Fatalf("role mismatch")
	}
	if _, err := am.Login(ctx, "admin", "wrong"); err == nil {
		t.Fatalf("expected login failure")
	}
	if !auth.HasPermission("administrator", "delete") {
		t.Fatalf("admin should have all permissions")
	}
	if auth.HasPermission("viewer", "delete") {
		t.Fatalf("viewer should not delete")
	}
	if !auth.HasPermission("operator", "run") {
		t.Fatalf("operator should run")
	}
}

func TestTLSConfigRequiresCertificatePair(t *testing.T) {
	_, err := New(Config{DataDir: t.TempDir(), ArtifactDir: t.TempDir(), WebTLSCert: "cert.pem"}, slog.Default())
	if err == nil {
		t.Fatal("expected incomplete web TLS configuration to fail")
	}
	_, err = New(Config{DataDir: t.TempDir(), ArtifactDir: t.TempDir(), GatewayTLSKey: "key.pem"}, slog.Default())
	if err == nil {
		t.Fatal("expected incomplete gateway TLS configuration to fail")
	}
}

func TestSyncManagerComputeDesiredState(t *testing.T) {
	st := mustStore(t)
	ctx := context.Background()
	rm := hub.NewRevisionManager(st)
	sessions := hub.NewSessionManager()
	nm := hub.NewNodeManager(st, rm)
	sm := hub.NewSyncManager(st, rm, sessions, nm)

	n := &models.Node{ID: "node1", Labels: map[string]string{"env": "prod"}}
	if err := st.UpsertNode(ctx, n); err != nil {
		t.Fatalf("upsert node: %v", err)
	}

	sc := &models.Script{Name: "s1", Interpreter: "shell", Content: "echo", Enabled: true}
	if err := st.CreateScript(ctx, sc); err != nil {
		t.Fatalf("create script: %v", err)
	}
	task := &models.Task{
		Name: "t1", Type: "script", ScriptID: sc.ID, Enabled: true,
		Target: models.Target{Type: "label", LabelKey: "env", LabelValue: "prod"},
	}
	if err := st.CreateTask(ctx, task); err != nil {
		t.Fatalf("create task: %v", err)
	}

	objs, err := sm.ComputeDesiredState(ctx, "node1")
	if err != nil {
		t.Fatalf("compute: %v", err)
	}
	if len(objs.Tasks) != 1 {
		t.Fatalf("expected 1 task, got %d", len(objs.Tasks))
	}
	if len(objs.Scripts) != 1 {
		t.Fatalf("expected 1 script, got %d", len(objs.Scripts))
	}
}

func TestChangeLogWindowFullResync(t *testing.T) {
	st := mustStore(t)
	ctx := context.Background()
	rm := hub.NewRevisionManager(st)
	sessions := hub.NewSessionManager()
	nm := hub.NewNodeManager(st, rm)
	sm := hub.NewSyncManager(st, rm, sessions, nm)
	sm.SetChangelogWindow(100)

	n := &models.Node{ID: "node1", Labels: map[string]string{"env": "prod"}}
	if err := st.UpsertNode(ctx, n); err != nil {
		t.Fatalf("upsert node: %v", err)
	}

	for i := 0; i < 200; i++ {
		_, _ = rm.Next(ctx)
	}
	resp, err := sm.BuildSyncResponse(ctx, "node1", 5)
	if err != nil {
		t.Fatalf("build sync response: %v", err)
	}
	if !resp.FullResync {
		t.Fatalf("expected full resync when since is far behind")
	}
}

func mustStore(t *testing.T) store.Store {
	t.Helper()
	s, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { s.Close() })
	return s
}

func newTestHub(t *testing.T) {
	t.Helper()
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	h, err := New(Config{
		WebAddr:           "127.0.0.1:0",
		GatewayAddr:       "127.0.0.1:0",
		RegistrationToken: "test-token",
		DataDir:           dir,
		ArtifactDir:       dir + "/artifacts",
		BaseURL:           "http://127.0.0.1:18080",
		HeartbeatTimeout:  30 * time.Second,
		AdminUsername:     "admin",
		AdminPassword:     "admin123",
		SessionTTL:        time.Hour,
		RevisionCheckSec:  5,
		ChangelogWindow:   5000,
	}, logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })
}
