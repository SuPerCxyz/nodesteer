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

// TestDeploymentJournalRecordsInstalledFromVersion from_version 必须记录本节点
// 实际安装版本（最近一次成功部署的 to_version），而不是 Hub 当前应用定义版本
// （缺陷 OBS-20261016-JOURNAL-FROM-VERSION）。
func TestDeploymentJournalRecordsInstalledFromVersion(t *testing.T) {
	ctx := context.Background()
	st := newTestAgentStore(t)
	h := &testHost{root: t.TempDir()}
	cache := NewArtifactCache(filepath.Join(t.TempDir(), "artifacts"), st, discardLogger(), "")
	am := NewApplicationManager(st, h, cache, discardLogger())

	// 应用定义当前版本 3.0（期望版本）；节点实际只装过 1.0。
	app := models.Application{
		ID: "journal-app", Name: "journal", Version: "3.0",
		BinaryPath: "/usr/local/bin/journal", UnitName: "nodesteer-journal.service",
	}
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

	// 从未部署过 → 无已安装版本
	if v, err := st.InstalledApplicationVersion(ctx, app.ID); err != nil || v != "" {
		t.Fatalf("installed version before any deployment = %q err=%v, want empty", v, err)
	}

	// 历史：成功部署 1.0，随后一次升级到 2.0 但回滚成功（实际仍是 1.0）
	mustDeployRow := func(id, from, to, kind string, at time.Time) {
		t.Helper()
		ts := at.UTC().Format(time.RFC3339Nano)
		if err := st.CreateDeployment(ctx, &DeploymentRow{
			ID: id, ApplicationID: app.ID, FromVersion: from, ToVersion: to,
			Phase: PhasePreparing, StartedAt: ts, UpdatedAt: ts,
		}); err != nil {
			t.Fatal(err)
		}
		if err := st.CompleteDeployment(ctx, id, kind, ""); err != nil {
			t.Fatal(err)
		}
	}
	mustDeployRow("d1", "", "1.0", CompletedKindDeployed, time.Now().Add(-2*time.Hour))
	mustDeployRow("d2", "1.0", "2.0", CompletedKindRolledBack, time.Now().Add(-time.Hour))
	if v, err := st.InstalledApplicationVersion(ctx, app.ID); err != nil || v != "1.0" {
		t.Fatalf("installed version after rollback = %q err=%v, want 1.0", v, err)
	}

	// 本次部署尝试（健康检查失败触发回滚，仅验证 journal 首行）：from_version 必须是 1.0
	var result protocol.DeployResultPayload
	am.deploy(ctx, protocol.DeployRequestPayload{
		AppID: app.ID, AppVersion: "3.0",
		BinaryPath: app.BinaryPath, UnitName: app.UnitName, Config: "new-config",
		HealthCheck: []byte(`{"type":"command","target":"false"}`), Operation: "upgrade",
	}, def, "exec-journal", func(p protocol.DeployResultPayload) { result = p })
	if result.OK {
		t.Fatalf("expected failed deployment for journal assertion, got %+v", result)
	}

	rows, err := st.ListDeploymentsByApplication(ctx, app.ID)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 3 {
		t.Fatalf("deployment rows = %d, want 3", len(rows))
	}
	last := rows[len(rows)-1]
	if last.FromVersion != "1.0" {
		t.Fatalf("journal from_version = %q, want actual installed 1.0 (not definition 3.0)", last.FromVersion)
	}
	if last.ToVersion != "3.0" {
		t.Fatalf("journal to_version = %q, want 3.0", last.ToVersion)
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

func recoveryFixture(t *testing.T) (*ApplicationManager, *testHost, models.Application) {
	t.Helper()
	return recoveryFixtureWithLogger(t, discardLogger())
}

// recoveryFixtureWithLogger 与 recoveryFixture 相同，但注入指定 logger（用于断言恢复日志）。
func recoveryFixtureWithLogger(t *testing.T, logger *slog.Logger) (*ApplicationManager, *testHost, models.Application) {
	t.Helper()
	st := newTestAgentStore(t)
	h := &testHost{root: t.TempDir()}
	am := NewApplicationManager(st, h, nil, logger)
	app := models.Application{ID: "recover-owned", Revision: 1, BinaryPath: "/opt/owned/bin", ConfigPath: "/opt/owned/config", UnitName: "owned.service"}
	def, _ := json.Marshal(app)
	if err := st.BeginSync(t.Context()); err != nil {
		t.Fatal(err)
	}
	if err := st.UpsertApplication(t.Context(), app.ID, def, 1); err != nil {
		t.Fatal(err)
	}
	if err := st.CommitSync(); err != nil {
		t.Fatal(err)
	}
	for path, data := range map[string]string{app.BinaryPath: "old binary", app.ConfigPath: "old config", "/etc/systemd/system/" + app.UnitName: "old unit"} {
		if err := h.WriteFile(t.Context(), path, []byte(data), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	return am, h, app
}

// matchingBinding 返回与 recoveryFixture 当前定义一致的 journal 绑定字段。
func matchingBinding(app models.Application) (int64, string, string, string) {
	return app.Revision, app.BinaryPath, app.ConfigPath, app.UnitName
}

func TestPreparingRecoveryPreservesLegacyFiles(t *testing.T) {
	am, h, app := recoveryFixture(t)
	if err := am.store.RegisterUnit(t.Context(), app.ID, app.UnitName); err != nil {
		t.Fatal(err)
	}
	if err := am.store.CreateDeployment(t.Context(), &DeploymentRow{ID: "legacy", ApplicationID: app.ID, Phase: PhasePreparing}); err != nil {
		t.Fatal(err)
	}
	am.RecoverDeployments(t.Context())
	for path, want := range map[string]string{app.BinaryPath: "old binary", app.ConfigPath: "old config", "/etc/systemd/system/" + app.UnitName: "old unit"} {
		if got, err := h.ReadFile(t.Context(), path); err != nil || string(got) != want {
			t.Fatalf("legacy preparation changed %s: %q %v", path, got, err)
		}
	}
	if !h.active {
		t.Fatal("registered old service not recovered")
	}
	// CR-3：中断的准备阶段行落终态（DONE + prepare_interrupted），不再永久悬挂；
	// 文件保留与“不重放”语义不变。
	active, _ := am.store.GetActiveDeployments(t.Context())
	if len(active) != 0 {
		t.Fatalf("interrupted preparing journal must leave the active set: %+v", active)
	}
	if row := deploymentRowByID(t, am.store, app.ID, "legacy"); row.Phase != PhaseDone || row.CompletedKind != CompletedKindPrepareInterrupted {
		t.Fatalf("journal = %s/%s, want DONE/%s", row.Phase, row.CompletedKind, CompletedKindPrepareInterrupted)
	}
}

func TestFirstDeploymentRecoveryUsesAbsentMarkers(t *testing.T) {
	am, h, app := recoveryFixture(t)
	rev, bp, cp, un := matchingBinding(app)
	d := &DeploymentRow{ID: "first", ApplicationID: app.ID, Phase: PhaseReplaced, BackupPath: backupAbsent, BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent,
		AppRevision: rev, BinaryPath: bp, ConfigPath: cp, UnitName: un}
	if err := am.store.CreateDeployment(t.Context(), d); err != nil {
		t.Fatal(err)
	}
	am.RecoverDeployments(t.Context())
	for _, path := range []string{app.BinaryPath, app.ConfigPath, "/etc/systemd/system/" + app.UnitName} {
		if _, err := h.Stat(t.Context(), path); !os.IsNotExist(err) {
			t.Fatalf("owned first-install file retained: %s %v", path, err)
		}
	}
	active, _ := am.store.GetActiveDeployments(t.Context())
	if len(active) != 0 {
		t.Fatal("successful first-install cleanup not completed")
	}
}

func TestRecoveryMissingBackupNeverDeletesOrReplays(t *testing.T) {
	cases := []struct {
		name      string
		backup    string
		binding   bool
		wantPhase string
		wantKind  string
	}{
		// 旧行缺绑定字段（未知绑定）→ 一律不重放，落终态 legacy_unbound（CR-3）。
		{name: "legacy-empty", backup: "", binding: false, wantPhase: PhaseDone, wantKind: CompletedKindLegacyUnbound},
		// 绑定匹配但 backup 不可读 → 尝试重放后在预检处失败，落 ROLLBACK_FAILED（保持活动可重试）。
		{name: "missing-file", backup: "/missing/backup", binding: true, wantPhase: PhaseRollbackFailed},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			am, h, app := recoveryFixture(t)
			h.active = true
			d := &DeploymentRow{ID: "missing", ApplicationID: app.ID, Phase: PhaseVerifying, BackupPath: tc.backup, BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent}
			if tc.binding {
				d.AppRevision, d.BinaryPath, d.ConfigPath, d.UnitName = matchingBinding(app)
			}
			if err := am.store.CreateDeployment(t.Context(), d); err != nil {
				t.Fatal(err)
			}
			am.RecoverDeployments(t.Context())
			if got, err := h.ReadFile(t.Context(), app.BinaryPath); err != nil || string(got) != "old binary" || !h.active {
				t.Fatalf("unknown ownership changed file/service: %q %v", got, err)
			}
			active, _ := am.store.GetActiveDeployments(t.Context())
			if tc.wantPhase == PhaseDone {
				if len(active) != 0 {
					t.Fatalf("unreplayable journal must leave the active set: %+v", active)
				}
				row := deploymentRowByID(t, am.store, app.ID, "missing")
				if row.Phase != PhaseDone || row.CompletedKind != tc.wantKind {
					t.Fatalf("journal = %s/%s, want DONE/%s", row.Phase, row.CompletedKind, tc.wantKind)
				}
				return
			}
			if len(active) != 1 || active[0].Phase != tc.wantPhase {
				t.Fatalf("journal phase = %+v, want %s", active, tc.wantPhase)
			}
		})
	}
}

func TestDeploymentJournalWriteFailuresRestoreOldFiles(t *testing.T) {
	for _, phase := range []string{PhaseStopped, PhaseReplaced, PhaseStarted, PhaseVerifying, PhaseDone} {
		t.Run(phase, func(t *testing.T) {
			am, h, app := recoveryFixture(t)
			h.active = true
			content := []byte("new binary")
			sum := sha256.Sum256(content)
			sha := hex.EncodeToString(sum[:])
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { w.Write(content) }))
			defer server.Close()
			am.artifact = NewArtifactCache(filepath.Join(t.TempDir(), "cache"), am.store, discardLogger(), "")
			// 数据库真实失败注入，验证失败发生在正确副作用边界。
			if _, err := am.store.DB().Exec(`CREATE TRIGGER fail_phase BEFORE UPDATE OF phase ON deployments WHEN NEW.phase = '` + phase + `' BEGIN SELECT RAISE(ABORT, 'injected journal failure'); END`); err != nil {
				t.Fatal(err)
			}
			def, _ := json.Marshal(app)
			var result protocol.DeployResultPayload
			am.deploy(t.Context(), protocol.DeployRequestPayload{AppID: app.ID, AppVersion: "v2", ArtifactURL: server.URL, ArtifactSHA256: sha, BinaryPath: app.BinaryPath, ConfigPath: app.ConfigPath, UnitName: app.UnitName, Config: "new config"}, def, "exec", func(p protocol.DeployResultPayload) { result = p })
			if result.OK || !strings.Contains(result.Error, "journal") && !strings.Contains(result.Error, "phase") {
				t.Fatalf("journal failure reported success: %+v", result)
			}
			for path, want := range map[string]string{app.BinaryPath: "old binary", app.ConfigPath: "old config", "/etc/systemd/system/" + app.UnitName: "old unit"} {
				if got, err := h.ReadFile(t.Context(), path); err != nil || string(got) != want {
					t.Fatalf("journal failure lost %s: %q %v", path, got, err)
				}
			}
			if !h.active {
				t.Fatal("old service not running after failure")
			}
		})
	}
}

