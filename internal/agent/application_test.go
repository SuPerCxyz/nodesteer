package agent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/agent/host"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

type testHost struct {
	root   string
	active bool
}

type transientStatusHost struct {
	testHost
	calls int
}

func (h *transientStatusHost) ServiceStatus(context.Context, string) (string, error) {
	h.calls++
	if h.calls == 1 {
		return "active", nil
	}
	return "activating", nil
}

func (h *testHost) HostRoot() string        { return h.root }
func (h *testHost) MapPath(p string) string { return filepath.Join(h.root, strings.TrimPrefix(p, "/")) }
func (h *testHost) ValidatePath(p string) error {
	if !filepath.IsAbs(p) {
		return errors.New("absolute path required")
	}
	return nil
}
func (h *testHost) WriteFile(_ context.Context, p string, data []byte, mode os.FileMode) error {
	if err := os.MkdirAll(filepath.Dir(h.MapPath(p)), 0o755); err != nil {
		return err
	}
	return os.WriteFile(h.MapPath(p), data, mode)
}
func (h *testHost) AtomicReplace(ctx context.Context, p string, data []byte, mode os.FileMode) error {
	return h.WriteFile(ctx, p, data, mode)
}
func (h *testHost) OpenRead(_ context.Context, p string) (io.ReadCloser, os.FileInfo, error) {
	f, err := os.Open(h.MapPath(p))
	if err != nil {
		return nil, nil, err
	}
	info, err := f.Stat()
	if err != nil {
		f.Close()
		return nil, nil, err
	}
	return f, info, nil
}
func (h *testHost) AtomicReplaceReader(ctx context.Context, p string, r io.Reader, mode os.FileMode) error {
	data, err := io.ReadAll(r)
	if err != nil {
		return err
	}
	return h.AtomicReplace(ctx, p, data, mode)
}
func (h *testHost) Chmod(_ context.Context, p string, mode os.FileMode) error {
	return os.Chmod(h.MapPath(p), mode)
}
func (h *testHost) Chown(context.Context, string, string, string) error { return nil }
func (h *testHost) MkdirAll(_ context.Context, p string, mode os.FileMode) error {
	return os.MkdirAll(h.MapPath(p), mode)
}
func (h *testHost) Remove(_ context.Context, p string) error { return os.Remove(h.MapPath(p)) }
func (h *testHost) ReadFile(_ context.Context, p string) ([]byte, error) {
	return os.ReadFile(h.MapPath(p))
}
func (h *testHost) Stat(_ context.Context, p string) (os.FileInfo, error) {
	return os.Stat(h.MapPath(p))
}
func (h *testHost) InstallUnit(ctx context.Context, unit, content string) error {
	return h.WriteFile(ctx, "/etc/systemd/system/"+unit, []byte(content), 0o644)
}
func (h *testHost) UpdateUnit(ctx context.Context, unit, content string) error {
	return h.InstallUnit(ctx, unit, content)
}
func (h *testHost) DaemonReload(context.Context) error           { return nil }
func (h *testHost) EnableService(context.Context, string) error  { return nil }
func (h *testHost) DisableService(context.Context, string) error { h.active = false; return nil }
func (h *testHost) StartService(context.Context, string) error   { h.active = true; return nil }
func (h *testHost) StopService(context.Context, string) error    { h.active = false; return nil }
func (h *testHost) RestartService(context.Context, string) error { h.active = true; return nil }
func (h *testHost) ServiceStatus(context.Context, string) (string, error) {
	if h.active {
		return "active", nil
	}
	return "inactive", errors.New("inactive")
}
func (h *testHost) RunCommand(_ context.Context, _ string, args ...string) (string, error) {
	if len(args) > 1 && args[1] == "false" {
		return "", errors.New("command failed")
	}
	return "", nil
}
func (h *testHost) RunCommandWithEnv(ctx context.Context, name string, env []string, args ...string) (string, error) {
	return h.RunCommand(ctx, name, append(env, args...)...)
}

var _ host.HostAdapter = (*testHost)(nil)

