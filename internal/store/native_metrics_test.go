package store

import (
	"context"
	"database/sql"
	"errors"
	"fmt"
	"math"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
)

func float64Ptr(value float64) *float64 { return &value }

func nativeValidSample(timestamp time.Time, metric string, dimensions map[string]string, value float64) NativeSample {
	return NativeSample{
		Timestamp:  timestamp,
		Metric:     metric,
		Dimensions: dimensions,
		Value:      float64Ptr(value),
		Quality:    NativeQualityValid,
	}
}

func nativeMissingSample(timestamp time.Time, metric string, dimensions map[string]string) NativeSample {
	return NativeSample{
		Timestamp:  timestamp,
		Metric:     metric,
		Dimensions: dimensions,
		Value:      nil,
		Quality:    NativeQualityMissing,
	}
}

func requireNativeFloat(t *testing.T, got *float64, want float64) {
	t.Helper()
	if got == nil {
		t.Fatalf("value is nil, want %v", want)
	}
	if *got != want {
		t.Fatalf("value = %v, want %v", *got, want)
	}
}

func TestNativeMetricInsertQueryAndUpsert(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Add(-time.Hour).Truncate(time.Minute)
	dimensions := map[string]string{"mount": "/", "device": "/dev/sda1"}

	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(base, "disk.usage", dimensions, 61.5),
		nativeValidSample(base.Add(time.Minute), "disk.usage", dimensions, 62.5),
		nativeMissingSample(base.Add(2*time.Minute), "disk.usage", dimensions),
	}); err != nil {
		t.Fatalf("insert: %v", err)
	}

	seriesKey := BuildSeriesKey("disk.usage", dimensions)
	if seriesKey != "disk.usage|device=/dev/sda1,mount=/" {
		t.Fatalf("series key = %q", seriesKey)
	}
	metric, parsedDimensions, ok := ParseSeriesKey(seriesKey)
	if !ok || metric != "disk.usage" || parsedDimensions["mount"] != "/" || parsedDimensions["device"] != "/dev/sda1" {
		t.Fatalf("parse series key = %q, %v, %v", metric, parsedDimensions, ok)
	}

	samples, err := st.QueryNodeMetricSamples(ctx, "node-1", "disk.usage", seriesKey, base, base.Add(3*time.Minute))
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(samples) != 3 {
		t.Fatalf("samples = %d, want 3", len(samples))
	}
	requireNativeFloat(t, samples[0].Value, 61.5)
	if samples[2].Value != nil || samples[2].Quality != NativeQualityMissing {
		t.Fatalf("missing sample = %+v", samples[2])
	}

	// 同主键重复写入必须覆盖 value/quality/dimensions。
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(base, "disk.usage", dimensions, 99.5),
	}); err != nil {
		t.Fatalf("upsert: %v", err)
	}
	samples, err = st.QueryNodeMetricSamples(ctx, "node-1", "disk.usage", seriesKey, base, base.Add(time.Minute))
	if err != nil {
		t.Fatalf("query after upsert: %v", err)
	}
	if len(samples) != 1 {
		t.Fatalf("samples after upsert = %d", len(samples))
	}
	requireNativeFloat(t, samples[0].Value, 99.5)

	// 覆盖为缺失值。
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeMissingSample(base, "disk.usage", dimensions),
	}); err != nil {
		t.Fatalf("upsert missing: %v", err)
	}
	samples, err = st.QueryNodeMetricSamples(ctx, "node-1", "disk.usage", seriesKey, base, base.Add(time.Minute))
	if err != nil {
		t.Fatalf("query after missing upsert: %v", err)
	}
	if len(samples) != 1 || samples[0].Value != nil || samples[0].Quality != NativeQualityMissing {
		t.Fatalf("missing upsert = %+v", samples)
	}
}

