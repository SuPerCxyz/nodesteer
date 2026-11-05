package monitoring

import (
	"context"
	"errors"
	"math"
	"sort"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type fakeNativeReader struct {
	samples []store.NativeSample
	rollups map[string][]store.NativeRollup
	err     error
}

func (reader *fakeNativeReader) QueryNodeMetricSamplesLimited(_ context.Context, nodeID, metric, seriesKey string, start, end time.Time, limit int) ([]store.NativeSample, error) {
	if reader.err != nil {
		return nil, reader.err
	}
	result := make([]store.NativeSample, 0, len(reader.samples))
	for _, sample := range reader.samples {
		if sample.NodeID != "" && sample.NodeID != nodeID {
			continue
		}
		if metric != "" && sample.Metric != metric {
			continue
		}
		if seriesKey != "" && store.BuildSeriesKey(sample.Metric, sample.Dimensions) != seriesKey {
			continue
		}
		if !start.IsZero() && sample.Timestamp.Before(start) {
			continue
		}
		if !end.IsZero() && !sample.Timestamp.Before(end) {
			continue
		}
		result = append(result, sample)
		if len(result) >= limit {
			break
		}
	}
	sort.SliceStable(result, func(left, right int) bool {
		return result[left].Timestamp.Before(result[right].Timestamp)
	})
	return result, nil
}

func (reader *fakeNativeReader) QueryNodeMetricRollups(_ context.Context, nodeID, metric, seriesKey, tier string, start, end time.Time, limit int) ([]store.NativeRollup, error) {
	if reader.err != nil {
		return nil, reader.err
	}
	result := make([]store.NativeRollup, 0)
	for _, rollup := range reader.rollups[tier] {
		if rollup.NodeID != "" && rollup.NodeID != nodeID {
			continue
		}
		if metric != "" && rollup.Metric != metric {
			continue
		}
		if seriesKey != "" && rollup.SeriesKey != seriesKey {
			continue
		}
		if !start.IsZero() && rollup.BucketStart.Before(start) {
			continue
		}
		if !end.IsZero() && !rollup.BucketStart.Before(end) {
			continue
		}
		result = append(result, rollup)
		if len(result) >= limit {
			break
		}
	}
	sort.SliceStable(result, func(left, right int) bool {
		return result[left].BucketStart.Before(result[right].BucketStart)
	})
	return result, nil
}

func nativeTestSample(timestamp time.Time, metric string, value float64) store.NativeSample {
	return store.NativeSample{
		Timestamp: timestamp,
		Metric:    metric,
		Value:     &value,
		Quality:   store.NativeQualityValid,
	}
}

func nativeTestMissing(timestamp time.Time, metric string) store.NativeSample {
	return store.NativeSample{
		Timestamp: timestamp,
		Metric:    metric,
		Value:     nil,
		Quality:   store.NativeQualityMissing,
	}
}

func nativeTestRollup(timestamp time.Time, metric, tier string, count uint64, sum, minimum, maximum float64) store.NativeRollup {
	expected := uint64(tierDuration(tier) / store.NativeRawStep)
	interval := tierDuration(tier) / time.Duration(expected)
	return store.NativeRollup{
		Metric:      metric,
		SeriesKey:   store.BuildSeriesKey(metric, nil),
		Tier:        tier,
		BucketStart: timestamp,
		Count:       count,
		Expected:    expected,
		Sum:         sum,
		Min:         minimum,
		Max:         maximum,
		First:       minimum,
		Last:        maximum,
		FirstTS:     timestamp,
		LastTS:      timestamp.Add(time.Duration(expected-1) * interval),
	}
}

func tierDuration(tier string) time.Duration {
	switch tier {
	case store.NativeTier1m:
		return time.Minute
	case store.NativeTier5m:
		return 5 * time.Minute
	case store.NativeTier1h:
		return time.Hour
	default:
		return store.NativeRawStep
	}
}

func TestNativeQueryRawTierAndGaps(t *testing.T) {
	start := time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC)
	end := start.Add(time.Hour)
	reader := &fakeNativeReader{samples: []store.NativeSample{
		nativeTestSample(start, "cpu.usage", 10),
		nativeTestSample(start.Add(30*time.Second), "cpu.usage", 20),
		nativeTestSample(start.Add(5*time.Minute), "cpu.usage", 30),
	}}
	history, err := QueryNativeHistory(context.Background(), reader, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if history.Tier != store.NativeTierRaw || history.Step != store.NativeRawStep {
		t.Fatalf("tier/step = %q/%v", history.Tier, history.Step)
	}
	if history.State != "data_gap" {
		t.Fatalf("state = %q, want data_gap", history.State)
	}
	if len(history.Points) != 3 {
		t.Fatalf("points = %d", len(history.Points))
	}
	if len(history.Gaps) != 2 {
		t.Fatalf("gaps = %+v", history.Gaps)
	}
	if !history.Gaps[0].Start.Equal(start.Add(time.Minute)) || !history.Gaps[0].End.Equal(start.Add(5*time.Minute)) {
		t.Fatalf("first gap = %+v", history.Gaps[0])
	}
	if !history.Gaps[1].Start.Equal(start.Add(5*time.Minute+30*time.Second)) || !history.Gaps[1].End.Equal(end) {
		t.Fatalf("second gap = %+v", history.Gaps[1])
	}
	if history.Summary == nil || history.Summary.Count != 3 || history.Summary.Average == nil || *history.Summary.Average != 20 {
		t.Fatalf("summary = %+v", history.Summary)
	}
}

func TestNativeQueryRollupTier(t *testing.T) {
	start := time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC)
	end := start.Add(24 * time.Hour)
	reader := &fakeNativeReader{rollups: map[string][]store.NativeRollup{
		store.NativeTier1m: {
			nativeTestRollup(start, "cpu.usage", store.NativeTier1m, 2, 30, 10, 20),
			nativeTestRollup(start.Add(time.Minute), "cpu.usage", store.NativeTier1m, 2, 50, 20, 30),
			nativeTestRollup(start.Add(3*time.Minute), "cpu.usage", store.NativeTier1m, 2, 70, 30, 40),
		},
	}}
	history, err := QueryNativeHistory(context.Background(), reader, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if history.Tier != store.NativeTier1m || history.Step != time.Minute {
		t.Fatalf("tier/step = %q/%v", history.Tier, history.Step)
	}
	if len(history.Points) != 3 {
		t.Fatalf("points = %d", len(history.Points))
	}
	first := history.Points[0]
	if first.Quality != 0 || first.Count != 2 || first.Value == nil || *first.Value != 15 {
		t.Fatalf("first rollup point = %+v", first)
	}
	if first.Min == nil || *first.Min != 10 || first.Max == nil || *first.Max != 20 {
		t.Fatalf("min/max = %v/%v", first.Min, first.Max)
	}
	if len(history.Gaps) == 0 || !history.Gaps[0].Start.Equal(start.Add(2*time.Minute)) || !history.Gaps[0].End.Equal(start.Add(3*time.Minute)) {
		t.Fatalf("gaps = %+v", history.Gaps)
	}
}

func TestNativeQueryRollupIncludesPartialFirstBucket(t *testing.T) {
	start := time.Date(2026, 11, 2, 10, 0, 30, 0, time.UTC)
	end := time.Date(2026, 11, 2, 10, 3, 0, 0, time.UTC)
	reader := &fakeNativeReader{rollups: map[string][]store.NativeRollup{
		store.NativeTier1m: {
			nativeTestRollup(time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC), "cpu.usage", store.NativeTier1m, 2, 30, 10, 20),
			nativeTestRollup(time.Date(2026, 11, 2, 10, 1, 0, 0, time.UTC), "cpu.usage", store.NativeTier1m, 2, 40, 15, 25),
			nativeTestRollup(time.Date(2026, 11, 2, 10, 2, 0, 0, time.UTC), "cpu.usage", store.NativeTier1m, 2, 50, 20, 30),
		},
	}}
	history, err := QueryNativeHistory(context.Background(), reader, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end, Step: time.Minute,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(history.Points) != 3 {
		t.Fatalf("points = %d, want 3 (partial first bucket must be included)", len(history.Points))
	}
	if len(history.Gaps) != 0 || history.State != "ready" {
		t.Fatalf("gaps/state = %+v/%q", history.Gaps, history.State)
	}
}

func TestNativeQueryFallsBackToRaw(t *testing.T) {
	start := time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC)
	end := start.Add(time.Hour)
	samples := make([]store.NativeSample, 0, 120)
	for index := 0; index < 120; index++ {
		samples = append(samples, nativeTestSample(start.Add(time.Duration(index)*30*time.Second), "cpu.usage", float64(index)))
	}
	reader := &fakeNativeReader{samples: samples}
	history, err := QueryNativeHistory(context.Background(), reader, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end, Step: time.Minute,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if history.Tier != store.NativeTierRaw || history.Step != store.NativeRawStep {
		t.Fatalf("tier/step = %q/%v, want raw fallback", history.Tier, history.Step)
	}
	if len(history.Points) != 120 || history.State != "ready" {
		t.Fatalf("points/state = %d/%q", len(history.Points), history.State)
	}
}

func TestNativeQueryDownsamplesRawFallback(t *testing.T) {
	start := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	end := start.Add(24 * time.Hour)
	samples := make([]store.NativeSample, 0, 2880)
	for index := 0; index < 2880; index++ {
		samples = append(samples, nativeTestSample(start.Add(time.Duration(index)*30*time.Second), "cpu.usage", 10))
	}
	reader := &fakeNativeReader{samples: samples}
	history, err := QueryNativeHistory(context.Background(), reader, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if history.Tier != store.NativeTierRaw || history.Step != time.Minute {
		t.Fatalf("tier/step = %q/%v, want raw/downsampled 1m", history.Tier, history.Step)
	}
	if len(history.Points) != 1440 {
		t.Fatalf("points = %d, want 1440", len(history.Points))
	}
	if len(history.Gaps) != 0 || history.State != "ready" {
		t.Fatalf("gaps/state = %d/%q", len(history.Gaps), history.State)
	}
	if history.Points[0].Count != 2 || history.Points[0].Quality != 0 {
		t.Fatalf("downsampled point = %+v", history.Points[0])
	}
}

func TestNativeQueryZeroAndMissing(t *testing.T) {
	start := time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC)
	end := start.Add(2 * time.Minute)
	reader := &fakeNativeReader{samples: []store.NativeSample{
		nativeTestSample(start, "cpu.usage", 0),
		nativeTestMissing(start.Add(30*time.Second), "cpu.usage"),
		nativeTestSample(start.Add(time.Minute), "cpu.usage", 2),
	}}
	history, err := QueryNativeHistory(context.Background(), reader, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(history.Points) != 3 {
		t.Fatalf("points = %d", len(history.Points))
	}
	if history.Points[0].Value == nil || *history.Points[0].Value != 0 || history.Points[0].Quality != 1 {
		t.Fatalf("zero point = %+v", history.Points[0])
	}
	if history.Points[1].Value != nil || history.Points[1].Quality != store.NativeQualityMissing {
		t.Fatalf("missing point = %+v", history.Points[1])
	}
	if history.State != "data_gap" {
		t.Fatalf("state = %q", history.State)
	}
	if history.Summary == nil || history.Summary.Count != 2 || history.Summary.Expected != 4 || history.Summary.Missing != 2 {
		t.Fatalf("summary = %+v", history.Summary)
	}
	if history.Summary.Min == nil || *history.Summary.Min != 0 || history.Summary.Max == nil || *history.Summary.Max != 2 {
		t.Fatalf("summary min/max = %v/%v", history.Summary.Min, history.Summary.Max)
	}
}

func TestNativeQueryNoDataAndOffline(t *testing.T) {
	start := time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC)
	end := start.Add(24 * time.Hour)

	history, err := QueryNativeHistory(context.Background(), &fakeNativeReader{}, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end,
	})
	if err != nil {
		t.Fatalf("empty query: %v", err)
	}
	if history.State != "no_data" || len(history.Points) != 0 || len(history.Gaps) != 0 || history.Summary != nil {
		t.Fatalf("empty history = %+v", history)
	}
	if history.Tier != store.NativeTier1m || history.Step != time.Minute {
		t.Fatalf("empty tier/step = %q/%v", history.Tier, history.Step)
	}

	reader := &fakeNativeReader{samples: []store.NativeSample{
		nativeTestSample(start, "cpu.usage", 10),
	}}
	history, err = QueryNativeHistory(context.Background(), reader, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end, Offline: true,
	})
	if err != nil {
		t.Fatalf("offline query: %v", err)
	}
	if history.State != "agent_offline" {
		t.Fatalf("offline state = %q", history.State)
	}
}