func TestApplicationDeploymentRollsBackPreviousFiles(t *testing.T) {
	ctx := context.Background()
	st := newTestAgentStore(t)
	root := t.TempDir()
	h := &testHost{root: root}
	cache := NewArtifactCache(filepath.Join(t.TempDir(), "artifacts"), st, slog.New(slog.NewTextHandler(io.Discard, nil)), "")
	am := NewApplicationManager(st, h, cache, slog.New(slog.NewTextHandler(io.Discard, nil)))
	app := models.Application{ID: "app-1", Name: "app", Version: "1.0", BinaryPath: "/usr/local/bin/app", UnitName: "nodesteer-app.service"}
	def, _ := json.Marshal(app)
	if err := st.BeginSync(ctx); err != nil {
		t.Fatal(err)
	}
	if err := st.UpsertApplication(ctx, app.ID, def, 1); err != nil {
		st.RollbackSync()
		t.Fatal(err)
	}
	if err := st.CommitSync(); err != nil {
		t.Fatal(err)
	}
	first := []byte("version-1")
	firstSum := sha256.Sum256(first)
	firstSHA := hex.EncodeToString(firstSum[:])
	second := []byte("version-2")
	secondSum := sha256.Sum256(second)
	secondSHA := hex.EncodeToString(secondSum[:])
	content := first
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write(content) }))
	defer srv.Close()
	result := func(protocol.DeployResultPayload) {}
	am.deploy(ctx, protocol.DeployRequestPayload{AppID: app.ID, AppVersion: "1.0", ArtifactURL: srv.URL, ArtifactSHA256: firstSHA, BinaryPath: app.BinaryPath, UnitName: app.UnitName, Config: "old", HealthCheck: []byte(`{"type":"command","target":"true"}`), Operation: "deploy"}, def, "e1", result)
	content = second
	am.deploy(ctx, protocol.DeployRequestPayload{AppID: app.ID, AppVersion: "2.0", ArtifactURL: srv.URL, ArtifactSHA256: secondSHA, BinaryPath: app.BinaryPath, UnitName: app.UnitName, Config: "new", HealthCheck: []byte(`{"type":"command","target":"false"}`), Operation: "upgrade"}, def, "e2", result)
	data, err := h.ReadFile(ctx, app.BinaryPath)
	if err != nil || string(data) != string(first) {
		t.Fatalf("previous binary not restored: %q err=%v", data, err)
	}
	config, err := h.ReadFile(ctx, "/etc/nodesteer-app.conf")
	if err != nil || string(config) != "old" {
		t.Fatalf("previous config not restored: %q err=%v", config, err)
	}
}

func TestSystemdHealthRequiresStableActiveState(t *testing.T) {
	h := &transientStatusHost{testHost: testHost{root: t.TempDir()}}
	am := &ApplicationManager{host: h}

	if am.checkOnce(context.Background(), models.HealthCheck{
		Type: models.HealthTypeSystemd,
	}, "nodesteer-test.service", time.Second) {
		t.Fatal("transient active state must not pass health check")
	}
	if h.calls != 2 {
		t.Fatalf("health check calls = %d, want 2", h.calls)
	}
}

func TestFirstDeploymentFailureCleansManagedFiles(t *testing.T) {
	ctx := context.Background()
	st := newTestAgentStore(t)
	h := &testHost{root: t.TempDir()}
	cache := NewArtifactCache(filepath.Join(t.TempDir(), "artifacts"), st, slog.New(slog.NewTextHandler(io.Discard, nil)), "")
	am := NewApplicationManager(st, h, cache, slog.New(slog.NewTextHandler(io.Discard, nil)))
	app := models.Application{ID: "first-failure", Name: "first", Version: "1.0", BinaryPath: "/usr/local/bin/first", UnitName: "nodesteer-first.service"}
	def, _ := json.Marshal(app)
	artifact := []byte("new-version")
	sum := sha256.Sum256(artifact)
	sha := hex.EncodeToString(sum[:])
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write(artifact) }))
	defer server.Close()
	var result protocol.DeployResultPayload
	am.deploy(ctx, protocol.DeployRequestPayload{
		AppID: app.ID, AppVersion: app.Version, ArtifactURL: server.URL, ArtifactSHA256: sha,
		BinaryPath: app.BinaryPath, UnitName: app.UnitName, Config: "new-config",
		HealthCheck: []byte(`{"type":"command","target":"false"}`), Operation: "deploy",
	}, def, "first-failure-exec", func(p protocol.DeployResultPayload) { result = p })
	if result.OK || !result.Rollback {
		t.Fatalf("expected failed deployment with rollback, got %+v", result)
	}
	if _, err := h.Stat(ctx, app.BinaryPath); !os.IsNotExist(err) {
		t.Fatalf("failed first deployment left binary, err=%v", err)
	}
	if _, err := h.Stat(ctx, "/etc/nodesteer-first.conf"); !os.IsNotExist(err) {
		t.Fatalf("failed first deployment left config, err=%v", err)
	}
	if _, err := h.Stat(ctx, "/etc/systemd/system/"+app.UnitName); !os.IsNotExist(err) {
		t.Fatalf("failed first deployment left unit, err=%v", err)
	}
	deployments, err := st.GetActiveDeployments(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if len(deployments) != 0 {
		t.Fatalf("failed deployment journal did not converge: %+v", deployments)
	}
}
