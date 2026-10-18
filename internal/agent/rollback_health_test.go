package agent

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// failingRestoreHost 让指定路径上第 failAt 次 AtomicReplace 失败，
// 用于构造“真的回滚失败”（备份可读但还原写入失败），与“回滚其实成功”区分开。
type failingRestoreHost struct {
	testHost
	path   string
	failAt int
	calls  int
}

func (h *failingRestoreHost) AtomicReplace(ctx context.Context, p string, data []byte, mode os.FileMode) error {
	if p == h.path {
		h.calls++
		if h.calls == h.failAt {
			return errors.New("injected restore failure")
		}
	}
	return h.testHost.AtomicReplace(ctx, p, data, mode)
}

// TestRollbackSucceedsWhenFailedVersionHealthCheckWouldFail 回滚已成功还原文件并拉起旧版本时，
// 不得再用**失败版本**的健康定义复检：非 systemd 负向检查（此处 TCP 指向必然拒绝的端口）
// 与被还原的旧版本无关，用它复检必然再失败，会把成功回滚误报为
// "health check failed; rollback failed" + ROLLBACK_FAILED
// （缺陷 APPUI-20261016-ROLLBACK-FALSE-FAILURE）。
func TestRollbackSucceedsWhenFailedVersionHealthCheckWouldFail(t *testing.T) {
	ctx := t.Context()
	am, h, app := recoveryFixture(t)
	am.artifact = NewArtifactCache(filepath.Join(t.TempDir(), "cache"), am.store, discardLogger(), "")
	// 旧版本（被还原版本）自身可用：首次部署成功。
	content := []byte("version-1")
	sum := sha256.Sum256(content)
	sha := hex.EncodeToString(sum[:])
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) { _, _ = w.Write(content) }))
	defer server.Close()
	def, _ := json.Marshal(app)

	var first protocol.DeployResultPayload
	am.deploy(ctx, protocol.DeployRequestPayload{
		AppID: app.ID, AppVersion: "1.0", AppRevision: app.Revision, ArtifactURL: server.URL, ArtifactSHA256: sha,
		BinaryPath: app.BinaryPath, ConfigPath: app.ConfigPath, UnitName: app.UnitName, Config: "config-1",
		HealthCheck: []byte(`{"type":"command","target":"true","attempts":1}`), Operation: "deploy",
	}, def, "exec-1", func(p protocol.DeployResultPayload) { first = p })
	if !first.OK {
		t.Fatalf("first deploy must succeed: %+v", first)
	}

	// 新版本健康检查为负向且非 systemd：健康检查必然失败并触发回滚。
	content = []byte("version-2")
	sum = sha256.Sum256(content)
	sha = hex.EncodeToString(sum[:])
	var failed protocol.DeployResultPayload
	am.deploy(ctx, protocol.DeployRequestPayload{
		AppID: app.ID, AppVersion: "2.0", AppRevision: app.Revision, ArtifactURL: server.URL, ArtifactSHA256: sha,
		BinaryPath: app.BinaryPath, ConfigPath: app.ConfigPath, UnitName: app.UnitName, Config: "config-2",
		HealthCheck: []byte(`{"type":"tcp","target":"127.0.0.1:1","attempts":1,"interval":1,"timeout":1}`),
		Operation:   "upgrade",
	}, def, "exec-2", func(p protocol.DeployResultPayload) { failed = p })

	if failed.OK {
		t.Fatalf("second deploy must fail: %+v", failed)
	}
	if !failed.Rollback || failed.Health != "unhealthy" {
		t.Fatalf("rollback must be reported: %+v", failed)
	}
	if !strings.Contains(failed.Error, "health check failed") {
		t.Fatalf("error must keep the health-check reason: %q", failed.Error)
	}
	if strings.Contains(failed.Error, "rollback failed") {
		t.Fatalf("successful rollback must not be reported as failed: %q", failed.Error)
	}
	// 被还原版本的文件确实回来了，旧服务已重新运行。
	if got, err := h.ReadFile(ctx, app.BinaryPath); err != nil || string(got) != "version-1" {
		t.Fatalf("binary not restored: %q %v", got, err)
	}
	if got, err := h.ReadFile(ctx, app.ConfigPath); err != nil || string(got) != "config-1" {
		t.Fatalf("config not restored: %q %v", got, err)
	}
	if !h.active {
		t.Fatal("restored service must be running")
	}
	// journal 必须收敛为 DONE(rolled_back)，而不是 ROLLBACK_FAILED。
	active, err := am.store.GetActiveDeployments(ctx)
	if err != nil || len(active) != 0 {
		t.Fatalf("journal must converge to a terminal DONE row: %+v err=%v", active, err)
	}
	rows, err := am.store.ListDeploymentsByApplication(ctx, app.ID)
	if err != nil || len(rows) != 2 {
		t.Fatalf("deployment rows = %+v err=%v", rows, err)
	}
	last := rows[len(rows)-1]
	if last.Phase != PhaseDone || last.CompletedKind != CompletedKindRolledBack {
		t.Fatalf("rollback journal = %+v, want DONE/%s", last, CompletedKindRolledBack)
	}
}

