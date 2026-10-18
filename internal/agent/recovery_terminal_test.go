package agent

import (
	"log/slog"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// deploymentRowByID 读取指定 journal 记录（含 DONE 终态行），供恢复终态断言使用。
func deploymentRowByID(t *testing.T, st *LocalStore, appID, id string) *DeploymentRow {
	t.Helper()
	rows, err := st.ListDeploymentsByApplication(t.Context(), appID)
	if err != nil {
		t.Fatalf("list deployments: %v", err)
	}
	for _, row := range rows {
		if row.ID == id {
			return row
		}
	}
	t.Fatalf("deployment %s not found in journal", id)
	return nil
}

// TestRecoveryClosesUnreplayableJournals CR-3：四类不可重放的 journal 行（被更晚成功部署取代、
// 绑定漂移、旧行缺绑定、准备阶段中断）在恢复后都落终态（DONE + completed_kind），离开活动
// 部署集合；第二次恢复不再重复告警；四类行仍不重放回滚（宿主文件与服务保持原状）。
func TestRecoveryClosesUnreplayableJournals(t *testing.T) {
	buf := &syncBuffer{}
	am, h, app := recoveryFixtureWithLogger(t, slog.New(slog.NewTextHandler(buf, nil)))
	ctx := t.Context()
	rev, bp, cp, un := matchingBinding(app)

	// 可读的陈旧 backup：若发生重放会把宿主文件覆盖成 "stale backup"。
	backup := filepath.Join(t.TempDir(), "stale.binary")
	if err := os.WriteFile(backup, []byte("stale backup"), 0o755); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	stamp := func(offset time.Duration) string { return now.Add(offset).Format(time.RFC3339Nano) }

	rows := []*DeploymentRow{
		{
			// 1) 已被更晚的成功部署取代（ROLLBACK_FAILED 毒行的典型场景）。
			ID: "superseded", ApplicationID: app.ID, Phase: PhaseRollbackFailed,
			BackupPath: backup, BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent,
			AppRevision: rev, BinaryPath: bp, ConfigPath: cp, UnitName: un,
			StartedAt: stamp(-30 * time.Second), UpdatedAt: stamp(-30 * time.Second),
		},
		{
			// 取代依据：更晚的成功部署（DONE + deployed），早于其余待测行。
			ID: "newer", ApplicationID: app.ID, Phase: PhaseDone, CompletedKind: CompletedKindDeployed, ToVersion: "5.0",
			StartedAt: stamp(-25 * time.Second), UpdatedAt: stamp(-25 * time.Second),
		},
		{
			// 2) 绑定漂移：app revision 与当前定义不一致。
			ID: "drifted", ApplicationID: app.ID, Phase: PhaseVerifying,
			BackupPath: backup, BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent,
			AppRevision: rev + 1, BinaryPath: bp, ConfigPath: cp, UnitName: un,
			StartedAt: stamp(-20 * time.Second), UpdatedAt: stamp(-20 * time.Second),
		},
		{
			// 3) 旧行缺绑定字段（升级前写入）。
			ID: "legacy", ApplicationID: app.ID, Phase: PhaseVerifying,
			BackupPath: backup, BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent,
			StartedAt: stamp(-10 * time.Second), UpdatedAt: stamp(-10 * time.Second),
		},
		{
			// 4) 准备阶段中断。
			ID: "preparing", ApplicationID: app.ID, Phase: PhasePreparing,
			StartedAt: stamp(-5 * time.Second), UpdatedAt: stamp(-5 * time.Second),
		},
	}
	for _, d := range rows {
		if err := am.store.CreateDeployment(ctx, d); err != nil {
			t.Fatal(err)
		}
	}

	h.active = false
	am.RecoverDeployments(ctx)

	// 不重放：陈旧 backup 未被用于覆盖，服务未被启动。
	for path, want := range map[string]string{bp: "old binary", cp: "old config", "/etc/systemd/system/" + un: "old unit"} {
		if got, err := h.ReadFile(ctx, path); err != nil || string(got) != want {
			t.Fatalf("unreplayable recovery changed %s: %q %v", path, got, err)
		}
	}
	if h.active {
		t.Fatal("unreplayable recovery must not start the service")
	}

	// 终态：四类行都离开活动集合，成因各自可辨（记录保留可查）。
	wantKinds := map[string]string{
		"superseded": CompletedKindSuperseded,
		"drifted":    CompletedKindStaleBinding,
		"legacy":     CompletedKindLegacyUnbound,
		"preparing":  CompletedKindPrepareInterrupted,
	}
	if active, err := am.store.GetActiveDeployments(ctx); err != nil || len(active) != 0 {
		t.Fatalf("unreplayable journals must leave the active set: %+v err=%v", active, err)
	}
	for id, kind := range wantKinds {
		row := deploymentRowByID(t, am.store, app.ID, id)
		if row.Phase != PhaseDone || row.CompletedKind != kind {
			t.Fatalf("%s journal = %s/%s, want DONE/%s", id, row.Phase, row.CompletedKind, kind)
		}
	}

	// 首次恢复：每条各关闭一次并汇总关闭数量，无遗留活动行。
	if got := buf.countLines("unreplayable deployment journal closed"); got != len(wantKinds) {
		t.Fatalf("close logs = %d, want %d\n%s", got, len(wantKinds), buf.String())
	}
	if got := buf.countLines("unreplayable deployment journals closed by recovery"); got != 1 {
		t.Fatalf("recovery summary = %d, want 1\n%s", got, buf.String())
	}
	if got := buf.countLines("deployment journals still active after recovery"); got != 0 {
		t.Fatalf("nothing should stay active\n%s", buf.String())
	}

	// 第二次恢复（等价于 Agent 重启）不再重复告警：终态行不在活动集合中，日志完全不变。
	firstRun := buf.String()
	am.RecoverDeployments(ctx)
	if got := buf.String(); got != firstRun {
		t.Fatalf("second recovery must not log again:\nfirst:\n%s\nsecond:\n%s", firstRun, got)
	}
}

// TestRecoveryCloseFailureKeepsJournalActive 终态写入失败时行保持活动并汇总提示，
// 下次启动可重试关闭；不因写失败而错误重放。
func TestRecoveryCloseFailureKeepsJournalActive(t *testing.T) {
	buf := &syncBuffer{}
	am, h, app := recoveryFixtureWithLogger(t, slog.New(slog.NewTextHandler(buf, nil)))
	ctx := t.Context()
	// 旧行缺绑定 → 走 legacy_unbound 关闭路径；注入 DONE 写入失败。
	if _, err := am.store.DB().Exec(`CREATE TRIGGER fail_done BEFORE UPDATE OF phase ON deployments WHEN NEW.phase = 'DONE' BEGIN SELECT RAISE(ABORT, 'injected journal failure'); END`); err != nil {
		t.Fatal(err)
	}
	d := &DeploymentRow{ID: "legacy", ApplicationID: app.ID, Phase: PhaseVerifying,
		BackupPath: backupAbsent, BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent}
	if err := am.store.CreateDeployment(ctx, d); err != nil {
		t.Fatal(err)
	}
	am.RecoverDeployments(ctx)
	if got, err := h.ReadFile(ctx, app.BinaryPath); err != nil || string(got) != "old binary" {
		t.Fatalf("close failure must not replay: %q %v", got, err)
	}
	active, err := am.store.GetActiveDeployments(ctx)
	if err != nil || len(active) != 1 || active[0].ID != "legacy" || active[0].Phase != PhaseVerifying {
		t.Fatalf("failed close must keep the row active for retry: %+v err=%v", active, err)
	}
	if got := buf.countLines("close unreplayable deployment journal failed"); got != 1 {
		t.Fatalf("close failure log = %d, want 1\n%s", got, buf.String())
	}
	if got := buf.countLines("deployment journals still active after recovery"); got != 1 {
		t.Fatalf("pending summary = %d, want 1\n%s", got, buf.String())
	}
}
