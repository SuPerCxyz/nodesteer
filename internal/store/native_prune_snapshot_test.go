package store

import (
	"context"
	"os"
	"testing"
	"time"
)

// TestSnapshotPruneManual 是隔离快照保留期验证入口；默认跳过。
// 用法：
//
//	NODESTEER_PERF_DB=/tmp/.../hub.db go test ./internal/store -run TestSnapshotPruneManual -v
func TestSnapshotPruneManual(t *testing.T) {
	path := os.Getenv("NODESTEER_PERF_DB")
	if path == "" {
		t.Skip("NODESTEER_PERF_DB not set")
	}
	st, err := OpenSQLite(path)
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	ctx := context.Background()
	retention := NativeRetention{
		Raw: 24 * time.Hour, OneMinute: 7 * 24 * time.Hour,
		FiveMinute: 30 * 24 * time.Hour, OneHour: 365 * 24 * time.Hour,
	}
	if err := st.SetNativeRetention(retention); err != nil {
		t.Fatal(err)
	}
	var latestRaw string
	if err := st.DB().QueryRow(`SELECT COALESCE(MAX(timestamp), '') FROM node_metric_samples`).Scan(&latestRaw); err != nil {
		t.Fatal(err)
	}
	latest, err := parseNativeTime(latestRaw)
	if err != nil {
		t.Fatal(err)
	}
	// 快照跨度约 22.6h；用 latest+4h 模拟当前时刻，使 1d raw 保留期覆盖约 2.6h 过期数据。
	now := latest.Truncate(time.Minute).Add(4 * time.Hour)
	st.markAggregateSuccess(ctx, now)

	count := func(query string, args ...any) int64 {
		t.Helper()
		var n int64
		if err := st.DB().QueryRow(query, args...).Scan(&n); err != nil {
			t.Fatal(err)
		}
		return n
	}
	rawBefore := count(`SELECT COUNT(*) FROM node_metric_samples`)
	oneBefore := count(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier='1m' AND bucket_start >= ?`, formatNativeTime(now.Add(-retention.OneMinute)))
	fiveBefore := count(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier='5m' AND bucket_start >= ?`, formatNativeTime(now.Add(-retention.FiveMinute)))
	hourBefore := count(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier='1h' AND bucket_start >= ?`, formatNativeTime(now.Add(-retention.OneHour)))

	dry, err := st.PruneNodeMetricsWithOptions(ctx, now, NativePruneOptions{DryRun: true, BatchRows: 10000, Budget: time.Minute})
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("dry-run: raw=%d 1m=%d 5m=%d 1h=%d raw_skipped=%v duration=%v",
		dry.DeletedRaw, dry.Deleted1m, dry.Deleted5m, dry.Deleted1h, dry.RawSkipped, dry.Duration)

	start := time.Now()
	stats, err := st.PruneNodeMetricsWithOptions(ctx, now, NativePruneOptions{BatchRows: 10000, Budget: 2 * time.Minute, Pause: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	t.Logf("prune: wall=%v duration=%v raw=%d 1m=%d 5m=%d 1h=%d batches=%d busy=%d budget_hit=%v",
		time.Since(start), stats.Duration, stats.DeletedRaw, stats.Deleted1m, stats.Deleted5m, stats.Deleted1h,
		stats.Batches, stats.BusyErrors, stats.BudgetHit)

	rawAfter := count(`SELECT COUNT(*) FROM node_metric_samples`)
	oneAfter := count(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier='1m' AND bucket_start >= ?`, formatNativeTime(now.Add(-retention.OneMinute)))
	fiveAfter := count(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier='5m' AND bucket_start >= ?`, formatNativeTime(now.Add(-retention.FiveMinute)))
	hourAfter := count(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier='1h' AND bucket_start >= ?`, formatNativeTime(now.Add(-retention.OneHour)))
	t.Logf("raw before/after=%d/%d; 1m %d/%d; 5m %d/%d; 1h %d/%d",
		rawBefore, rawAfter, oneBefore, oneAfter, fiveBefore, fiveAfter, hourBefore, hourAfter)
	if rawAfter >= rawBefore {
		t.Fatalf("raw not pruned: %d -> %d", rawBefore, rawAfter)
	}
	if oneAfter != oneBefore || fiveAfter != fiveBefore || hourAfter != hourBefore {
		t.Fatal("in-retention rollups changed unexpectedly")
	}
	repeat, err := st.PruneNodeMetricsWithOptions(ctx, now, NativePruneOptions{BatchRows: 10000, Budget: time.Minute, Pause: time.Millisecond})
	if err != nil {
		t.Fatal(err)
	}
	if repeat.DeletedRaw != 0 || repeat.Deleted1m != 0 || repeat.Deleted5m != 0 || repeat.Deleted1h != 0 {
		t.Fatalf("repeat prune deleted rows: %+v", repeat)
	}
}