func TestNativeMetricInsertValidation(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Add(-time.Minute)
	tooManyDimensions := map[string]string{}
	for index := 0; index < 17; index++ {
		tooManyDimensions[string(rune('a'+index))] = "v"
	}

	cases := []struct {
		name   string
		nodeID string
		sample NativeSample
		want   error
	}{
		{name: "empty node", nodeID: "", sample: nativeValidSample(base, "cpu.usage", nil, 1), want: ErrNativeInvalidSample},
		{name: "empty metric", nodeID: "node", sample: nativeValidSample(base, "", nil, 1), want: ErrNativeInvalidSample},
		{name: "invalid metric", nodeID: "node", sample: nativeValidSample(base, "cpu usage", nil, 1), want: ErrNativeInvalidSample},
		{name: "too many dimensions", nodeID: "node", sample: nativeValidSample(base, "cpu.usage", tooManyDimensions, 1), want: ErrNativeInvalidSample},
		{name: "long dimension key", nodeID: "node", sample: nativeValidSample(base, "cpu.usage", map[string]string{strings.Repeat("k", 65): "v"}, 1), want: ErrNativeInvalidSample},
		{name: "long dimension value", nodeID: "node", sample: nativeValidSample(base, "cpu.usage", map[string]string{"k": strings.Repeat("v", 257)}, 1), want: ErrNativeInvalidSample},
		{name: "invalid quality", nodeID: "node", sample: NativeSample{Timestamp: base, Metric: "cpu.usage", Value: float64Ptr(1), Quality: 3}, want: ErrNativeInvalidSample},
		{name: "valid without value", nodeID: "node", sample: NativeSample{Timestamp: base, Metric: "cpu.usage", Quality: NativeQualityValid}, want: ErrNativeInvalidSample},
		{name: "missing with value", nodeID: "node", sample: NativeSample{Timestamp: base, Metric: "cpu.usage", Value: float64Ptr(1), Quality: NativeQualityMissing}, want: ErrNativeInvalidSample},
		{name: "nan", nodeID: "node", sample: nativeValidSample(base, "cpu.usage", nil, math.NaN()), want: ErrNativeInvalidSample},
		{name: "inf", nodeID: "node", sample: nativeValidSample(base, "cpu.usage", nil, math.Inf(1)), want: ErrNativeInvalidSample},
		{name: "node mismatch", nodeID: "node", sample: NativeSample{NodeID: "other", Timestamp: base, Metric: "cpu.usage", Value: float64Ptr(1), Quality: NativeQualityValid}, want: ErrNativeInvalidSample},
		{name: "zero timestamp", nodeID: "node", sample: NativeSample{Metric: "cpu.usage", Value: float64Ptr(1), Quality: NativeQualityValid}, want: ErrNativeInvalidSample},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			err := st.InsertNodeMetricSamples(ctx, tc.nodeID, []NativeSample{tc.sample})
			if !errors.Is(err, tc.want) {
				t.Fatalf("error = %v, want %v", err, tc.want)
			}
		})
	}
	var count int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_samples`).Scan(&count); err != nil {
		t.Fatalf("count: %v", err)
	}
	if count != 0 {
		t.Fatalf("invalid batch inserted %d rows", count)
	}
}

func TestNativeMetricBatchLimit(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Add(-time.Minute)
	samples := make([]NativeSample, NativeMaxBatchSamples+1)
	for index := range samples {
		samples[index] = nativeValidSample(base.Add(time.Duration(index)*time.Second), "cpu.usage", nil, 1)
	}
	if err := st.InsertNodeMetricSamples(ctx, "node", samples); !errors.Is(err, ErrNativeBatchTooLarge) {
		t.Fatalf("error = %v, want ErrNativeBatchTooLarge", err)
	}
	if err := st.InsertNodeMetricSamples(ctx, "node", nil); err != nil {
		t.Fatalf("empty batch: %v", err)
	}
}

func TestNativeMetricTimeBounds(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC()

	if err := st.InsertNodeMetricSamples(ctx, "node", []NativeSample{
		nativeValidSample(now.Add(6*time.Minute), "cpu.usage", nil, 1),
	}); !errors.Is(err, ErrNativeSampleTime) {
		t.Fatalf("future error = %v", err)
	}
	if err := st.InsertNodeMetricSamples(ctx, "node", []NativeSample{
		nativeValidSample(now.Add(-8*24*time.Hour), "cpu.usage", nil, 1),
	}); !errors.Is(err, ErrNativeSampleTime) {
		t.Fatalf("old error = %v", err)
	}
	if err := st.InsertNodeMetricSamples(ctx, "node", []NativeSample{
		nativeValidSample(now.Add(-6*24*time.Hour), "cpu.usage", nil, 1),
	}); err != nil {
		t.Fatalf("accepted old sample: %v", err)
	}
}

func TestNativeMetricSeriesAndLatest(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Add(-time.Hour).Truncate(time.Minute)

	samples := []NativeSample{
		nativeValidSample(base, "cpu.usage", map[string]string{"cpu": "0"}, 10),
		nativeValidSample(base.Add(time.Minute), "cpu.usage", map[string]string{"cpu": "0"}, 20),
		nativeValidSample(base, "cpu.usage", map[string]string{"cpu": "all"}, 15),
		nativeValidSample(base, "memory.usage", nil, 45),
		nativeValidSample(base, "disk.usage", map[string]string{"mount": "/", "device": "/dev/sda1"}, 61),
		nativeValidSample(base, "disk.usage", map[string]string{"mount": "/data", "device": "/dev/sdb1"}, 70),
	}
	if err := st.InsertNodeMetricSamples(ctx, "node-1", samples); err != nil {
		t.Fatalf("insert: %v", err)
	}
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeMissingSample(base.Add(2*time.Minute), "memory.usage", nil),
	}); err != nil {
		t.Fatalf("insert missing: %v", err)
	}

	series, err := st.NodeMetricSeries(ctx, "node-1", "cpu.usage")
	if err != nil {
		t.Fatalf("series: %v", err)
	}
	if len(series) != 2 {
		t.Fatalf("series = %d, want 2", len(series))
	}
	if series[0].SeriesKey != "cpu.usage|cpu=0" || series[1].SeriesKey != "cpu.usage|cpu=all" {
		t.Fatalf("series keys = %q, %q", series[0].SeriesKey, series[1].SeriesKey)
	}
	if series[0].Dimensions["cpu"] != "0" {
		t.Fatalf("dimensions = %v", series[0].Dimensions)
	}

	latest, err := st.LatestNodeMetricSamples(ctx, "node-1")
	if err != nil {
		t.Fatalf("latest: %v", err)
	}
	if len(latest) != 5 {
		t.Fatalf("latest = %d, want 5", len(latest))
	}
	latestByKey := map[string]NativeSample{}
	for _, sample := range latest {
		latestByKey[BuildSeriesKey(sample.Metric, sample.Dimensions)] = sample
	}
	requireNativeFloat(t, latestByKey["cpu.usage|cpu=0"].Value, 20)
	if latestByKey["memory.usage"].Quality != NativeQualityMissing || latestByKey["memory.usage"].Value != nil {
		t.Fatalf("latest memory = %+v", latestByKey["memory.usage"])
	}

	// 原始样本不存在但聚合桶存在时，series 仍需返回，并从 series_key 还原维度。
	if _, err := st.DB().Exec(`INSERT INTO node_metric_rollups
		(node_id, metric, series_key, tier, bucket_start, "count", "expected", "missing", "sum", "min", "max", "first", "last", first_ts, last_ts, quality)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		"rollup-node", "disk.usage", "disk.usage|mount=/data", NativeTier1m,
		formatNativeTime(base), 1, 2, 1, 50, 50, 50, 50, 50,
		formatNativeTime(base), formatNativeTime(base), 0); err != nil {
		t.Fatalf("insert rollup: %v", err)
	}
	series, err = st.NodeMetricSeries(ctx, "rollup-node", "disk.usage")
	if err != nil {
		t.Fatalf("rollup series: %v", err)
	}
	if len(series) != 1 || series[0].Dimensions["mount"] != "/data" {
		t.Fatalf("rollup series = %+v", series)
	}
}

func TestNativeSeriesKeyRoundTrip(t *testing.T) {
	dimensions := map[string]string{
		"mount": "/var/lib/a,b",
		"note":  `a=b|c\d`,
		"empty": "",
	}
	key := BuildSeriesKey("disk.usage", dimensions)
	metric, parsed, ok := ParseSeriesKey(key)
	if !ok || metric != "disk.usage" {
		t.Fatalf("parse = %q, %v, %v", metric, parsed, ok)
	}
	for name, value := range dimensions {
		if parsed[name] != value {
			t.Fatalf("dimension %q = %q, want %q (key %q)", name, parsed[name], value, key)
		}
	}
	if BuildSeriesKey("cpu.usage", nil) != "cpu.usage" {
		t.Fatalf("empty dimensions key = %q", BuildSeriesKey("cpu.usage", nil))
	}
	if _, _, ok := ParseSeriesKey("|mount=/"); ok {
		t.Fatal("empty metric key should be invalid")
	}
}

