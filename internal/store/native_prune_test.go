package store

import (
	"context"
	"fmt"
	"path/filepath"
	"testing"
	"time"
)

func TestNativeRetentionValidate(t *testing.T) {
	valid := NativeRetention{Raw: time.Hour, OneMinute: 2 * time.Hour, FiveMinute: 3 * time.Hour, OneHour: 4 * time.Hour}
	if err := valid.Validate(); err != nil {
		t.Fatalf("valid retention rejected: %v", err)
	}
	for _, bad := range []NativeRetention{
		{Raw: 30 * time.Minute, OneMinute: time.Hour, FiveMinute: 2 * time.Hour, OneHour: 3 * time.Hour},
		{Raw: 2 * time.Hour, OneMinute: time.Hour, FiveMinute: 3 * time.Hour, OneHour: 4 * time.Hour},
		{Raw: time.Hour, OneMinute: 3 * time.Hour, FiveMinute: 2 * time.Hour, OneHour: 4 * time.Hour},
		{Raw: time.Hour, OneMinute: 2 * time.Hour, FiveMinute: 4 * time.Hour, OneHour: 3 * time.Hour},
	} {
		if err := bad.Validate(); err == nil {
			t.Fatalf("invalid retention accepted: %+v", bad)
		}
	}
}

func TestNativePruneBoundariesWithCustomRetention(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Minute)
	retention := NativeRetention{Raw: time.Hour, OneMinute: 2 * time.Hour, FiveMinute: 3 * time.Hour, OneHour: 4 * time.Hour}
	if err := st.SetNativeRetention(retention); err != nil {
		t.Fatal(err)
	}
	insertRaw := func(ts time.Time) {
		t.Helper()
		if _, err := st.DB().Exec(`INSERT INTO node_metric_samples (node_id, metric, series_key, timestamp, value, quality, dimensions_json)
			VALUES ('node-1', 'cpu.usage', 'cpu.usage', ?, 1, 1, '{}')`, formatNativeTime(ts)); err != nil {
			t.Fatal(err)
		}
	}
	insertRollup := func(tier string, ts time.Time) {
		t.Helper()
		if _, err := st.DB().Exec(`INSERT INTO node_metric_rollups
			(node_id, metric, series_key, tier, bucket_start, "count", "expected", "missing", "sum", "min", "max", "first", "last", first_ts, last_ts, quality)
			VALUES ('node-1', 'cpu.usage', 'cpu.usage', ?, ?, 1, 1, 0, 1, 1, 1, 1, 1, ?, ?, 0)`,
			tier, formatNativeTime(ts), formatNativeTime(ts), formatNativeTime(ts)); err != nil {
			t.Fatal(err)
		}
	}
	insertRaw(now.Add(-2 * time.Hour))
	insertRaw(now.Add(-30 * time.Minute))
	insertRollup(NativeTier1m, now.Add(-3*time.Hour))
	insertRollup(NativeTier1m, now.Add(-90*time.Minute))
	insertRollup(NativeTier5m, now.Add(-4*time.Hour))
	insertRollup(NativeTier5m, now.Add(-2*time.Hour))
	insertRollup(NativeTier1h, now.Add(-5*time.Hour))
	insertRollup(NativeTier1h, now.Add(-3*time.Hour))
	st.markAggregateSuccess(context.Background(), now)

	stats, err := st.PruneNodeMetricsWithOptions(ctx, now, NativePruneOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if stats.DeletedRaw != 1 || stats.Deleted1m != 1 || stats.Deleted5m != 1 || stats.Deleted1h != 1 {
		t.Fatalf("prune stats = %+v", stats)
	}
	count := func(query string) int {
		t.Helper()
		var n int
		if err := st.DB().QueryRow(query).Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	if got := count(`SELECT COUNT(*) FROM node_metric_samples`); got != 1 {
		t.Fatalf("raw rows = %d, want 1", got)
	}
	for _, tier := range []string{NativeTier1m, NativeTier5m, NativeTier1h} {
		if got := count(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier = '` + tier + `'`); got != 1 {
			t.Fatalf("%s rows = %d, want 1", tier, got)
		}
	}
}

func TestNativePruneRawWatermarkGate(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC()
	if _, err := st.DB().Exec(`INSERT INTO node_metric_samples (node_id, metric, series_key, timestamp, value, quality, dimensions_json)
		VALUES ('node-1', 'cpu.usage', 'cpu.usage', ?, 1, 1, '{}')`, formatNativeTime(now.Add(-8*24*time.Hour))); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().Exec(`INSERT INTO node_metric_rollups
		(node_id, metric, series_key, tier, bucket_start, "count", "expected", "missing", "sum", "min", "max", "first", "last", first_ts, last_ts, quality)
		VALUES ('node-1', 'cpu.usage', 'cpu.usage', '1m', ?, 1, 1, 0, 1, 1, 1, 1, 1, ?, ?, 0)`,
		formatNativeTime(now.Add(-91*24*time.Hour)), formatNativeTime(now.Add(-91*24*time.Hour)), formatNativeTime(now.Add(-91*24*time.Hour))); err != nil {
		t.Fatal(err)
	}
	stats, err := st.PruneNodeMetricsWithOptions(ctx, now, NativePruneOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if !stats.RawSkipped || stats.DeletedRaw != 0 {
		t.Fatalf("raw should be gated without watermark: %+v", stats)
	}
	if stats.Deleted1m != 1 {
		t.Fatalf("rollup prune must continue without raw watermark: %+v", stats)
	}
	st.markAggregateSuccess(context.Background(), now)
	stats, err = st.PruneNodeMetricsWithOptions(ctx, now, NativePruneOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if stats.RawSkipped || stats.DeletedRaw != 1 {
		t.Fatalf("raw not pruned after aggregate success: %+v", stats)
	}
}

func TestNativePruneBatchesBudgetAndDryRun(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC()
	const total = 2500
	for start := 0; start < total; start += 500 {
		tx, err := st.db.BeginTx(ctx, nil)
		if err != nil {
			t.Fatal(err)
		}
		for i := start; i < start+500; i++ {
			if _, err := tx.ExecContext(ctx, `INSERT INTO node_metric_samples (node_id, metric, series_key, timestamp, value, quality, dimensions_json)
				VALUES ('node-1', 'cpu.usage', 'cpu.usage', ?, 1, 1, '{}')`, formatNativeTime(now.Add(-8*24*time.Hour-time.Duration(i)*time.Second))); err != nil {
				tx.Rollback()
				t.Fatal(err)
			}
		}
		if err := tx.Commit(); err != nil {
			t.Fatal(err)
		}
	}
	st.markAggregateSuccess(context.Background(), now)
	dry, err := st.PruneNodeMetricsWithOptions(ctx, now, NativePruneOptions{DryRun: true, BatchRows: 500})
	if err != nil {
		t.Fatal(err)
	}
	if dry.DeletedRaw != total {
		t.Fatalf("dry-run raw = %d, want %d", dry.DeletedRaw, total)
	}
	if got := countNativeRows(t, st, `SELECT COUNT(*) FROM node_metric_samples`); got != total {
		t.Fatalf("dry-run deleted rows: %d", got)
	}
	stats, err := st.PruneNodeMetricsWithOptions(ctx, now, NativePruneOptions{BatchRows: 500, Budget: time.Minute, Pause: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	if stats.DeletedRaw != total || stats.Batches < total/500 {
		t.Fatalf("batched prune stats = %+v", stats)
	}
	if got := countNativeRows(t, st, `SELECT COUNT(*) FROM node_metric_samples`); got != 0 {
		t.Fatalf("raw after prune = %d", got)
	}
}

func TestNativeMaxSampleAgeUsesRetention(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	if err := st.SetNativeRetention(NativeRetention{Raw: 2 * time.Hour, OneMinute: 3 * time.Hour, FiveMinute: 4 * time.Hour, OneHour: 5 * time.Hour}); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC()
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(now.Add(-2*time.Hour-30*time.Minute), "cpu.usage", nil, 1),
	}); err != nil {
		t.Fatalf("within raw+1h rejected: %v", err)
	}
	err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(now.Add(-3*time.Hour-30*time.Minute), "cpu.usage", nil, 1),
	})
	if err == nil {
		t.Fatal("sample older than raw+1h accepted")
	}
}

func TestSQLiteNewDBAutoVacuumIncremental(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	var mode int
	if err := st.DB().QueryRow(`PRAGMA auto_vacuum`).Scan(&mode); err != nil {
		t.Fatal(err)
	}
	if mode != 2 {
		t.Fatalf("auto_vacuum = %d, want 2 (INCREMENTAL)", mode)
	}
}

func TestEnableIncrementalVacuum(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	ctx := context.Background()
	for i := 0; i < 200; i++ {
		if _, err := st.DB().ExecContext(ctx, `INSERT INTO settings (key, value) VALUES (?, ?)`, fmt.Sprintf("vac-%03d", i), "x"); err != nil {
			t.Fatal(err)
		}
	}
	if _, err := st.DB().ExecContext(ctx, `DELETE FROM settings WHERE key LIKE 'vac-%'`); err != nil {
		t.Fatal(err)
	}
	if err := st.EnableIncrementalVacuum(ctx); err != nil {
		t.Fatal(err)
	}
	var mode, free int
	if err := st.DB().QueryRow(`PRAGMA auto_vacuum`).Scan(&mode); err != nil {
		t.Fatal(err)
	}
	if err := st.DB().QueryRow(`PRAGMA freelist_count`).Scan(&free); err != nil {
		t.Fatal(err)
	}
	if mode != 2 || free != 0 {
		t.Fatalf("auto_vacuum=%d freelist=%d, want 2/0", mode, free)
	}
}

func TestNativeVacuumPagesConfig(t *testing.T) {
	st := newTestStore(t)
	if got := st.vacuumPages(); got != NativeDefaultVacuumPages {
		t.Fatalf("default vacuum pages = %d, want %d", got, NativeDefaultVacuumPages)
	}
	st.SetNativeVacuumPages(1234)
	if got := st.vacuumPages(); got != 1234 {
		t.Fatalf("configured vacuum pages = %d, want 1234", got)
	}
}

func TestNativePruneVacuumPagesCapped(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	ctx := context.Background()
	now := time.Now().UTC()
	if err := insertOldRawRows(t, st, 20000, now); err != nil {
		t.Fatal(err)
	}
	st.markAggregateSuccess(ctx, now)
	stats, err := st.PruneNodeMetricsWithOptions(ctx, now, NativePruneOptions{
		BatchRows: 10000, Budget: 10 * time.Second, Pause: time.Millisecond, VacuumPages: 50,
	})
	if err != nil {
		t.Fatal(err)
	}
	if stats.VacuumPages > 50 {
		t.Fatalf("vacuum pages = %d, want <=50", stats.VacuumPages)
	}
	if stats.VacuumPages == 0 || stats.FreelistBefore == 0 || stats.FreelistAfter >= stats.FreelistBefore {
		t.Fatalf("vacuum did not reclaim pages: %+v", stats)
	}
}

func TestDrainFreePagesDryRunBatchAndDataSafety(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	ctx := context.Background()
	now := time.Now().UTC()
	if err := insertOldRawRows(t, st, 20000, now); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().ExecContext(ctx, `INSERT INTO settings (key, value) VALUES ('keep-me', '1')`); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().ExecContext(ctx, `DELETE FROM node_metric_samples`); err != nil {
		t.Fatal(err)
	}
	dry, err := st.DrainFreePages(ctx, NativeDrainOptions{DryRun: true, MaxPages: 100, BatchPages: 25, Budget: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	if dry.PagesReclaimed != 0 || dry.FreelistBefore == 0 {
		t.Fatalf("dry-run vacuum = %+v", dry)
	}
	stats, err := st.DrainFreePages(ctx, NativeDrainOptions{MaxPages: 100, BatchPages: 25, Budget: time.Second})
	if err != nil {
		t.Fatal(err)
	}
	if stats.PagesReclaimed > 100 || stats.Batches == 0 || stats.FreelistAfter >= stats.FreelistBefore {
		t.Fatalf("drain stats = %+v", stats)
	}
	var keep int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM settings WHERE key='keep-me'`).Scan(&keep); err != nil {
		t.Fatal(err)
	}
	if keep != 1 {
		t.Fatal("vacuum deleted unrelated data")
	}
}

func TestDrainFreePagesBudget(t *testing.T) {
	st, err := OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = st.Close() })
	ctx := context.Background()
	if err := insertOldRawRows(t, st, 20000, time.Now().UTC()); err != nil {
		t.Fatal(err)
	}
	if _, err := st.DB().ExecContext(ctx, `DELETE FROM node_metric_samples`); err != nil {
		t.Fatal(err)
	}
	stats, err := st.DrainFreePages(ctx, NativeDrainOptions{MaxPages: 100000, BatchPages: 25, Budget: time.Nanosecond})
	if err != nil {
		t.Fatal(err)
	}
	if !stats.BudgetHit || stats.PagesReclaimed != 0 {
		t.Fatalf("budget drain = %+v", stats)
	}
}

func insertOldRawRows(t *testing.T, st *SQLiteStore, rows int, now time.Time) error {
	t.Helper()
	tx, err := st.db.BeginTx(context.Background(), nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	stmt, err := tx.PrepareContext(context.Background(), `INSERT INTO node_metric_samples
		(node_id, metric, series_key, timestamp, value, quality, dimensions_json)
		VALUES ('node-1', 'cpu.usage', 'cpu.usage', ?, 1, 1, '{}')`)
	if err != nil {
		return err
	}
	defer stmt.Close()
	for i := 0; i < rows; i++ {
		if _, err := stmt.ExecContext(context.Background(), formatNativeTime(now.Add(-8*24*time.Hour-time.Duration(i)*time.Second))); err != nil {
			return err
		}
	}
	return tx.Commit()
}