// TestRecoverySkipsSupersededStaleDeployment 复现 AR-32 主链：回滚失败的毒行之后
// 同应用已有更晚的成功部署（DONE + completed_kind=deployed），重启恢复必须不重放
// 陈旧 backup、不启动服务、不动文件。
func TestRecoverySkipsSupersededStaleDeployment(t *testing.T) {
	am, h, app := recoveryFixture(t)
	ctx := t.Context()
	backup := filepath.Join(t.TempDir(), "stale.binary")
	if err := os.WriteFile(backup, []byte("stale binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	rev, bp, cp, un := matchingBinding(app)
	stale := &DeploymentRow{
		ID: "stale", ApplicationID: app.ID, Phase: PhaseRollbackFailed,
		BackupPath: backup, BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent,
		AppRevision: rev, BinaryPath: bp, ConfigPath: cp, UnitName: un,
		StartedAt: time.Now().Add(-10 * time.Second).UTC().Format(time.RFC3339Nano),
		UpdatedAt: time.Now().UTC().Format(time.RFC3339Nano),
	}
	newer := &DeploymentRow{
		ID: "newer", ApplicationID: app.ID, Phase: PhaseDone, CompletedKind: CompletedKindDeployed, ToVersion: "5.0",
		AppRevision: rev, BinaryPath: bp, ConfigPath: cp, UnitName: un,
		StartedAt: time.Now().Add(-time.Second).UTC().Format(time.RFC3339Nano),
		UpdatedAt: time.Now().UTC().Format(time.RFC3339Nano),
	}
	for _, d := range []*DeploymentRow{stale, newer} {
		if err := am.store.CreateDeployment(ctx, d); err != nil {
			t.Fatal(err)
		}
	}
	h.active = false
	am.RecoverDeployments(ctx)
	for path, want := range map[string]string{bp: "old binary", cp: "old config", "/etc/systemd/system/" + un: "old unit"} {
		if got, err := h.ReadFile(ctx, path); err != nil || string(got) != want {
			t.Fatalf("superseded recovery changed %s: %q %v", path, got, err)
		}
	}
	if h.active {
		t.Fatal("superseded recovery must not start the service")
	}
	// CR-3：被取代的行落终态 superseded，离开活动集合、重启不再重复告警；记录仍保留在 journal。
	active, err := am.store.GetActiveDeployments(ctx)
	if err != nil || len(active) != 0 {
		t.Fatalf("superseded journal must leave the active set: %+v err=%v", active, err)
	}
	if row := deploymentRowByID(t, am.store, app.ID, "stale"); row.Phase != PhaseDone || row.CompletedKind != CompletedKindSuperseded {
		t.Fatalf("journal = %s/%s, want DONE/%s", row.Phase, row.CompletedKind, CompletedKindSuperseded)
	}
}

// TestRecoverySupersedeRequiresSuccessfulDeploy CR-1：更晚的 DONE 行只有终态成因是
// 成功部署（deployed）才构成 superseded；prepare_failed（未做任何宿主替换）、
// rolled_back（该次尝试已回到起点）与旧行缺 completed_kind（未知）都不构成取代，
// 半完成行仍按安全恢复重放。
func TestRecoverySupersedeRequiresSuccessfulDeploy(t *testing.T) {
	cases := []struct {
		name          string
		completedKind string
		wantReplay    bool
	}{
		{name: "deployed", completedKind: CompletedKindDeployed, wantReplay: false},
		{name: "rolled back", completedKind: CompletedKindRolledBack, wantReplay: true},
		{name: "prepare failed", completedKind: CompletedKindPrepareFailed, wantReplay: true},
		{name: "legacy missing kind", completedKind: "", wantReplay: true},
		// CR-3 关闭的四种终态成因都不是“成功部署”，不得反过来取代更早的可恢复行。
		{name: "superseded kind", completedKind: CompletedKindSuperseded, wantReplay: true},
		{name: "stale binding kind", completedKind: CompletedKindStaleBinding, wantReplay: true},
		{name: "legacy unbound kind", completedKind: CompletedKindLegacyUnbound, wantReplay: true},
		{name: "prepare interrupted kind", completedKind: CompletedKindPrepareInterrupted, wantReplay: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			am, h, app := recoveryFixture(t)
			ctx := t.Context()
			rev, bp, cp, un := matchingBinding(app)
			dir := t.TempDir()
			backups := map[string]string{
				filepath.Join(dir, "binary"): "old binary",
				filepath.Join(dir, "conf"):   "old config",
				filepath.Join(dir, "unit"):   "old unit",
			}
			for path, data := range backups {
				if err := os.WriteFile(path, []byte(data), 0o755); err != nil {
					t.Fatal(err)
				}
			}
			// 半完成行：宿主文件已是本次尝试替换的新版本
			for path, data := range map[string]string{bp: "new binary", cp: "new config", "/etc/systemd/system/" + un: "new unit"} {
				if err := h.WriteFile(ctx, path, []byte(data), 0o755); err != nil {
					t.Fatal(err)
				}
			}
			stale := &DeploymentRow{
				ID: "stale", ApplicationID: app.ID, Phase: PhaseVerifying,
				BackupPath:       filepath.Join(dir, "binary"),
				BackupConfigPath: filepath.Join(dir, "conf"),
				BackupUnitPath:   filepath.Join(dir, "unit"),
				AppRevision:      rev, BinaryPath: bp, ConfigPath: cp, UnitName: un,
				StartedAt: time.Now().Add(-10 * time.Second).UTC().Format(time.RFC3339Nano),
				UpdatedAt: time.Now().UTC().Format(time.RFC3339Nano),
			}
			later := &DeploymentRow{
				ID: "later", ApplicationID: app.ID, Phase: PhaseDone, CompletedKind: tc.completedKind, ToVersion: "5.0",
				AppRevision: rev, BinaryPath: bp, ConfigPath: cp, UnitName: un,
				StartedAt: time.Now().Add(-time.Second).UTC().Format(time.RFC3339Nano),
				UpdatedAt: time.Now().UTC().Format(time.RFC3339Nano),
			}
			for _, d := range []*DeploymentRow{stale, later} {
				if err := am.store.CreateDeployment(ctx, d); err != nil {
					t.Fatal(err)
				}
			}
			h.active = false
			am.RecoverDeployments(ctx)
			active, err := am.store.GetActiveDeployments(ctx)
			if err != nil {
				t.Fatal(err)
			}
			if tc.wantReplay {
				for path, want := range map[string]string{bp: "old binary", cp: "old config", "/etc/systemd/system/" + un: "old unit"} {
					if got, err := h.ReadFile(ctx, path); err != nil || string(got) != want {
						t.Fatalf("non-successful later DONE must not supersede: %s = %q %v", path, got, err)
					}
				}
				if !h.active {
					t.Fatal("replayed recovery must start the previous service")
				}
				if len(active) != 0 {
					t.Fatalf("replayed row must converge to DONE: %+v", active)
				}
				// 恢复重放成功的终态成因必须是 rolled_back，不会冒充成功部署。
				history, err := am.store.ListDeploymentsByApplication(ctx, app.ID)
				if err != nil {
					t.Fatal(err)
				}
				for _, d := range history {
					if d.ID == "stale" && (d.Phase != PhaseDone || d.CompletedKind != CompletedKindRolledBack) {
						t.Fatalf("recovery terminal cause = %s/%s, want DONE/%s", d.Phase, d.CompletedKind, CompletedKindRolledBack)
					}
				}
				return
			}
			for path, want := range map[string]string{bp: "new binary", cp: "new config", "/etc/systemd/system/" + un: "new unit"} {
				if got, err := h.ReadFile(ctx, path); err != nil || string(got) != want {
					t.Fatalf("successful deployment must not be rolled back: %s = %q %v", path, got, err)
				}
			}
			if h.active {
				t.Fatal("superseded recovery must not start the service")
			}
			// CR-3：被取代的行落终态 superseded，不再悬挂。
			if len(active) != 0 {
				t.Fatalf("superseded journal must leave the active set: %+v err=%v", active, err)
			}
			if row := deploymentRowByID(t, am.store, app.ID, "stale"); row.Phase != PhaseDone || row.CompletedKind != CompletedKindSuperseded {
				t.Fatalf("journal = %s/%s, want DONE/%s", row.Phase, row.CompletedKind, CompletedKindSuperseded)
			}
		})
	}
}

// TestDeploymentStartedAfterBoundaries 固定 deploymentStartedAfter 的既有边界行为：
// 同秒不构成“更晚”（含省略尾随零的等价时刻）；任一侧解析失败仍回退字典序比较，
// 先后方向由原始字符串决定，不因“其中一侧可解析”而更可靠。
func TestDeploymentStartedAfterBoundaries(t *testing.T) {
	cases := []struct {
		name string
		a    string
		b    string
		want bool
	}{
		{name: "same second", a: "2026-10-16T10:00:00Z", b: "2026-10-16T10:00:00Z", want: false},
		{name: "later sub-second", a: "2026-10-16T10:00:00.5Z", b: "2026-10-16T10:00:00Z", want: true},
		{name: "trailing zero equivalence", a: "2026-10-16T10:00:00.500Z", b: "2026-10-16T10:00:00.5Z", want: false},
		{name: "earlier", a: "2026-10-16T09:59:59Z", b: "2026-10-16T10:00:00Z", want: false},
		{name: "unparsable left falls back to lexicographic", a: "invalid", b: "2026-10-16T10:00:00Z", want: true},
		{name: "unparsable right falls back to lexicographic", a: "2026-10-16T10:00:00Z", b: "invalid", want: false},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := deploymentStartedAfter(tc.a, tc.b); got != tc.want {
				t.Fatalf("deploymentStartedAfter(%q, %q) = %v, want %v", tc.a, tc.b, got, tc.want)
			}
		})
	}
}

