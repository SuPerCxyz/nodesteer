package store

import (
	"context"
	"fmt"
	"os"
	"testing"
	"time"
)

// TestSnapshotAggregationManual 是隔离快照验证入口；默认跳过。
// 用法：
//
//	NODESTEER_PERF_DB=/tmp/.../hub.db go test ./internal/store -run TestSnapshotAggregationManual -v
func TestSnapshotAggregationManual(t *testing.T) {
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
	var latestRaw string
	if err := st.DB().QueryRow(`SELECT COALESCE(MAX(timestamp), '') FROM node_metric_samples`).Scan(&latestRaw); err != nil {
		t.Fatal(err)
	}
	if latestRaw == "" {
		t.Skip("snapshot has no raw samples")
	}
	latest, err := parseNativeTime(latestRaw)
	if err != nil {
		t.Fatal(err)
	}
	now := latest.Truncate(time.Minute).Add(time.Minute)
	st.SetNativeLateWindow(15 * time.Minute)
	st.SetNativeAggregateBudget(10 * time.Second)

	var rawBefore int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_samples`).Scan(&rawBefore); err != nil {
		t.Fatal(err)
	}
	var series int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_series`).Scan(&series); err != nil {
		t.Fatal(err)
	}
	t.Logf("snapshot raw=%d series=%d latest=%s now=%s", rawBefore, series, latest, now)

	start := time.Now()
	if err := st.AggregateNodeMetrics(ctx, now); err != nil {
		t.Fatalf("aggregate: %v", err)
	}
	stats := st.LastNativeAggregateStats()
	t.Logf("aggregate round 1: wall=%v stats_duration=%v series=%d tx=%d busy=%d truncated=%d",
		time.Since(start), stats.Duration, stats.Series, stats.Transactions, stats.BusyErrors, stats.Truncated)
	start = time.Now()
	if err := st.AggregateNodeMetrics(ctx, now); err != nil {
		t.Fatalf("aggregate round 2: %v", err)
	}
	stats = st.LastNativeAggregateStats()
	t.Logf("aggregate round 2: wall=%v stats_duration=%v series=%d tx=%d busy=%d truncated=%d",
		time.Since(start), stats.Duration, stats.Series, stats.Transactions, stats.BusyErrors, stats.Truncated)

	from := now.Add(-40 * time.Minute)
	to := now
	if _, err := st.DB().Exec(`DELETE FROM node_metric_rollups WHERE bucket_start >= ?`, formatNativeTime(from)); err != nil {
		t.Fatal(err)
	}
	start = time.Now()
	if err := st.BackfillNativeMetrics(ctx, from, to, NativeBackfillOptions{Chunk: 15 * time.Minute, Pause: 5 * time.Millisecond}); err != nil {
		t.Fatalf("backfill: %v", err)
	}
	stats = st.LastNativeBackfillStats()
	t.Logf("backfill: wall=%v stats_duration=%v series=%d tx=%d busy=%d truncated=%d",
		time.Since(start), stats.Duration, stats.Series, stats.Transactions, stats.BusyErrors, stats.Truncated)

	for _, tier := range []string{NativeTier1m, NativeTier5m, NativeTier1h} {
		var count int
		if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier = ? AND bucket_start >= ? AND bucket_start < ?`,
			tier, formatNativeTime(from.Truncate(time.Hour)), formatNativeTime(to)).Scan(&count); err != nil {
			t.Fatal(err)
		}
		t.Logf("backfill tier %s rollups=%d", tier, count)
	}
	var rawAfter int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_samples`).Scan(&rawAfter); err != nil {
		t.Fatal(err)
	}
	if rawAfter != rawBefore {
		t.Fatalf("raw changed by backfill: %d -> %d", rawBefore, rawAfter)
	}

	// 重复回填幂等：比较第二次前后 rollup 总数。
	var beforeRepeat, afterRepeat int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_rollups`).Scan(&beforeRepeat); err != nil {
		t.Fatal(err)
	}
	if err := st.BackfillNativeMetrics(ctx, from, to, NativeBackfillOptions{Chunk: 15 * time.Minute, Pause: 0}); err != nil {
		t.Fatalf("repeat backfill: %v", err)
	}
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_rollups`).Scan(&afterRepeat); err != nil {
		t.Fatal(err)
	}
	t.Logf("repeat backfill rollups=%d -> %d", beforeRepeat, afterRepeat)
	if beforeRepeat != afterRepeat {
		t.Fatalf("repeat backfill changed rollup count: %d -> %d", beforeRepeat, afterRepeat)
	}
	fmt.Fprintln(os.Stderr, "snapshot verification complete")
}
