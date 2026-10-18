package agent

import (
	"encoding/json"
	"log/slog"
	"os"
	"path/filepath"
	"reflect"
	"testing"
	"time"
)

// readDeploymentsSummary 读取并解析 <data_dir>/deployments-summary.json。
func readDeploymentsSummary(t *testing.T, st *LocalStore) *deploymentsSummary {
	t.Helper()
	path := filepath.Join(st.Dir(), deploymentsSummaryFileName)
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read deployments summary: %v", err)
	}
	var summary deploymentsSummary
	if err := json.Unmarshal(data, &summary); err != nil {
		t.Fatalf("decode deployments summary: %v\n%s", err, data)
	}
	t.Logf("deployments summary: %s", data)
	return &summary
}

// TestRecoveryWritesDeploymentsSummary 恢复完成后生成只读汇总：
// 非 deployed 的 DONE 行按成因计数（含本次恢复关闭的四类行与历史终态、缺成因旧行），
// 仍活动的行按 id/应用/阶段列出；成功部署不计入。
func TestRecoveryWritesDeploymentsSummary(t *testing.T) {
	am, _, app := recoveryFixture(t)
	ctx := t.Context()
	rev, bp, cp, un := matchingBinding(app)

	backup := filepath.Join(t.TempDir(), "stale.binary")
	if err := os.WriteFile(backup, []byte("stale backup"), 0o755); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	stamp := func(offset time.Duration) string { return now.Add(offset).Format(time.RFC3339Nano) }

	rows := []*DeploymentRow{
		// 本次恢复会关闭的四类不可重放行。
		{ID: "superseded", ApplicationID: app.ID, Phase: PhaseRollbackFailed,
			BackupPath: backup, BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent,
			AppRevision: rev, BinaryPath: bp, ConfigPath: cp, UnitName: un,
			StartedAt: stamp(-40 * time.Second), UpdatedAt: stamp(-40 * time.Second)},
		{ID: "newer", ApplicationID: app.ID, Phase: PhaseDone, CompletedKind: CompletedKindDeployed, ToVersion: "5.0",
			StartedAt: stamp(-35 * time.Second), UpdatedAt: stamp(-35 * time.Second)},
		{ID: "drifted", ApplicationID: app.ID, Phase: PhaseVerifying,
			BackupPath: backup, BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent,
			AppRevision: rev + 1, BinaryPath: bp, ConfigPath: cp, UnitName: un,
			StartedAt: stamp(-30 * time.Second), UpdatedAt: stamp(-30 * time.Second)},
		{ID: "legacy", ApplicationID: app.ID, Phase: PhaseVerifying,
			BackupPath: backup, BackupConfigPath: backupAbsent, BackupUnitPath: backupAbsent,
			StartedAt: stamp(-20 * time.Second), UpdatedAt: stamp(-20 * time.Second)},
		{ID: "preparing", ApplicationID: app.ID, Phase: PhasePreparing,
			StartedAt: stamp(-10 * time.Second), UpdatedAt: stamp(-10 * time.Second)},
		// 应用定义缺失：保持活动，应出现在 active 列表。
		{ID: "missing-app", ApplicationID: "missing-app-def", Phase: PhaseRollingBack,
			StartedAt: stamp(-5 * time.Second), UpdatedAt: stamp(-5 * time.Second)},
		// 历史终态（本次恢复不处置，只参与计数）。
		{ID: "hist-rolled", ApplicationID: app.ID, Phase: PhaseDone, CompletedKind: CompletedKindRolledBack,
			StartedAt: stamp(-60 * time.Second), UpdatedAt: stamp(-60 * time.Second)},
		{ID: "hist-prepare-failed", ApplicationID: app.ID, Phase: PhaseDone, CompletedKind: CompletedKindPrepareFailed,
			StartedAt: stamp(-61 * time.Second), UpdatedAt: stamp(-61 * time.Second)},
		{ID: "hist-legacy-done", ApplicationID: app.ID, Phase: PhaseDone,
			StartedAt: stamp(-62 * time.Second), UpdatedAt: stamp(-62 * time.Second)},
		{ID: "hist-deployed", ApplicationID: app.ID, Phase: PhaseDone, CompletedKind: CompletedKindDeployed,
			StartedAt: stamp(-63 * time.Second), UpdatedAt: stamp(-63 * time.Second)},
	}
	for _, d := range rows {
		if err := am.store.CreateDeployment(ctx, d); err != nil {
			t.Fatal(err)
		}
	}

	am.RecoverDeployments(ctx)

	summary := readDeploymentsSummary(t, am.store)
	if _, err := time.Parse(time.RFC3339, summary.GeneratedAt); err != nil {
		t.Fatalf("generated_at = %q, want RFC3339: %v", summary.GeneratedAt, err)
	}
	wantKinds := map[string]int{
		CompletedKindSuperseded:         1,
		CompletedKindStaleBinding:       1,
		CompletedKindLegacyUnbound:      1,
		CompletedKindPrepareInterrupted: 1,
		CompletedKindRolledBack:         1,
		CompletedKindPrepareFailed:      1,
		"unknown":                       1,
	}
	if !reflect.DeepEqual(summary.DoneByKind, wantKinds) {
		t.Fatalf("done_by_kind = %v, want %v", summary.DoneByKind, wantKinds)
	}
	if _, ok := summary.DoneByKind[CompletedKindDeployed]; ok {
		t.Fatalf("successful deployment must not be counted: %v", summary.DoneByKind)
	}

	if len(summary.Active) != 1 {
		t.Fatalf("active = %+v, want exactly the missing-app row", summary.Active)
	}
	got := summary.Active[0]
	if got.ID != "missing-app" || got.ApplicationID != "missing-app-def" || got.Phase != PhaseRollingBack {
		t.Fatalf("active row = %+v, want missing-app/missing-app-def/%s", got, PhaseRollingBack)
	}
	if got.StartedAt == "" {
		t.Fatalf("active row missing started_at: %+v", got)
	}
}