// TestDeployJournalRecordsCompletedKind 三处 DONE 写入口如实记录终态成因：
// 成功部署 deployed、回滚成功 rolled_back、准备失败 prepare_failed（CR-1 判定依据）。
func TestDeployJournalRecordsCompletedKind(t *testing.T) {
	ctx := context.Background()
	newManager := func(t *testing.T) (*ApplicationManager, *testHost, *LocalStore, models.Application, []byte) {
		t.Helper()
		st := newTestAgentStore(t)
		h := &testHost{root: t.TempDir()}
		cache := NewArtifactCache(filepath.Join(t.TempDir(), "artifacts"), st, discardLogger(), "")
		am := NewApplicationManager(st, h, cache, discardLogger())
		app := models.Application{ID: "kind-app", Revision: 1, Version: "1.0", BinaryPath: "/usr/local/bin/kind-app", UnitName: "nodesteer-kind-app.service"}
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
		if err := h.WriteFile(ctx, app.BinaryPath, []byte("old binary"), 0o755); err != nil {
			t.Fatal(err)
		}
		return am, h, st, app, def
	}
	soleRow := func(t *testing.T, st *LocalStore, appID string) *DeploymentRow {
		t.Helper()
		rows, err := st.ListDeploymentsByApplication(context.Background(), appID)
		if err != nil || len(rows) != 1 {
			t.Fatalf("journal rows = %d err=%v, want 1", len(rows), err)
		}
		return rows[0]
	}

	t.Run("deployed", func(t *testing.T) {
		am, h, st, app, def := newManager(t)
		h.active = true
		var result protocol.DeployResultPayload
		am.deploy(ctx, protocol.DeployRequestPayload{
			AppID: app.ID, AppVersion: "2.0", AppRevision: app.Revision,
			BinaryPath: app.BinaryPath, UnitName: app.UnitName, Config: "new config",
			HealthCheck: []byte(`{"type":"command","target":"true","attempts":1}`), Operation: "deploy",
		}, def, "exec", func(p protocol.DeployResultPayload) { result = p })
		if !result.OK {
			t.Fatalf("deploy failed: %+v", result)
		}
		if row := soleRow(t, st, app.ID); row.Phase != PhaseDone || row.CompletedKind != CompletedKindDeployed {
			t.Fatalf("journal = %s/%s, want %s/%s", row.Phase, row.CompletedKind, PhaseDone, CompletedKindDeployed)
		}
	})

	t.Run("rolled back", func(t *testing.T) {
		am, h, st, app, def := newManager(t)
		h.active = true
		// 真实失败注入：STARTED 相位写失败 → 回滚成功（应用无 health check，复检直接通过）。
		if _, err := st.DB().Exec(`CREATE TRIGGER fail_started BEFORE UPDATE OF phase ON deployments WHEN NEW.phase = 'STARTED' BEGIN SELECT RAISE(ABORT, 'injected journal failure'); END`); err != nil {
			t.Fatal(err)
		}
		var result protocol.DeployResultPayload
		am.deploy(ctx, protocol.DeployRequestPayload{
			AppID: app.ID, AppVersion: "2.0", AppRevision: app.Revision,
			BinaryPath: app.BinaryPath, UnitName: app.UnitName, Config: "new config", Operation: "deploy",
		}, def, "exec", func(p protocol.DeployResultPayload) { result = p })
		if result.OK || result.Error == "" {
			t.Fatalf("expected failed deployment, got %+v", result)
		}
		if row := soleRow(t, st, app.ID); row.Phase != PhaseDone || row.CompletedKind != CompletedKindRolledBack {
			t.Fatalf("journal = %s/%s, want %s/%s", row.Phase, row.CompletedKind, PhaseDone, CompletedKindRolledBack)
		}
		if data, err := h.ReadFile(ctx, app.BinaryPath); err != nil || string(data) != "old binary" {
			t.Fatalf("rollback did not restore binary: %q %v", data, err)
		}
	})

	t.Run("prepare failed", func(t *testing.T) {
		am, h, st, app, def := newManager(t)
		h.active = true
		var result protocol.DeployResultPayload
		am.deploy(ctx, protocol.DeployRequestPayload{
			AppID: app.ID, AppVersion: "2.0", AppRevision: app.Revision,
			BinaryPath: app.BinaryPath, UnitName: "bad/name", Config: "new config", Operation: "deploy",
		}, def, "exec", func(p protocol.DeployResultPayload) { result = p })
		if result.OK || !strings.Contains(result.Error, "invalid systemd unit name") {
			t.Fatalf("expected preparation failure, got %+v", result)
		}
		if row := soleRow(t, st, app.ID); row.Phase != PhaseDone || row.CompletedKind != CompletedKindPrepareFailed {
			t.Fatalf("journal = %s/%s, want %s/%s", row.Phase, row.CompletedKind, PhaseDone, CompletedKindPrepareFailed)
		}
		if data, err := h.ReadFile(ctx, app.BinaryPath); err != nil || string(data) != "old binary" {
			t.Fatalf("preparation failure must not touch host files: %q %v", data, err)
		}
	})
}