func TestNativeQueryExplicitStep(t *testing.T) {
	start := time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC)
	end := start.Add(time.Hour)
	reader := &fakeNativeReader{rollups: map[string][]store.NativeRollup{
		store.NativeTier5m: {
			nativeTestRollup(start, "cpu.usage", store.NativeTier5m, 10, 100, 5, 15),
			nativeTestRollup(start.Add(5*time.Minute), "cpu.usage", store.NativeTier5m, 10, 120, 6, 16),
		},
	}}
	history, err := QueryNativeHistory(context.Background(), reader, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end, Step: 5 * time.Minute,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if history.Tier != store.NativeTier5m || history.Step != 5*time.Minute {
		t.Fatalf("tier/step = %q/%v", history.Tier, history.Step)
	}
}

func TestNativeQuerySelectsCoarsestTierForLongRange(t *testing.T) {
	start := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	for _, tc := range []struct {
		rangeDuration time.Duration
		wantTier      string
	}{
		{rangeDuration: 7 * 24 * time.Hour, wantTier: store.NativeTier1h},
		{rangeDuration: 30 * 24 * time.Hour, wantTier: store.NativeTier1h},
	} {
		history, err := QueryNativeHistory(context.Background(), &fakeNativeReader{}, NativeQuery{
			NodeID: "node", Metric: "cpu.usage", Start: start, End: start.Add(tc.rangeDuration),
		})
		if err != nil {
			t.Fatalf("query %v: %v", tc.rangeDuration, err)
		}
		if history.Tier != tc.wantTier {
			t.Fatalf("range %v tier = %q, want %q", tc.rangeDuration, history.Tier, tc.wantTier)
		}
	}
}