// TestDeploymentsSummaryEmptyState 无任何 journal 行时也生成文件：
// 空计数与空数组（而非 null），运维读到的是确定的空态。
func TestDeploymentsSummaryEmptyState(t *testing.T) {
	am, _, _ := recoveryFixture(t)

	am.RecoverDeployments(t.Context())

	summary := readDeploymentsSummary(t, am.store)
	if summary.DoneByKind == nil || len(summary.DoneByKind) != 0 {
		t.Fatalf("done_by_kind = %#v, want empty non-nil map", summary.DoneByKind)
	}
	if summary.Active == nil || len(summary.Active) != 0 {
		t.Fatalf("active = %#v, want empty non-nil array", summary.Active)
	}
}

// TestDeploymentsSummaryRefreshedOnEachRecovery 每次恢复都覆盖刷新快照：
// 第二次恢复应反映第一次之后新增的终态行。
func TestDeploymentsSummaryRefreshedOnEachRecovery(t *testing.T) {
	am, _, app := recoveryFixture(t)
	ctx := t.Context()

	am.RecoverDeployments(ctx)
	if summary := readDeploymentsSummary(t, am.store); len(summary.DoneByKind) != 0 {
		t.Fatalf("first summary should be empty: %v", summary.DoneByKind)
	}

	if err := am.store.CreateDeployment(ctx, &DeploymentRow{
		ID: "later-rolled", ApplicationID: app.ID, Phase: PhasePreparing,
		StartedAt: time.Now().UTC().Format(time.RFC3339Nano),
	}); err != nil {
		t.Fatal(err)
	}
	if err := am.store.CompleteDeployment(ctx, "later-rolled", CompletedKindRolledBack, ""); err != nil {
		t.Fatal(err)
	}

	am.RecoverDeployments(ctx)
	summary := readDeploymentsSummary(t, am.store)
	if summary.DoneByKind[CompletedKindRolledBack] != 1 {
		t.Fatalf("second summary not refreshed: %v", summary.DoneByKind)
	}
}

// TestDeploymentsSummaryWriteFailureDoesNotBreakRecovery 汇总文件写入失败（路径被占位）时：
// 恢复照常关闭不可重放行、记错误日志、清理临时文件；下次恢复重试。
func TestDeploymentsSummaryWriteFailureDoesNotBreakRecovery(t *testing.T) {
	buf := &syncBuffer{}
	am, _, app := recoveryFixtureWithLogger(t, slog.New(slog.NewTextHandler(buf, nil)))
	ctx := t.Context()

	// 用同名目录占位，使 rename 替换失败。
	blocked := filepath.Join(am.store.Dir(), deploymentsSummaryFileName)
	if err := os.Mkdir(blocked, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := am.store.CreateDeployment(ctx, &DeploymentRow{
		ID: "legacy", ApplicationID: app.ID, Phase: PhaseVerifying,
		StartedAt: time.Now().UTC().Format(time.RFC3339Nano),
	}); err != nil {
		t.Fatal(err)
	}

	am.RecoverDeployments(ctx)

	row := deploymentRowByID(t, am.store, app.ID, "legacy")
	if row.Phase != PhaseDone || row.CompletedKind != CompletedKindLegacyUnbound {
		t.Fatalf("recovery did not close journal despite summary failure: %s/%s", row.Phase, row.CompletedKind)
	}
	if got := buf.countLines("replace deployments summary failed"); got != 1 {
		t.Fatalf("summary failure log = %d, want 1\n%s", got, buf.String())
	}
	if _, err := os.Stat(blocked + ".tmp"); !os.IsNotExist(err) {
		t.Fatalf("temporary summary file not cleaned up: %v", err)
	}
}

// TestCountClosedDeploymentsByKind 只统计非 deployed 的 DONE 行：
// 成功部署与活动行不计入，旧行空成因保留空键由汇总侧归一。
func TestCountClosedDeploymentsByKind(t *testing.T) {
	st := newTestAgentStore(t)
	ctx := t.Context()
	mk := func(id, phase, kind string) {
		t.Helper()
		if err := st.CreateDeployment(ctx, &DeploymentRow{
			ID: id, ApplicationID: "count-app", Phase: phase,
			StartedAt: time.Now().UTC().Format(time.RFC3339Nano),
		}); err != nil {
			t.Fatal(err)
		}
		if kind != "" {
			if err := st.CompleteDeployment(ctx, id, kind, ""); err != nil {
				t.Fatal(err)
			}
		}
	}
	mk("deployed", PhasePreparing, CompletedKindDeployed)
	mk("rolled", PhasePreparing, CompletedKindRolledBack)
	mk("superseded", PhasePreparing, CompletedKindSuperseded)
	mk("unknown", PhaseDone, "")
	mk("active", PhaseVerifying, "")

	counts, err := st.CountClosedDeploymentsByKind(ctx)
	if err != nil {
		t.Fatal(err)
	}
	want := map[string]int{
		CompletedKindRolledBack: 1,
		CompletedKindSuperseded: 1,
		"":                      1,
	}
	if !reflect.DeepEqual(counts, want) {
		t.Fatalf("counts = %v, want %v", counts, want)
	}
}