// TestRecoverySkipsBindingMismatch journal 绑定与当前定义不一致（或旧行缺失绑定）时
// 一律不重放：不恢复文件、不启动服务，并落终态（legacy_unbound / stale_binding，CR-3），
// journal 记录保留可查。
func TestRecoverySkipsBindingMismatch(t *testing.T) {
	cases := []struct {
		name     string
		mutate   func(d *DeploymentRow, app models.Application)
		wantKind string
	}{
		{"legacy missing binding", func(d *DeploymentRow, _ models.Application) {
			d.AppRevision, d.BinaryPath, d.ConfigPath, d.UnitName = 0, "", "", ""
		}, CompletedKindLegacyUnbound},
		{"revision mismatch", func(d *DeploymentRow, _ models.Application) { d.AppRevision++ }, CompletedKindStaleBinding},
		{"binary path drift", func(d *DeploymentRow, _ models.Application) { d.BinaryPath = "/opt/other/bin" }, CompletedKindStaleBinding},
		{"unit drift", func(d *DeploymentRow, _ models.Application) { d.UnitName = "other.service" }, CompletedKindStaleBinding},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			am, h, app := recoveryFixture(t)
			ctx := t.Context()
			rev, bp, cp, un := matchingBinding(app)
			d := &DeploymentRow{
				ID: "drifted", ApplicationID: app.ID, Phase: PhaseVerifying, BackupPath: backupAbsent,
				BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent,
				AppRevision: rev, BinaryPath: bp, ConfigPath: cp, UnitName: un,
			}
			tc.mutate(d, app)
			if err := am.store.CreateDeployment(ctx, d); err != nil {
				t.Fatal(err)
			}
			h.active = false
			am.RecoverDeployments(ctx)
			for path, want := range map[string]string{bp: "old binary", cp: "old config", "/etc/systemd/system/" + un: "old unit"} {
				if got, err := h.ReadFile(ctx, path); err != nil || string(got) != want {
					t.Fatalf("binding mismatch changed %s: %q %v", path, got, err)
				}
			}
			if h.active {
				t.Fatal("binding mismatch must not start the service")
			}
			// CR-3：绑定失效行落终态，不再留在活动集合、重启不再重复告警。
			active, err := am.store.GetActiveDeployments(ctx)
			if err != nil || len(active) != 0 {
				t.Fatalf("binding mismatch journal must leave the active set: %+v err=%v", active, err)
			}
			if row := deploymentRowByID(t, am.store, app.ID, "drifted"); row.Phase != PhaseDone || row.CompletedKind != tc.wantKind {
				t.Fatalf("journal = %s/%s, want DONE/%s", row.Phase, row.CompletedKind, tc.wantKind)
			}
		})
	}
}