func TestNativeQueryRejectsOversizedRange(t *testing.T) {
	start := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	_, err := QueryNativeHistory(context.Background(), &fakeNativeReader{}, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: start.Add(10 * 365 * 24 * time.Hour),
	})
	if !errors.Is(err, ErrNativeRangeTooLarge) {
		t.Fatalf("error = %v, want ErrNativeRangeTooLarge", err)
	}

	// 极端范围不能让 duration 溢出后误判为小范围。
	_, err = QueryNativeHistory(context.Background(), &fakeNativeReader{}, NativeQuery{
		NodeID: "node", Metric: "cpu.usage",
		Start: time.Date(2, 1, 1, 0, 0, 0, 0, time.UTC),
		End:   time.Date(9999, 12, 31, 0, 0, 0, 0, time.UTC),
	})
	if !errors.Is(err, ErrNativeRangeTooLarge) {
		t.Fatalf("extreme range error = %v, want ErrNativeRangeTooLarge", err)
	}
}

func TestNativeQueryInvalidArguments(t *testing.T) {
	start := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	cases := []struct {
		name   string
		reader store.NativeMetricReader
		query  NativeQuery
		want   error
	}{
		{name: "nil reader", reader: nil, query: NativeQuery{NodeID: "node", Metric: "cpu.usage"}, want: nil},
		{name: "invalid metric", reader: &fakeNativeReader{}, query: NativeQuery{NodeID: "node", Metric: "bad metric"}, want: ErrNativeInvalidQuery},
		{name: "invalid range", reader: &fakeNativeReader{}, query: NativeQuery{NodeID: "node", Metric: "cpu.usage", Start: start, End: start}, want: ErrNativeInvalidRange},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := QueryNativeHistory(context.Background(), tc.reader, tc.query)
			if tc.want == nil {
				if err == nil {
					t.Fatal("expected error")
				}
				return
			}
			if !errors.Is(err, tc.want) {
				t.Fatalf("error = %v, want %v", err, tc.want)
			}
		})
	}
}