func TestNativeMetricAggregationTiers(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Hour).Add(-3 * time.Hour)
	seriesKey := BuildSeriesKey("cpu.usage", nil)

	samples := []NativeSample{
		nativeValidSample(base, "cpu.usage", nil, 10),
		nativeValidSample(base.Add(30*time.Second), "cpu.usage", nil, 20),
		nativeValidSample(base.Add(time.Minute), "cpu.usage", nil, 30),
		nativeValidSample(base.Add(90*time.Second), "cpu.usage", nil, 40),
		nativeValidSample(base.Add(5*time.Minute), "cpu.usage", nil, 50),
	}
	if err := st.InsertNodeMetricSamples(ctx, "node-1", samples); err != nil {
		t.Fatalf("insert: %v", err)
	}
	if err := st.AggregateNodeMetrics(ctx, time.Now().UTC()); err != nil {
		t.Fatalf("aggregate: %v", err)
	}

	oneMinute, err := st.QueryNodeMetricRollups(ctx, "node-1", "cpu.usage", seriesKey, NativeTier1m, base, base.Add(10*time.Minute), 100)
	if err != nil {
		t.Fatalf("query 1m: %v", err)
	}
	if len(oneMinute) != 3 {
		t.Fatalf("1m buckets = %d, want 3", len(oneMinute))
	}
	first := oneMinute[0]
	if !first.BucketStart.Equal(base) || first.Count != 2 || first.Expected != 2 || first.Missing != 0 {
		t.Fatalf("1m bucket = %+v", first)
	}
	if first.Sum != 30 || first.Min != 10 || first.Max != 20 || first.First != 10 || first.Last != 20 || first.Quality != 0 {
		t.Fatalf("1m values = %+v", first)
	}
	if !first.FirstTS.Equal(base) || !first.LastTS.Equal(base.Add(30*time.Second)) {
		t.Fatalf("1m timestamps = %v, %v", first.FirstTS, first.LastTS)
	}
	if oneMinute[1].Count != 2 || oneMinute[2].Count != 1 {
		t.Fatalf("1m counts = %d, %d", oneMinute[1].Count, oneMinute[2].Count)
	}

	fiveMinute, err := st.QueryNodeMetricRollups(ctx, "node-1", "cpu.usage", seriesKey, NativeTier5m, base, base.Add(time.Hour), 100)
	if err != nil {
		t.Fatalf("query 5m: %v", err)
	}
	if len(fiveMinute) != 2 {
		t.Fatalf("5m buckets = %d, want 2", len(fiveMinute))
	}
	if fiveMinute[0].Count != 4 || fiveMinute[0].Expected != 5 || fiveMinute[0].Missing != 1 {
		t.Fatalf("5m first = %+v", fiveMinute[0])
	}
	if fiveMinute[0].Sum != 100 || fiveMinute[0].Min != 10 || fiveMinute[0].Max != 40 || fiveMinute[0].First != 10 || fiveMinute[0].Last != 40 {
		t.Fatalf("5m values = %+v", fiveMinute[0])
	}
	if !fiveMinute[0].FirstTS.Equal(base) || !fiveMinute[0].LastTS.Equal(base.Add(90*time.Second)) {
		t.Fatalf("5m timestamps = %v, %v", fiveMinute[0].FirstTS, fiveMinute[0].LastTS)
	}

	hourly, err := st.QueryNodeMetricRollups(ctx, "node-1", "cpu.usage", seriesKey, NativeTier1h, base, base.Add(2*time.Hour), 100)
	if err != nil {
		t.Fatalf("query 1h: %v", err)
	}
	if len(hourly) != 1 {
		t.Fatalf("1h buckets = %d, want 1", len(hourly))
	}
	if hourly[0].Count != 5 || hourly[0].Expected != 12 || hourly[0].Missing != 7 {
		t.Fatalf("1h first = %+v", hourly[0])
	}
	if hourly[0].Sum != 150 || hourly[0].Min != 10 || hourly[0].Max != 50 || hourly[0].First != 10 || hourly[0].Last != 50 {
		t.Fatalf("1h values = %+v", hourly[0])
	}
	if !hourly[0].FirstTS.Equal(base) || !hourly[0].LastTS.Equal(base.Add(5*time.Minute)) {
		t.Fatalf("1h timestamps = %v, %v", hourly[0].FirstTS, hourly[0].LastTS)
	}

	// 重复聚合必须幂等，不能把 count/sum 累加两次。
	if err := st.AggregateNodeMetrics(ctx, time.Now().UTC()); err != nil {
		t.Fatalf("second aggregate: %v", err)
	}
	oneMinute, err = st.QueryNodeMetricRollups(ctx, "node-1", "cpu.usage", seriesKey, NativeTier1m, base, base.Add(10*time.Minute), 100)
	if err != nil {
		t.Fatalf("query 1m after second aggregate: %v", err)
	}
	if len(oneMinute) != 3 || oneMinute[0].Count != 2 || oneMinute[0].Sum != 30 {
		t.Fatalf("idempotent 1m buckets = %+v", oneMinute)
	}
}

func TestNativeMetricAggregationSkipsEmptyBuckets(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Hour).Add(-2 * time.Hour)
	seriesKey := BuildSeriesKey("memory.usage", nil)

	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(base, "memory.usage", nil, 10),
		nativeValidSample(base.Add(2*time.Minute), "memory.usage", nil, 30),
	}); err != nil {
		t.Fatalf("insert: %v", err)
	}
	if err := st.AggregateNodeMetrics(ctx, time.Now().UTC()); err != nil {
		t.Fatalf("aggregate: %v", err)
	}
	rollups, err := st.QueryNodeMetricRollups(ctx, "node-1", "memory.usage", seriesKey, NativeTier1m, base, base.Add(3*time.Minute), 100)
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(rollups) != 2 {
		t.Fatalf("buckets = %d, want 2 (empty bucket must not be written)", len(rollups))
	}
	if !rollups[0].BucketStart.Equal(base) || !rollups[1].BucketStart.Equal(base.Add(2*time.Minute)) {
		t.Fatalf("bucket starts = %v, %v", rollups[0].BucketStart, rollups[1].BucketStart)
	}
}