// TestRecoveryReplaysMatchingIncompleteDeployment 无更晚成功部署且绑定匹配时，
// 恢复仍按 journal 记录的绑定路径重放回滚（SYS-51 类路径不回退）。
func TestRecoveryReplaysMatchingIncompleteDeployment(t *testing.T) {
	am, h, app := recoveryFixture(t)
	ctx := t.Context()
	rev, bp, cp, un := matchingBinding(app)
	dir := t.TempDir()
	backups := map[string]string{
		filepath.Join(dir, "binary"): "old binary",
		filepath.Join(dir, "conf"):   "old config",
		filepath.Join(dir, "unit"):   "old unit",
	}
	for path, data := range backups {
		if err := os.WriteFile(path, []byte(data), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	// 半完成的部署已经把宿主文件替换成新版本
	for path, data := range map[string]string{bp: "new binary", cp: "new config", "/etc/systemd/system/" + un: "new unit"} {
		if err := h.WriteFile(ctx, path, []byte(data), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	d := &DeploymentRow{
		ID: "half-done", ApplicationID: app.ID, Phase: PhaseVerifying,
		BackupPath:       filepath.Join(dir, "binary"),
		BackupConfigPath: filepath.Join(dir, "conf"),
		BackupUnitPath:   filepath.Join(dir, "unit"),
		AppRevision:      rev, BinaryPath: bp, ConfigPath: cp, UnitName: un,
	}
	if err := am.store.CreateDeployment(ctx, d); err != nil {
		t.Fatal(err)
	}
	am.RecoverDeployments(ctx)
	for path, want := range map[string]string{bp: "old binary", cp: "old config", "/etc/systemd/system/" + un: "old unit"} {
		if got, err := h.ReadFile(ctx, path); err != nil || string(got) != want {
			t.Fatalf("recovery did not replay journal: %s = %q %v", path, got, err)
		}
	}
	if !h.active {
		t.Fatal("recovered previous service must be started")
	}
	active, err := am.store.GetActiveDeployments(ctx)
	if err != nil || len(active) != 0 {
		t.Fatalf("matching recovery must converge to DONE: %+v err=%v", active, err)
	}
}

// TestRecoveryReplaysWhenLaterDeploymentNotDone 边界（已知限制）：同应用存在更晚但
// 未 DONE 的部署行时不构成 superseded，仍按逐条重放处理。
func TestRecoveryReplaysWhenLaterDeploymentNotDone(t *testing.T) {
	am, h, app := recoveryFixture(t)
	ctx := t.Context()
	rev, bp, cp, un := matchingBinding(app)
	d := &DeploymentRow{
		ID: "stale", ApplicationID: app.ID, Phase: PhaseVerifying, BackupPath: backupAbsent,
		BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent,
		AppRevision: rev, BinaryPath: bp, ConfigPath: cp, UnitName: un,
		StartedAt: time.Now().Add(-10 * time.Second).UTC().Format(time.RFC3339Nano),
		UpdatedAt: time.Now().UTC().Format(time.RFC3339Nano),
	}
	later := &DeploymentRow{
		ID: "later-preparing", ApplicationID: app.ID, Phase: PhasePreparing,
		StartedAt: time.Now().Add(-time.Second).UTC().Format(time.RFC3339Nano),
		UpdatedAt: time.Now().UTC().Format(time.RFC3339Nano),
	}
	for _, row := range []*DeploymentRow{d, later} {
		if err := am.store.CreateDeployment(ctx, row); err != nil {
			t.Fatal(err)
		}
	}
	am.RecoverDeployments(ctx)
	// 陈旧行照旧重放（absent 标记 → 清理受管文件并 DONE）
	for _, path := range []string{bp, cp, "/etc/systemd/system/" + un} {
		if _, err := h.Stat(ctx, path); !os.IsNotExist(err) {
			t.Fatalf("stale row was not replayed: %s %v", path, err)
		}
	}
	// CR-3：更晚的 PREPARING 行按保守语义处理（不动文件、不重放）后落终态，不再悬挂。
	active, err := am.store.GetActiveDeployments(ctx)
	if err != nil || len(active) != 0 {
		t.Fatalf("both rows must converge to terminal states: %+v err=%v", active, err)
	}
	if row := deploymentRowByID(t, am.store, app.ID, "later-preparing"); row.Phase != PhaseDone || row.CompletedKind != CompletedKindPrepareInterrupted {
		t.Fatalf("journal = %s/%s, want DONE/%s", row.Phase, row.CompletedKind, CompletedKindPrepareInterrupted)
	}
}

// TestRollbackFailureMarksRollbackFailedPhase 回滚已尝试且**真的失败**时不再遗留
// ROLLING_BACK，而是落终端状态 ROLLBACK_FAILED；重启恢复在绑定匹配且无更晚 DONE 时
// 仍可重放并收敛。
//
// 注意与“回滚成功但被失败版本健康定义误判”区分（缺陷 APPUI-20261016-ROLLBACK-FALSE-FAILURE）：
// 本用例注入的是真实的还原写入失败（第 3 次 AtomicReplace：v1 安装、v2 安装、回滚还原）。
func TestRollbackFailureMarksRollbackFailedPhase(t *testing.T) {
	ctx := context.Background()
	st := newTestAgentStore(t)
	h := &failingRestoreHost{testHost: testHost{root: t.TempDir()}}
	cache := NewArtifactCache(filepath.Join(t.TempDir(), "artifacts"), st, discardLogger(), "")
	am := NewApplicationManager(st, h, cache, discardLogger())
	app := models.Application{ID: "rollback-fail", Revision: 1, Version: "1.0", BinaryPath: "/usr/local/bin/rollback-fail", UnitName: "nodesteer-rollback-fail.service"}
	h.path, h.failAt = app.BinaryPath, 3
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
	content := []byte("version-1")
	sum := sha256.Sum256(content)
	sha := hex.EncodeToString(sum[:])
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write(content) }))
	defer server.Close()
	result := func(protocol.DeployResultPayload) {}
	deploy := func(version, config, health string, cb func(protocol.DeployResultPayload)) {
		am.deploy(ctx, protocol.DeployRequestPayload{
			AppID: app.ID, AppVersion: version, AppRevision: 1, ArtifactURL: server.URL, ArtifactSHA256: sha,
			BinaryPath: app.BinaryPath, UnitName: app.UnitName, Config: config,
			HealthCheck: []byte(health), Operation: "deploy",
		}, def, "exec", cb)
	}
	deploy("1.0", "config-1", `{"type":"command","target":"true","attempts":1}`, result)
	if data, err := h.ReadFile(ctx, app.BinaryPath); err != nil || string(data) != "version-1" {
		t.Fatalf("first deploy not applied: %q %v", data, err)
	}
	content = []byte("version-2")
	sum = sha256.Sum256(content)
	sha = hex.EncodeToString(sum[:])
	var failed protocol.DeployResultPayload
	deploy("2.0", "config-2", `{"type":"command","target":"false","attempts":1}`, func(p protocol.DeployResultPayload) { failed = p })
	if failed.OK || !failed.Rollback || !strings.Contains(failed.Error, "rollback failed") {
		t.Fatalf("expected failed rollback report, got %+v", failed)
	}
	// 还原写入失败：二进制仍是失败版本（回滚确实没完成），journal 必须如实落 ROLLBACK_FAILED。
	if data, err := h.ReadFile(ctx, app.BinaryPath); err != nil || string(data) != "version-2" {
		t.Fatalf("failed restore must leave the new binary in place: %q %v", data, err)
	}
	active, err := st.GetActiveDeployments(ctx)
	if err != nil || len(active) != 1 || active[0].Phase != PhaseRollbackFailed {
		t.Fatalf("journal phase = %+v err=%v, want %s", active, err, PhaseRollbackFailed)
	}
	// 重启恢复：绑定匹配、无更晚 DONE；重放回滚（本次还原写入不再失败）后收敛 DONE，
	// 证明 ROLLBACK_FAILED 仍保留恢复路径（不是永久毒行）。
	am.RecoverDeployments(ctx)
	active, err = st.GetActiveDeployments(ctx)
	if err != nil || len(active) != 0 {
		t.Fatalf("ROLLBACK_FAILED row should still be recoverable: %+v err=%v", active, err)
	}
	if data, err := h.ReadFile(ctx, app.BinaryPath); err != nil || string(data) != "version-1" {
		t.Fatalf("recovery changed binary: %q %v", data, err)
	}
}