func TestNativeQueryReaderError(t *testing.T) {
	start := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	expected := errors.New("reader failed")
	_, err := QueryNativeHistory(context.Background(), &fakeNativeReader{err: expected}, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: start.Add(time.Hour),
	})
	if !errors.Is(err, expected) {
		t.Fatalf("error = %v", err)
	}
}

func TestNativeQueryInfersSlowSampleInterval(t *testing.T) {
	for _, interval := range []time.Duration{time.Minute, 5 * time.Minute} {
		t.Run(interval.String(), func(t *testing.T) {
			start := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
			const count = 12
			end := start.Add(time.Duration(count) * interval)
			samples := make([]store.NativeSample, 0, count)
			for index := 0; index < count; index++ {
				samples = append(samples, nativeTestSample(start.Add(time.Duration(index)*interval), "cpu.usage", float64(index)))
			}
			history, err := QueryNativeHistory(context.Background(), &fakeNativeReader{samples: samples}, NativeQuery{
				NodeID: "node", Metric: "cpu.usage", Start: start, End: end,
			})
			if err != nil {
				t.Fatalf("query: %v", err)
			}
			if len(history.Gaps) != 0 {
				t.Fatalf("slow sampling reported gaps: %+v", history.Gaps)
			}
			if history.State != "ready" || history.Summary == nil {
				t.Fatalf("state/summary = %q/%+v", history.State, history.Summary)
			}
			if history.Summary.Expected != count || history.Summary.Missing != 0 {
				t.Fatalf("summary = %+v", history.Summary)
			}
		})
	}
}