func TestNativeMetricPrune(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC()

	insertRaw := func(timestamp time.Time, value float64) {
		if _, err := st.DB().Exec(`INSERT INTO node_metric_samples
			(node_id, metric, series_key, timestamp, value, quality, dimensions_json)
			VALUES (?, ?, ?, ?, ?, ?, ?)`,
			"node-1", "cpu.usage", "cpu.usage", formatNativeTime(timestamp), value, 1, "{}"); err != nil {
			t.Fatalf("insert raw: %v", err)
		}
	}
	insertRollup := func(tier string, bucketStart time.Time) {
		if _, err := st.DB().Exec(`INSERT INTO node_metric_rollups
			(node_id, metric, series_key, tier, bucket_start, "count", "expected", "missing", "sum", "min", "max", "first", "last", first_ts, last_ts, quality)
			VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
			"node-1", "cpu.usage", "cpu.usage", tier, formatNativeTime(bucketStart),
			1, 2, 1, 10, 10, 10, 10, 10,
			formatNativeTime(bucketStart), formatNativeTime(bucketStart), 0); err != nil {
			t.Fatalf("insert rollup: %v", err)
		}
	}

	insertRaw(now.Add(-8*24*time.Hour), 1)
	insertRaw(now.Add(-6*24*time.Hour), 2)
	insertRollup(NativeTier1m, now.Add(-91*24*time.Hour))
	insertRollup(NativeTier1m, now.Add(-89*24*time.Hour))
	insertRollup(NativeTier5m, now.Add(-366*24*time.Hour))
	insertRollup(NativeTier5m, now.Add(-364*24*time.Hour))
	insertRollup(NativeTier1h, now.Add(-731*24*time.Hour))
	insertRollup(NativeTier1h, now.Add(-729*24*time.Hour))

	if err := st.PruneNodeMetrics(ctx, now); err != nil {
		t.Fatalf("prune: %v", err)
	}

	count := func(query string, args ...any) int {
		t.Helper()
		var total int
		if err := st.DB().QueryRow(query, args...).Scan(&total); err != nil {
			t.Fatalf("count: %v", err)
		}
		return total
	}
	if got := count(`SELECT COUNT(*) FROM node_metric_samples`); got != 1 {
		t.Fatalf("raw rows = %d, want 1", got)
	}
	if got := count(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier = ?`, NativeTier1m); got != 1 {
		t.Fatalf("1m rows = %d, want 1", got)
	}
	if got := count(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier = ?`, NativeTier5m); got != 1 {
		t.Fatalf("5m rows = %d, want 1", got)
	}
	if got := count(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier = ?`, NativeTier1h); got != 1 {
		t.Fatalf("1h rows = %d, want 1", got)
	}
}

func TestNativeMetricQueryRangeAndLimit(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Add(-time.Hour).Truncate(time.Minute)
	samples := make([]NativeSample, 0, 5)
	for index := 0; index < 5; index++ {
		samples = append(samples, nativeValidSample(base.Add(time.Duration(index)*time.Minute), "load.1", nil, float64(index)))
	}
	if err := st.InsertNodeMetricSamples(ctx, "node-1", samples); err != nil {
		t.Fatalf("insert: %v", err)
	}

	ranged, err := st.QueryNodeMetricSamples(ctx, "node-1", "load.1", "", base.Add(time.Minute), base.Add(4*time.Minute))
	if err != nil {
		t.Fatalf("range query: %v", err)
	}
	if len(ranged) != 3 {
		t.Fatalf("range samples = %d, want 3", len(ranged))
	}
	requireNativeFloat(t, ranged[0].Value, 1)
	requireNativeFloat(t, ranged[2].Value, 3)

	limited, err := st.QueryNodeMetricSamplesLimited(ctx, "node-1", "load.1", "", base, base.Add(time.Hour), 2)
	if err != nil {
		t.Fatalf("limited query: %v", err)
	}
	if len(limited) != 2 {
		t.Fatalf("limited samples = %d, want 2", len(limited))
	}
}

func TestNativeMetricFixedWidthTime(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	timestamp := time.Now().UTC().Add(-time.Hour).Truncate(time.Second).Add(123456789 * time.Nanosecond)
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(timestamp, "cpu.usage", nil, 1),
	}); err != nil {
		t.Fatalf("insert: %v", err)
	}
	var stored string
	if err := st.DB().QueryRow(`SELECT timestamp FROM node_metric_samples`).Scan(&stored); err != nil {
		t.Fatalf("query timestamp: %v", err)
	}
	if len(stored) != 30 || !strings.HasSuffix(stored, "Z") {
		t.Fatalf("stored timestamp = %q (len %d)", stored, len(stored))
	}
	if stored != timestamp.Format(nativeTimeLayout) {
		t.Fatalf("stored timestamp = %q, want %q", stored, timestamp.Format(nativeTimeLayout))
	}
}

func TestNativeMetricMigration(t *testing.T) {
	st := newTestStore(t)
	var tables int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name IN ('node_metric_samples', 'node_metric_rollups')`).Scan(&tables); err != nil {
		t.Fatalf("query tables: %v", err)
	}
	if tables != 2 {
		t.Fatalf("tables = %d, want 2", tables)
	}
	var version int
	if err := st.DB().QueryRow(`SELECT MAX(version) FROM schema_migrations`).Scan(&version); err != nil {
		t.Fatalf("query version: %v", err)
	}
	if version < 23 {
		t.Fatalf("schema version = %d, want >= 23", version)
	}
}

func TestNativeMetricAggregationProcessesAllSeries(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Minute).Add(-time.Minute)
	now := base.Add(2 * time.Hour)
	const total = 6000
	const perNode = 1000
	for nodeIndex := 0; nodeIndex < total/perNode; nodeIndex++ {
		nodeID := fmt.Sprintf("node-%d", nodeIndex)
		for start := 0; start < perNode; start += NativeMaxBatchSamples {
			end := start + NativeMaxBatchSamples
			if end > perNode {
				end = perNode
			}
			batch := make([]NativeSample, 0, end-start)
			for index := start; index < end; index++ {
				id := nodeIndex*perNode + index
				batch = append(batch, nativeValidSample(base, "bulk.metric", map[string]string{"id": fmt.Sprintf("%05d", id)}, float64(index)))
			}
			if err := st.InsertNodeMetricSamples(ctx, nodeID, batch); err != nil {
				t.Fatalf("insert node %s batch %d: %v", nodeID, start, err)
			}
		}
	}
	if err := st.AggregateNodeMetrics(ctx, now); err != nil {
		t.Fatalf("aggregate: %v", err)
	}
	for _, tier := range []string{NativeTier1m, NativeTier5m, NativeTier1h} {
		var count int
		if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_rollups WHERE tier = ?`, tier).Scan(&count); err != nil {
			t.Fatalf("count %s: %v", tier, err)
		}
		if count != total {
			t.Fatalf("%s rollups = %d, want %d", tier, count, total)
		}
	}
}

func TestNativeMetricAggregationRemovesStaleRollupAfterMissingUpsert(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Minute).Add(-time.Minute)
	now := base.Add(2 * time.Hour)
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(base, "cpu.usage", nil, 10),
	}); err != nil {
		t.Fatal(err)
	}
	if err := st.AggregateNodeMetrics(ctx, now); err != nil {
		t.Fatal(err)
	}
	seriesKey := BuildSeriesKey("cpu.usage", nil)
	queryStart := base.Truncate(time.Hour)
	for _, tier := range []string{NativeTier1m, NativeTier5m, NativeTier1h} {
		rollups, err := st.QueryNodeMetricRollups(ctx, "node-1", "cpu.usage", seriesKey, tier, queryStart, now.Add(time.Hour), 10)
		if err != nil || len(rollups) != 1 {
			t.Fatalf("%s before missing upsert: %d %v", tier, len(rollups), err)
		}
	}
	// 同主键 UPSERT 为缺失后，重算窗口必须删除旧 rollup，而不是保留旧值。
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeMissingSample(base, "cpu.usage", nil),
	}); err != nil {
		t.Fatal(err)
	}
	if err := st.AggregateNodeMetrics(ctx, now); err != nil {
		t.Fatal(err)
	}
	for _, tier := range []string{NativeTier1m, NativeTier5m, NativeTier1h} {
		rollups, err := st.QueryNodeMetricRollups(ctx, "node-1", "cpu.usage", seriesKey, tier, queryStart, now.Add(time.Hour), 10)
		if err != nil {
			t.Fatal(err)
		}
		if len(rollups) != 0 {
			t.Fatalf("%s stale rollups after missing upsert: %+v", tier, rollups)
		}
	}
}

func TestNativeMetricAggregationSkipsNonFiniteBuckets(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Truncate(time.Minute).Add(-time.Minute)
	now := base.Add(2 * time.Minute)
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(base, "overflow.metric", nil, math.MaxFloat64),
		nativeValidSample(base.Add(30*time.Second), "overflow.metric", nil, math.MaxFloat64),
	}); err != nil {
		t.Fatal(err)
	}
	if err := st.AggregateNodeMetrics(ctx, now); err != nil {
		t.Fatalf("aggregate: %v", err)
	}
	rollups, err := st.QueryNodeMetricRollups(ctx, "node-1", "overflow.metric", BuildSeriesKey("overflow.metric", nil), NativeTier1m, base, now.Add(time.Hour), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(rollups) != 0 {
		t.Fatalf("non-finite sum persisted: %+v", rollups)
	}
}