// TestRollbackHealthCheckIsVersionAgnostic 回滚复检不得复用失败版本的健康定义：
// 即使失败版本的健康检查是“必然失败”的负向检查，回滚成功也必须返回 true。
func TestRollbackHealthCheckIsVersionAgnostic(t *testing.T) {
	ctx := t.Context()
	am, h, app := recoveryFixture(t)
	if err := h.WriteFile(ctx, app.BinaryPath, []byte("new binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	dir := t.TempDir()
	backup := filepath.Join(dir, "binary.bak")
	if err := os.WriteFile(backup, []byte("old binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	unitBackup := filepath.Join(dir, "unit.bak")
	if err := os.WriteFile(unitBackup, []byte("old unit"), 0o644); err != nil {
		t.Fatal(err)
	}
	h.active = false
	ok := am.rollback(ctx, app.BinaryPath, backup, app.UnitName, app.ConfigPath, backupAbsent, unitBackup)
	if !ok {
		t.Fatal("rollback with a healthy restored unit must succeed regardless of the failed version health definition")
	}
	if !h.active {
		t.Fatal("restored service must be started")
	}
	if got, err := h.ReadFile(ctx, app.BinaryPath); err != nil || string(got) != "old binary" {
		t.Fatalf("binary not restored: %q %v", got, err)
	}
}

// startFailureHost 服务启动失败（用于验证真回滚失败仍返回 false）。
type startFailureHost struct {
	testHost
}

func (h *startFailureHost) StartService(context.Context, string) error {
	return errors.New("injected start failure")
}

// TestRollbackLivenessCheckFailsWhenRestoredServiceCannotStart 真回滚失败仍必须返回 false：
// 还原后的服务起不来时不得谎报成功（ROLLBACK_FAILED 语义保留）。
func TestRollbackLivenessCheckFailsWhenRestoredServiceCannotStart(t *testing.T) {
	ctx := t.Context()
	st := newTestAgentStore(t)
	h := &startFailureHost{testHost: testHost{root: t.TempDir()}}
	am := NewApplicationManager(st, h, nil, discardLogger())
	app := models.Application{ID: "restore-start-fail", Revision: 1, Version: "1.0", BinaryPath: "/opt/fail/bin", ConfigPath: "/opt/fail/config", UnitName: "fail.service"}
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
	dir := t.TempDir()
	backup := filepath.Join(dir, "binary.bak")
	if err := os.WriteFile(backup, []byte("old binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	unitBackup := filepath.Join(dir, "unit.bak")
	if err := os.WriteFile(unitBackup, []byte("old unit"), 0o644); err != nil {
		t.Fatal(err)
	}
	if am.rollback(ctx, app.BinaryPath, backup, app.UnitName, app.ConfigPath, backupAbsent, unitBackup) {
		t.Fatal("rollback must fail when the restored service cannot start")
	}
}

// TestRecoverDeploymentsUsesVersionAgnosticHealthCheck 恢复重放的复检同样不依赖
// 应用定义中的健康检查：定义里的健康检查属于新版本，用它复检会让可收敛的半完成行
// 永久停在 ROLLBACK_FAILED。
func TestRecoverDeploymentsUsesVersionAgnosticHealthCheck(t *testing.T) {
	ctx := t.Context()
	am, h, app := recoveryFixture(t)
	// 应用定义带负向健康检查（等价于“当前 Desired State 是新版本”的场景）。
	app.HealthCheck = &models.HealthCheck{Type: models.HealthTypeTCP, Target: "127.0.0.1:1", Attempts: 1, Interval: 1, Timeout: 1}
	def, _ := json.Marshal(app)
	if err := am.store.BeginSync(ctx); err != nil {
		t.Fatal(err)
	}
	if err := am.store.UpsertApplication(ctx, app.ID, def, 1); err != nil {
		am.store.RollbackSync()
		t.Fatal(err)
	}
	if err := am.store.CommitSync(); err != nil {
		t.Fatal(err)
	}
	rev, bp, cp, un := matchingBinding(app)
	dir := t.TempDir()
	backups := map[string]string{filepath.Join(dir, "binary"): "old binary", filepath.Join(dir, "unit"): "old unit"}
	for path, data := range backups {
		if err := os.WriteFile(path, []byte(data), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	if err := h.WriteFile(ctx, bp, []byte("new binary"), 0o755); err != nil {
		t.Fatal(err)
	}
	d := &DeploymentRow{
		ID: "half-done", ApplicationID: app.ID, Phase: PhaseVerifying,
		BackupPath: filepath.Join(dir, "binary"), BackupConfigPath: backupAbsent, BackupUnitPath: filepath.Join(dir, "unit"),
		AppRevision: rev, BinaryPath: bp, ConfigPath: cp, UnitName: un,
	}
	if err := am.store.CreateDeployment(ctx, d); err != nil {
		t.Fatal(err)
	}
	am.RecoverDeployments(ctx)
	if active, err := am.store.GetActiveDeployments(ctx); err != nil || len(active) != 0 {
		t.Fatalf("recovery must converge to DONE: %+v err=%v", active, err)
	}
	if got, err := h.ReadFile(ctx, bp); err != nil || string(got) != "old binary" {
		t.Fatalf("recovery did not restore binary: %q %v", got, err)
	}
	if !h.active {
		t.Fatal("recovered service must be running")
	}
}