func TestNativeQuerySevenDayRawFallbackChunks(t *testing.T) {
	start := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	end := start.Add(7 * 24 * time.Hour)
	const count = 7 * 24 * 120 // 7d / 30s
	samples := make([]store.NativeSample, 0, count)
	for index := 0; index < count; index++ {
		samples = append(samples, nativeTestSample(start.Add(time.Duration(index)*30*time.Second), "cpu.usage", 10))
	}
	history, err := QueryNativeHistory(context.Background(), &fakeNativeReader{samples: samples}, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if history.Tier != store.NativeTierRaw || history.Step != time.Hour {
		t.Fatalf("tier/step = %q/%v, want raw/1h downsample", history.Tier, history.Step)
	}
	if len(history.Points) != 168 {
		t.Fatalf("points = %d, want 168", len(history.Points))
	}
	if history.State != "ready" {
		t.Fatalf("state = %q", history.State)
	}
}

func TestNativeQueryDownsampleRecordsMissing(t *testing.T) {
	start := time.Date(2026, 11, 2, 0, 0, 0, 0, time.UTC)
	end := start.Add(24 * time.Hour)
	samples := make([]store.NativeSample, 0, 2880)
	for index := 0; index < 2880; index++ {
		timestamp := start.Add(time.Duration(index) * 30 * time.Second)
		if index%4 == 3 {
			samples = append(samples, nativeTestMissing(timestamp, "cpu.usage"))
		} else {
			samples = append(samples, nativeTestSample(timestamp, "cpu.usage", 10))
		}
	}
	history, err := QueryNativeHistory(context.Background(), &fakeNativeReader{samples: samples}, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if history.Summary == nil {
		t.Fatal("missing summary")
	}
	if history.Summary.Expected != 2880 || history.Summary.Missing != 720 {
		t.Fatalf("summary = %+v, want expected 2880 missing 720", history.Summary)
	}
	if len(history.Points) < 2 {
		t.Fatalf("points = %d", len(history.Points))
	}
	if history.Points[1].Expected != 2 || history.Points[1].Missing != 1 || history.Points[1].Count != 1 {
		t.Fatalf("second bucket = %+v", history.Points[1])
	}
}

func TestNativeQueryRollupFirstBucketUsesFirstTimestamp(t *testing.T) {
	start := time.Date(2026, 11, 2, 10, 0, 30, 0, time.UTC)
	end := time.Date(2026, 11, 2, 10, 2, 0, 0, time.UTC)
	firstTS := time.Date(2026, 11, 2, 10, 0, 50, 0, time.UTC)
	reader := &fakeNativeReader{rollups: map[string][]store.NativeRollup{
		store.NativeTier1m: {
			{
				Metric: "cpu.usage", SeriesKey: store.BuildSeriesKey("cpu.usage", nil), Tier: store.NativeTier1m,
				BucketStart: time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC), Count: 1, Expected: 2, Missing: 1,
				Sum: 10, Min: 10, Max: 10, First: 10, Last: 10, FirstTS: firstTS, LastTS: firstTS,
			},
		},
	}}
	history, err := QueryNativeHistory(context.Background(), reader, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end, Step: time.Minute,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(history.Gaps) == 0 || !history.Gaps[0].Start.Equal(start) || !history.Gaps[0].End.Equal(firstTS) {
		t.Fatalf("first bucket gap = %+v, want [%s,%s]", history.Gaps, start, firstTS)
	}
}

func TestNativeQueryNonFiniteRollupIsMissing(t *testing.T) {
	start := time.Date(2026, 11, 2, 10, 0, 0, 0, time.UTC)
	end := start.Add(time.Hour)
	reader := &fakeNativeReader{rollups: map[string][]store.NativeRollup{
		store.NativeTier1m: {
			{
				Metric: "cpu.usage", SeriesKey: store.BuildSeriesKey("cpu.usage", nil), Tier: store.NativeTier1m,
				BucketStart: start, Count: 1, Expected: 2, Sum: math.Inf(1), Min: 1, Max: 1, First: 1, Last: 1,
				FirstTS: start, LastTS: start,
			},
		},
	}}
	history, err := QueryNativeHistory(context.Background(), reader, NativeQuery{
		NodeID: "node", Metric: "cpu.usage", Start: start, End: end, Step: time.Minute,
	})
	if err != nil {
		t.Fatalf("query: %v", err)
	}
	if len(history.Points) != 1 || history.Points[0].Value != nil || history.Points[0].Quality != store.NativeQualityMissing {
		t.Fatalf("non-finite point = %+v", history.Points)
	}
	if history.State != "data_gap" {
		t.Fatalf("state = %q", history.State)
	}
}

func TestNewMonitoringMetricUnits(t *testing.T) {
	for metric, want := range map[string]string{
		"filesystem.total_bytes": "B", "filesystem.used_bytes": "B", "memory.free_bytes": "B",
		"container.collection.discovered_count": "count", "container.collection.sampled_count": "count", "container.collection.limit": "count",
		"container.cpu.usage": "%", "container.memory.usage": "%", "container.network.rx_bps": "B/s", "container.network.tx_bps": "B/s",
	} {
		if got := nativeMetricUnit(metric); got != want {
			t.Errorf("%s = %q, want %q", metric, got, want)
		}
	}
}