func TestNativeMetricSeriesLimitAllowsExistingUpdates(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Add(-time.Minute)
	metric := "cap.metric"
	for batchIndex := 0; batchIndex < 2; batchIndex++ {
		batch := make([]NativeSample, 0, NativeMaxSeries/2)
		for index := 0; index < NativeMaxSeries/2; index++ {
			id := batchIndex*NativeMaxSeries/2 + index
			batch = append(batch, nativeValidSample(base, metric, map[string]string{"id": fmt.Sprintf("%04d", id)}, float64(id)))
		}
		if err := st.InsertNodeMetricSamples(ctx, "node-1", batch); err != nil {
			t.Fatalf("insert batch %d: %v", batchIndex, err)
		}
	}
	var count int
	if err := st.DB().QueryRow(`SELECT COUNT(DISTINCT series_key) FROM node_metric_samples WHERE node_id = ?`, "node-1").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != NativeMaxSeries {
		t.Fatalf("series = %d, want %d", count, NativeMaxSeries)
	}
	// 已有 series 的更新允许。
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(base.Add(time.Minute), metric, map[string]string{"id": "0000"}, 1),
	}); err != nil {
		t.Fatalf("existing series update rejected: %v", err)
	}
	// 新 series 超过上限拒绝。
	err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(base, metric, map[string]string{"id": "new"}, 1),
	})
	if !errors.Is(err, ErrNativeSeriesLimit) {
		t.Fatalf("new series error = %v, want ErrNativeSeriesLimit", err)
	}
	if err := st.DB().QueryRow(`SELECT COUNT(DISTINCT series_key) FROM node_metric_samples WHERE node_id = ?`, "node-1").Scan(&count); err != nil {
		t.Fatal(err)
	}
	if count != NativeMaxSeries {
		t.Fatalf("series after rejection = %d, want %d", count, NativeMaxSeries)
	}
}

func TestLatestNodeMetricSamplesForMetricsBatchFilters(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Second)
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(now.Add(-10*time.Minute), "cpu.usage", nil, 1),
		nativeValidSample(now.Add(-time.Minute), "cpu.usage", nil, 42),
		nativeValidSample(now.Add(-time.Minute), "memory.usage", nil, 70),
		nativeValidSample(now.Add(-time.Minute), "container.cpu.usage", nil, 5),
	}); err != nil {
		t.Fatal(err)
	}
	result, err := st.LatestNodeMetricSamplesForMetricsBatch(ctx, []string{"node-1"}, []string{"cpu.usage"}, now.Add(-5*time.Minute))
	if err != nil {
		t.Fatal(err)
	}
	samples := result["node-1"]
	if len(samples) != 1 || samples[0].Metric != "cpu.usage" {
		t.Fatalf("filtered samples = %+v, want only cpu.usage", samples)
	}
	requireNativeFloat(t, samples[0].Value, 42)

	// 窗口外的最新样本不应泄漏到 overview。
	result, err = st.LatestNodeMetricSamplesForMetricsBatch(ctx, []string{"node-1"}, []string{"cpu.usage"}, now.Add(-30*time.Second))
	if err != nil {
		t.Fatal(err)
	}
	if len(result["node-1"]) != 0 {
		t.Fatalf("outside-window samples = %+v", result["node-1"])
	}
}

func TestNodeMetricSampleIntervalsUsesRecentWindow(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC()
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(now.Add(-6*time.Hour), "cpu.usage", nil, 1),
		nativeValidSample(now.Add(-2*time.Minute), "cpu.usage", nil, 2),
		nativeValidSample(now.Add(-time.Minute), "cpu.usage", nil, 3),
	}); err != nil {
		t.Fatal(err)
	}
	intervals, err := st.NodeMetricSampleIntervals(ctx, []string{"node-1"})
	if err != nil {
		t.Fatal(err)
	}
	if intervals["node-1"] != time.Minute {
		t.Fatalf("interval = %v, want 1m", intervals["node-1"])
	}
}

func TestNodeMetricSeriesBatchGroupsMetrics(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC()
	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(now, "cpu.usage", map[string]string{"cpu": "all"}, 1),
		nativeValidSample(now, "cpu.usage", map[string]string{"cpu": "0"}, 2),
		nativeValidSample(now, "memory.usage", nil, 3),
	}); err != nil {
		t.Fatal(err)
	}
	result, err := st.NodeMetricSeriesBatch(ctx, "node-1", []string{"cpu.usage", "memory.usage"})
	if err != nil {
		t.Fatal(err)
	}
	if len(result["cpu.usage"]) != 2 || len(result["memory.usage"]) != 1 {
		t.Fatalf("series batch = %+v", result)
	}
}

func TestLatestMetricsBatchQueryPlanUsesIndex(t *testing.T) {
	st := newTestStore(t)
	now := time.Now().UTC()
	plan := nativeQueryPlanDetails(t, st, `SELECT node_id, metric, series_key, MAX(timestamp)
		FROM node_metric_samples
		WHERE node_id IN (?) AND metric IN (?) AND timestamp >= ?
		GROUP BY node_id, metric, series_key`,
		"node-1", "cpu.usage", formatNativeTime(now.Add(-15*time.Minute)))
	if !strings.Contains(plan, "USING INDEX") {
		t.Fatalf("overview latest query does not use an index:\n%s", plan)
	}
	if strings.Contains(plan, "SCAN node_metric_samples") {
		t.Fatalf("overview latest query performs a full table scan:\n%s", plan)
	}
}

func TestNativeMetricAggregationRecomputesLateSamples(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC().Truncate(time.Minute)
	if now.Minute() >= 59 {
		// 避免 -59min 与当前时间落在同一小时，导致 1h 桶尚未闭合。
		now = now.Add(-20 * time.Minute)
	}
	current := now.Add(-time.Minute)
	backfill15 := now.Add(-15 * time.Minute)
	backfill59 := now.Add(-59 * time.Minute)
	series := BuildSeriesKey("cpu.usage", nil)

	insert := func(timestamp time.Time, value float64) {
		t.Helper()
		if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
			nativeValidSample(timestamp, "cpu.usage", nil, value),
		}); err != nil {
			t.Fatal(err)
		}
	}
	insert(current, 10)
	if err := st.AggregateNodeMetrics(ctx, now); err != nil {
		t.Fatal(err)
	}
	insert(backfill15, 20)
	insert(backfill59, 30)
	if err := st.AggregateNodeMetrics(ctx, now); err != nil {
		t.Fatal(err)
	}

	expectBucket := func(tier string, bucket time.Time, want float64) {
		t.Helper()
		start := bucket.Truncate(time.Hour).Add(-2 * time.Hour)
		rollups, err := st.QueryNodeMetricRollups(ctx, "node-1", "cpu.usage", series, tier, start, now.Add(time.Hour), 1000)
		if err != nil {
			t.Fatal(err)
		}
		for _, rollup := range rollups {
			if rollup.BucketStart.Equal(bucket) {
				if rollup.Count == 0 || rollup.Max != want {
					t.Fatalf("%s bucket %s = %+v, want max %v", tier, bucket, rollup, want)
				}
				return
			}
		}
		t.Fatalf("%s bucket %s missing; rollups=%+v", tier, bucket, rollups)
	}
	expectBucket(NativeTier1m, backfill15, 20)
	expectBucket(NativeTier1m, backfill59, 30)
	expectBucket(NativeTier5m, backfill15.Truncate(5*time.Minute), 20)
	expectBucket(NativeTier1h, backfill59.Truncate(time.Hour), 30)

	// 重复补报必须幂等：同一时间戳 UPSERT 后重算不改变计数。
	insert(backfill15, 21)
	if err := st.AggregateNodeMetrics(ctx, now); err != nil {
		t.Fatal(err)
	}
	expectBucket(NativeTier1m, backfill15, 21)

	// 超过 1h 窗口：raw 保留，但 rollup 不再向前扩展。
	beyond := now.Add(-2 * time.Hour)
	insert(beyond, 99)
	if err := st.AggregateNodeMetrics(ctx, now); err != nil {
		t.Fatal(err)
	}
	rollups, err := st.QueryNodeMetricRollups(ctx, "node-1", "cpu.usage", series, NativeTier1m, beyond.Add(-time.Minute), beyond.Add(time.Minute), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(rollups) != 0 {
		t.Fatalf("beyond-window sample must not create rollup: %+v", rollups)
	}
	var rawCount int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_samples WHERE node_id = ? AND timestamp = ?`,
		"node-1", formatNativeTime(beyond)).Scan(&rawCount); err != nil {
		t.Fatal(err)
	}
	if rawCount != 1 {
		t.Fatalf("raw sample count = %d, want retained", rawCount)
	}
	if got := st.LateSampleCount(); got == 0 {
		t.Fatal("late sample counter not incremented")
	}
}

func TestNativeMetricAggregationExpectedUsesSampleInterval(t *testing.T) {
	for _, tc := range []struct {
		name     string
		interval time.Duration
		wantOne  uint64
		wantFive uint64
		wantHour uint64
	}{
		{name: "60s", interval: 60 * time.Second, wantOne: 1, wantFive: 5, wantHour: 12},
		{name: "300s", interval: 300 * time.Second, wantOne: 1, wantFive: 5, wantHour: 12},
	} {
		t.Run(tc.name, func(t *testing.T) {
			st := newTestStore(t)
			ctx := context.Background()
			base := time.Now().UTC().Truncate(time.Hour).Add(-2 * time.Hour)
			samples := make([]NativeSample, 0, 24)
			for index := 0; index < 12; index++ {
				samples = append(samples, nativeValidSample(base.Add(time.Duration(index)*tc.interval), "slow.metric", nil, float64(index)))
			}
			if err := st.InsertNodeMetricSamples(ctx, "node-1", samples); err != nil {
				t.Fatal(err)
			}
			if err := st.AggregateNodeMetrics(ctx, time.Now().UTC()); err != nil {
				t.Fatal(err)
			}
			oneMinute, err := st.QueryNodeMetricRollups(ctx, "node-1", "slow.metric", BuildSeriesKey("slow.metric", nil), NativeTier1m, base, base.Add(12*tc.interval), 100)
			if err != nil || len(oneMinute) == 0 {
				t.Fatalf("1m rollups = %d, %v", len(oneMinute), err)
			}
			if oneMinute[0].Expected != tc.wantOne {
				t.Fatalf("1m expected = %d, want %d (%+v)", oneMinute[0].Expected, tc.wantOne, oneMinute[0])
			}
			fiveMinute, err := st.QueryNodeMetricRollups(ctx, "node-1", "slow.metric", BuildSeriesKey("slow.metric", nil), NativeTier5m, base, base.Add(time.Hour), 100)
			if err != nil || len(fiveMinute) == 0 {
				t.Fatalf("5m rollups = %d, %v", len(fiveMinute), err)
			}
			if fiveMinute[0].Expected != tc.wantFive {
				t.Fatalf("5m expected = %d, want %d", fiveMinute[0].Expected, tc.wantFive)
			}
			hourly, err := st.QueryNodeMetricRollups(ctx, "node-1", "slow.metric", BuildSeriesKey("slow.metric", nil), NativeTier1h, base, base.Add(2*time.Hour), 100)
			if err != nil || len(hourly) == 0 {
				t.Fatalf("1h rollups = %d, %v", len(hourly), err)
			}
			if hourly[0].Expected != tc.wantHour {
				t.Fatalf("1h expected = %d, want %d", hourly[0].Expected, tc.wantHour)
			}
		})
	}
}

func TestNativeMetricLatestSamplesAndIntervalsBatch(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Add(-2 * time.Minute).Truncate(time.Second)

	if err := st.InsertNodeMetricSamples(ctx, "node-1", []NativeSample{
		nativeValidSample(base, "cpu.usage", map[string]string{"cpu": "all"}, 10),
		nativeValidSample(base.Add(time.Minute), "cpu.usage", map[string]string{"cpu": "all"}, 20),
		nativeMissingSample(base.Add(time.Minute), "memory.usage", nil),
	}); err != nil {
		t.Fatalf("insert node-1: %v", err)
	}
	if err := st.InsertNodeMetricSamples(ctx, "node-2", []NativeSample{
		nativeValidSample(base.Add(30*time.Second), "load.1", nil, 0.5),
	}); err != nil {
		t.Fatalf("insert node-2: %v", err)
	}

	latest, err := st.LatestNodeMetricSamplesBatch(ctx, []string{"node-1", "node-2", "node-empty", "node-1"})
	if err != nil {
		t.Fatalf("batch latest: %v", err)
	}
	if len(latest["node-1"]) != 2 || len(latest["node-2"]) != 1 {
		t.Fatalf("batch latest sizes = %d/%d, want 2/1", len(latest["node-1"]), len(latest["node-2"]))
	}
	if samples, ok := latest["node-empty"]; !ok || len(samples) != 0 {
		t.Fatalf("empty node latest = %v (present=%v), want empty slice", samples, ok)
	}
	latestCPU := map[string]NativeSample{}
	for _, sample := range latest["node-1"] {
		latestCPU[sample.Metric] = sample
	}
	requireNativeFloat(t, latestCPU["cpu.usage"].Value, 20)
	if latestCPU["memory.usage"].Quality != NativeQualityMissing || latestCPU["memory.usage"].Value != nil {
		t.Fatalf("batch latest memory = %+v", latestCPU["memory.usage"])
	}

	intervals, err := st.NodeMetricSampleIntervals(ctx, []string{"node-1", "node-2", "node-empty"})
	if err != nil {
		t.Fatalf("batch intervals: %v", err)
	}
	if intervals["node-1"] != time.Minute {
		t.Fatalf("node-1 interval = %v, want 1m", intervals["node-1"])
	}
	if intervals["node-2"] != NativeRawStep || intervals["node-empty"] != NativeRawStep {
		t.Fatalf("single-sample intervals = %v/%v, want %v", intervals["node-2"], intervals["node-empty"], NativeRawStep)
	}

	if _, err := st.LatestNodeMetricSamplesBatch(ctx, []string{"bad\nnode"}); !errors.Is(err, ErrNativeInvalidQuery) {
		t.Fatalf("invalid node id error = %v, want ErrNativeInvalidQuery", err)
	}
	if _, err := st.NodeMetricSampleIntervals(ctx, []string{"bad\nnode"}); !errors.Is(err, ErrNativeInvalidQuery) {
		t.Fatalf("invalid interval node id error = %v, want ErrNativeInvalidQuery", err)
	}
}

func nativeQueryPlanDetails(t *testing.T, st *SQLiteStore, query string, args ...any) string {
	t.Helper()
	rows, err := st.DB().Query("EXPLAIN QUERY PLAN "+query, args...)
	if err != nil {
		t.Fatalf("explain query plan: %v", err)
	}
	defer rows.Close()
	var details []string
	for rows.Next() {
		var id, parent, notUsed int
		var detail string
		if err := rows.Scan(&id, &parent, &notUsed, &detail); err != nil {
			t.Fatalf("scan query plan: %v", err)
		}
		details = append(details, detail)
	}
	if err := rows.Err(); err != nil {
		t.Fatalf("query plan rows: %v", err)
	}
	return strings.Join(details, "\n")
}

func TestNativeMetricQueryPlanUsesIndexes(t *testing.T) {
	st := newTestStore(t)
	now := time.Now().UTC()
	start := formatNativeTime(now.Add(-time.Hour))
	end := formatNativeTime(now)

	rawPlan := nativeQueryPlanDetails(t, st, `SELECT timestamp, value FROM node_metric_samples
		WHERE node_id = ? AND metric = ? AND series_key = ? AND timestamp >= ? AND timestamp < ?`,
		"node-1", "cpu.usage", "cpu.usage", start, end)
	if !strings.Contains(rawPlan, "USING INDEX") {
		t.Fatalf("raw query plan does not use an index:\n%s", rawPlan)
	}

	rollupPlan := nativeQueryPlanDetails(t, st, `SELECT bucket_start FROM node_metric_rollups
		WHERE node_id = ? AND tier = ? AND bucket_start >= ? AND bucket_start < ?`,
		"node-1", NativeTier1m, start, end)
	if !strings.Contains(rollupPlan, "idx_node_metric_rollups_node_tier_time") {
		t.Fatalf("rollup query plan does not use node/tier index:\n%s", rollupPlan)
	}

	latestPlan := nativeQueryPlanDetails(t, st, `SELECT node_id, metric, series_key, MAX(timestamp)
		FROM node_metric_samples WHERE node_id IN (?, ?)
		GROUP BY node_id, metric, series_key`, "node-1", "node-2")
	if !strings.Contains(latestPlan, "INDEX") {
		t.Fatalf("batch latest query plan does not use an index:\n%s", latestPlan)
	}
}

func TestDeleteNodeCleansNativeMetrics(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Add(-2 * time.Minute).Truncate(time.Second)

	for _, nodeID := range []string{"node-delete", "node-keep"} {
		if err := st.UpsertNode(ctx, &models.Node{
			ID: nodeID, AgentID: "agent-" + nodeID, Hostname: nodeID, Status: models.NodeStatusOnline,
		}); err != nil {
			t.Fatalf("create node %s: %v", nodeID, err)
		}
		if err := st.InsertNodeMetricSamples(ctx, nodeID, []NativeSample{
			nativeValidSample(base, "cpu.usage", nil, 10),
			nativeValidSample(base.Add(time.Minute), "cpu.usage", nil, 20),
		}); err != nil {
			t.Fatalf("insert samples %s: %v", nodeID, err)
		}
	}
	if err := st.AggregateNodeMetrics(ctx, base.Add(10*time.Minute)); err != nil {
		t.Fatalf("aggregate: %v", err)
	}

	count := func(table, nodeID string) int {
		t.Helper()
		var total int
		if err := st.DB().QueryRow(`SELECT COUNT(*) FROM `+table+` WHERE node_id = ?`, nodeID).Scan(&total); err != nil {
			t.Fatalf("count %s %s: %v", table, nodeID, err)
		}
		return total
	}
	if count("node_metric_samples", "node-delete") == 0 || count("node_metric_rollups", "node-delete") == 0 {
		t.Fatal("test setup must create samples and rollups for node-delete")
	}
	keptSamples := count("node_metric_samples", "node-keep")
	keptRollups := count("node_metric_rollups", "node-keep")

	if err := st.DeleteNode(ctx, "node-delete"); err != nil {
		t.Fatalf("delete node: %v", err)
	}
	if got := count("node_metric_samples", "node-delete"); got != 0 {
		t.Fatalf("deleted node samples = %d, want 0", got)
	}
	if got := count("node_metric_rollups", "node-delete"); got != 0 {
		t.Fatalf("deleted node rollups = %d, want 0", got)
	}
	if got := count("node_metric_samples", "node-keep"); got != keptSamples {
		t.Fatalf("kept node samples = %d, want %d", got, keptSamples)
	}
	if got := count("node_metric_rollups", "node-keep"); got != keptRollups {
		t.Fatalf("kept node rollups = %d, want %d", got, keptRollups)
	}
	if err := st.DeleteNode(ctx, "node-delete"); !errors.Is(err, sql.ErrNoRows) {
		t.Fatalf("second delete error = %v, want sql.ErrNoRows", err)
	}
}

func TestNativeMetricsMultiNodeRestartRecovery(t *testing.T) {
	ctx := context.Background()
	path := filepath.Join(t.TempDir(), "hub.db")
	st, err := OpenSQLite(path)
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })

	const nodeCount = 6
	const seriesPerNode = 40
	base := time.Now().UTC().Add(-5 * time.Minute).Truncate(time.Minute)
	nodeIDs := make([]string, 0, nodeCount)
	for nodeIndex := 0; nodeIndex < nodeCount; nodeIndex++ {
		nodeID := fmt.Sprintf("scale-node-%d", nodeIndex)
		nodeIDs = append(nodeIDs, nodeID)
		if err := st.UpsertNode(ctx, &models.Node{
			ID: nodeID, AgentID: "agent-" + nodeID, Hostname: nodeID, Status: models.NodeStatusOnline,
		}); err != nil {
			t.Fatalf("create node %s: %v", nodeID, err)
		}
		samples := make([]NativeSample, 0, seriesPerNode*2)
		for seriesIndex := 0; seriesIndex < seriesPerNode; seriesIndex++ {
			dimensions := map[string]string{"series": fmt.Sprintf("%02d", seriesIndex)}
			samples = append(samples,
				nativeValidSample(base, "scale.metric", dimensions, float64(seriesIndex)),
				nativeValidSample(base.Add(time.Minute), "scale.metric", dimensions, float64(seriesIndex+1)),
			)
		}
		if err := st.InsertNodeMetricSamples(ctx, nodeID, samples); err != nil {
			t.Fatalf("insert node %s: %v", nodeID, err)
		}
	}

	insertStart := time.Now()
	if err := st.AggregateNodeMetrics(ctx, base.Add(10*time.Minute)); err != nil {
		t.Fatalf("aggregate: %v", err)
	}
	aggregateElapsed := time.Since(insertStart)
	latest, err := st.LatestNodeMetricSamplesBatch(ctx, nodeIDs)
	if err != nil {
		t.Fatalf("latest before restart: %v", err)
	}
	for _, nodeID := range nodeIDs {
		if len(latest[nodeID]) != seriesPerNode {
			t.Fatalf("%s latest series = %d, want %d", nodeID, len(latest[nodeID]), seriesPerNode)
		}
	}

	if err := st.DeleteNode(ctx, nodeIDs[0]); err != nil {
		t.Fatalf("delete node: %v", err)
	}
	if err := st.Close(); err != nil {
		t.Fatalf("close store: %v", err)
	}

	reopenStart := time.Now()
	st, err = OpenSQLite(path)
	if err != nil {
		t.Fatalf("reopen store: %v", err)
	}
	reopenElapsed := time.Since(reopenStart)

	var deletedSamples, deletedRollups int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_samples WHERE node_id = ?`, nodeIDs[0]).Scan(&deletedSamples); err != nil {
		t.Fatalf("count deleted samples: %v", err)
	}
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_rollups WHERE node_id = ?`, nodeIDs[0]).Scan(&deletedRollups); err != nil {
		t.Fatalf("count deleted rollups: %v", err)
	}
	if deletedSamples != 0 || deletedRollups != 0 {
		t.Fatalf("deleted node residue = %d/%d samples/rollups, want 0/0", deletedSamples, deletedRollups)
	}

	remaining := nodeIDs[1:]
	queryStart := time.Now()
	latest, err = st.LatestNodeMetricSamplesBatch(ctx, remaining)
	if err != nil {
		t.Fatalf("latest after restart: %v", err)
	}
	queryElapsed := time.Since(queryStart)
	for _, nodeID := range remaining {
		if len(latest[nodeID]) != seriesPerNode {
			t.Fatalf("%s latest after restart = %d, want %d", nodeID, len(latest[nodeID]), seriesPerNode)
		}
	}
	rollups, err := st.QueryNodeMetricRollups(ctx, remaining[0], "scale.metric", "", NativeTier1m, base, base.Add(5*time.Minute), 100)
	if err != nil || len(rollups) != seriesPerNode*2 {
		t.Fatalf("rollups after restart = %d, %v, want %d", len(rollups), err, seriesPerNode*2)
	}
	// 重启后再次聚合必须幂等且仍可工作。
	if err := st.AggregateNodeMetrics(ctx, base.Add(20*time.Minute)); err != nil {
		t.Fatalf("aggregate after restart: %v", err)
	}
	// 重启后保留清理仍按原策略工作：原始样本过期删除，1m 聚合仍在保留期内。
	pruneNow := base.Add(NativeRawRetention + time.Hour)
	if err := st.PruneNodeMetrics(ctx, pruneNow); err != nil {
		t.Fatalf("prune after restart: %v", err)
	}
	var remainingSamples int
	if err := st.DB().QueryRow(`SELECT COUNT(*) FROM node_metric_samples WHERE node_id = ?`, remaining[0]).Scan(&remainingSamples); err != nil {
		t.Fatalf("count samples after prune: %v", err)
	}
	if remainingSamples != 0 {
		t.Fatalf("raw samples after prune = %d, want 0", remainingSamples)
	}
	rollups, err = st.QueryNodeMetricRollups(ctx, remaining[0], "scale.metric", "", NativeTier1m, base, base.Add(5*time.Minute), 100)
	if err != nil || len(rollups) != seriesPerNode*2 {
		t.Fatalf("1m rollups after prune = %d, %v, want %d", len(rollups), err, seriesPerNode*2)
	}

	t.Logf("scale test: nodes=%d series/node=%d samples=%d aggregate=%v reopen=%v latest-batch=%v",
		nodeCount, seriesPerNode, nodeCount*seriesPerNode*2, aggregateElapsed, reopenElapsed, queryElapsed)
}

func TestContainerCompletenessTransactionRejectedAtSeriesLimit(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	base := time.Now().UTC().Add(-time.Minute)
	names := []string{"container.collection.discovered_count", "container.collection.sampled_count", "container.collection.limit"}
	var initial []NativeSample
	for _, name := range names {
		initial = append(initial, nativeValidSample(base, name, nil, 1))
	}
	for i := 0; i < NativeMaxSeries-len(names); i++ {
		initial = append(initial, nativeValidSample(base, "cap.metric", map[string]string{"id": fmt.Sprint(i)}, 1))
	}
	for start := 0; start < len(initial); start += 500 {
		end := start + 500
		if end > len(initial) {
			end = len(initial)
		}
		if err := st.InsertNodeMetricSamples(ctx, "node-1", initial[start:end]); err != nil {
			t.Fatal(err)
		}
	}
	next := base.Add(time.Second)
	batch := []NativeSample{nativeValidSample(next, "container.cpu.usage", map[string]string{"container": "same", "container_id": "new"}, 10)}
	for _, name := range names {
		batch = append(batch, nativeValidSample(next, name, nil, 2))
	}
	if err := st.InsertNodeMetricSamples(ctx, "node-1", batch); !errors.Is(err, ErrNativeSeriesLimit) {
		t.Fatalf("expected series limit, got %v", err)
	}
	latest, err := st.LatestNodeMetricSamples(ctx, "node-1")
	if err != nil {
		t.Fatal(err)
	}
	for _, sample := range latest {
		for _, name := range names {
			if sample.Metric == name && !sample.Timestamp.Equal(base) {
				t.Fatalf("rejected batch updated completeness: %+v", sample)
			}
		}
	}
}

func TestContainerSameNameAndLegacyRemainDistinct(t *testing.T) {
	st := newTestStore(t)
	ctx := context.Background()
	now := time.Now().UTC().Add(-time.Minute)
	metric := "container.cpu.usage"
	first := map[string]string{"container": "web", "container_id": "first"}
	second := map[string]string{"container": "web", "container_id": "second"}
	legacy := map[string]string{"container": "web"}
	samples := []NativeSample{nativeValidSample(now, metric, first, 150), nativeValidSample(now, metric, second, 10), nativeValidSample(now, metric, legacy, 20)}
	if err := st.InsertNodeMetricSamples(ctx, "node-1", samples); err != nil {
		t.Fatal(err)
	}
	series, err := st.NodeMetricSeries(ctx, "node-1", metric)
	if err != nil {
		t.Fatal(err)
	}
	if len(series) != 3 {
		t.Fatalf("series = %+v", series)
	}
	for _, sample := range samples {
		points, err := st.QueryNodeMetricSamples(ctx, "node-1", metric, BuildSeriesKey(metric, sample.Dimensions), now.Add(-time.Second), now.Add(time.Second))
		if err != nil || len(points) != 1 || *points[0].Value != *sample.Value {
			t.Fatalf("query %v: %+v %v", sample.Dimensions, points, err)
		}
	}
}
