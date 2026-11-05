package hubserver

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func nativeProbeStore(t *testing.T) (*store.SQLiteStore, *hub.NodeManager) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { st.Close() })
	return st, hub.NewNodeManager(st, hub.NewRevisionManager(st))
}

func nativeProbeNode(t *testing.T, st *store.SQLiteStore, status string) *models.Node {
	t.Helper()
	node := &models.Node{ID: "node", AgentID: "agent", Hostname: "node", Status: status, Capabilities: map[string]bool{models.CapNodeMetrics: true}}
	if err := st.UpsertNode(t.Context(), node); err != nil {
		t.Fatal(err)
	}
	return node
}

func nativeProbeSample(timestamp time.Time, value float64, quality int) store.NativeSample {
	sample := store.NativeSample{
		Timestamp:  timestamp.UTC(),
		Metric:     "cpu.usage",
		Dimensions: map[string]string{"cpu": "all"},
		Quality:    quality,
	}
	if quality == store.NativeQualityValid {
		sample.Value = &value
	}
	return sample
}

func nativeProbeTarget(nodeID string, maxAgeSec int) monitoring.Target {
	return monitoring.Target{
		Name:            "CPU",
		Kind:            "node_metric",
		MonitoredNodeID: nodeID,
		Metric:          "cpu.usage",
		Series:          store.BuildSeriesKey("cpu.usage", map[string]string{"cpu": "all"}),
		IntervalSec:     10,
		TimeoutSec:      2,
		MaxAgeSec:       maxAgeSec,
		Enabled:         true,
	}
}

func TestNativeNodeMetricProbeReturnsRawSamples(t *testing.T) {
	st, nodes := nativeProbeStore(t)
	node := nativeProbeNode(t, st, models.NodeStatusOnline)
	now := time.Now().UTC().Truncate(time.Millisecond)
	if err := st.InsertNodeMetricSamples(t.Context(), node.ID, []store.NativeSample{
		nativeProbeSample(now.Add(-60*time.Second), 10, store.NativeQualityValid),
		nativeProbeSample(now.Add(-30*time.Second), 20, store.NativeQualityValid),
		nativeProbeSample(now.Add(-5*time.Second), 95, store.NativeQualityValid),
	}); err != nil {
		t.Fatal(err)
	}

	observation, err := observeNativeNodeMetric(t.Context(), nodes, st, nativeProbeTarget(node.ID, 60), now)
	if err != nil {
		t.Fatalf("probe failed: %v", err)
	}
	if !observation.HasValue || observation.Value != json.Number("95") || !observation.ObservedAt.Equal(now.Add(-5*time.Second)) {
		t.Fatalf("observation = %+v", observation)
	}
	if len(observation.Samples) != 3 {
		t.Fatalf("samples = %+v", observation.Samples)
	}
	for index := 1; index < len(observation.Samples); index++ {
		if !observation.Samples[index-1].ObservedAt.Before(observation.Samples[index].ObservedAt) {
			t.Fatalf("samples not sorted: %+v", observation.Samples)
		}
	}
	last := observation.Samples[len(observation.Samples)-1]
	if !last.Valid || !last.ObservedAt.Equal(observation.ObservedAt) || last.Value != observation.Value {
		t.Fatalf("last sample does not match latest: %+v", last)
	}
	if !observation.ContinuityStart.IsZero() {
		t.Fatalf("unexpected continuity start %s", observation.ContinuityStart)
	}
}

func TestNativeNodeMetricProbeContinuityFromGap(t *testing.T) {
	st, nodes := nativeProbeStore(t)
	node := nativeProbeNode(t, st, models.NodeStatusOnline)
	now := time.Now().UTC().Truncate(time.Millisecond)
	if err := st.InsertNodeMetricSamples(t.Context(), node.ID, []store.NativeSample{
		nativeProbeSample(now.Add(-4*time.Minute), 10, store.NativeQualityValid),
		nativeProbeSample(now.Add(-3*time.Minute), 20, store.NativeQualityValid),
		nativeProbeSample(now.Add(-10*time.Second), 95, store.NativeQualityValid),
	}); err != nil {
		t.Fatal(err)
	}

	observation, err := observeNativeNodeMetric(t.Context(), nodes, st, nativeProbeTarget(node.ID, 300), now)
	if err != nil {
		t.Fatalf("probe failed: %v", err)
	}
	if !observation.ContinuityStart.Equal(now.Add(-10 * time.Second)) {
		t.Fatalf("continuity start = %s, want latest gap end %s", observation.ContinuityStart, now.Add(-10*time.Second))
	}
}

func TestNativeNodeMetricProbeRejectsStaleAndFutureSamples(t *testing.T) {
	for _, tc := range []struct {
		name      string
		latest    time.Time
		maxAgeSec int
	}{
		{name: "stale", latest: time.Now().UTC().Add(-2 * time.Minute), maxAgeSec: 60},
		{name: "future", latest: time.Now().UTC().Add(time.Minute), maxAgeSec: 60},
	} {
		t.Run(tc.name, func(t *testing.T) {
			st, nodes := nativeProbeStore(t)
			node := nativeProbeNode(t, st, models.NodeStatusOnline)
			if err := st.InsertNodeMetricSamples(t.Context(), node.ID, []store.NativeSample{
				nativeProbeSample(tc.latest, 95, store.NativeQualityValid),
			}); err != nil {
				t.Fatal(err)
			}
			if _, err := observeNativeNodeMetric(t.Context(), nodes, st, nativeProbeTarget(node.ID, tc.maxAgeSec), time.Now().UTC()); err == nil {
				t.Fatal("stale/future sample accepted")
			}
		})
	}
}

func TestNativeNodeMetricProbeRejectsMissingSamples(t *testing.T) {
	st, nodes := nativeProbeStore(t)
	node := nativeProbeNode(t, st, models.NodeStatusOnline)
	if _, err := observeNativeNodeMetric(t.Context(), nodes, st, nativeProbeTarget(node.ID, 60), time.Now().UTC()); err == nil {
		t.Fatal("missing series accepted")
	}
}

func TestNativeNodeMetricProbeRejectsMissingLatestQuality(t *testing.T) {
	st, nodes := nativeProbeStore(t)
	node := nativeProbeNode(t, st, models.NodeStatusOnline)
	now := time.Now().UTC().Truncate(time.Millisecond)
	if err := st.InsertNodeMetricSamples(t.Context(), node.ID, []store.NativeSample{
		nativeProbeSample(now.Add(-40*time.Second), 10, store.NativeQualityValid),
		nativeProbeSample(now.Add(-5*time.Second), 0, store.NativeQualityMissing),
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := observeNativeNodeMetric(t.Context(), nodes, st, nativeProbeTarget(node.ID, 60), now); err == nil {
		t.Fatal("missing latest sample accepted")
	}
}

func TestNativeNodeMetricProbeRejectsOfflineNode(t *testing.T) {
	st, nodes := nativeProbeStore(t)
	node := nativeProbeNode(t, st, models.NodeStatusOffline)
	now := time.Now().UTC().Truncate(time.Millisecond)
	if err := st.InsertNodeMetricSamples(t.Context(), node.ID, []store.NativeSample{
		nativeProbeSample(now.Add(-5*time.Second), 95, store.NativeQualityValid),
	}); err != nil {
		t.Fatal(err)
	}
	if _, err := observeNativeNodeMetric(t.Context(), nodes, st, nativeProbeTarget(node.ID, 60), now); err == nil {
		t.Fatal("offline node accepted")
	}
}

func TestNativeNodeMetricProbeDrivesRuleClaim(t *testing.T) {
	st, nodes := nativeProbeStore(t)
	node := nativeProbeNode(t, st, models.NodeStatusOnline)
	repo := monitoring.NewRepository(st.DB())
	target := nativeProbeTarget(node.ID, 60)
	if err := repo.Create(t.Context(), &target); err != nil {
		t.Fatal(err)
	}
	rule := monitoring.Rule{Name: "high CPU", TargetID: target.ID, ScheduleID: "schedule", Operator: "gt", Expected: "90", Enabled: true}
	if err := repo.CreateRule(t.Context(), &rule); err != nil {
		t.Fatal(err)
	}
	now := time.Now().UTC().Truncate(time.Millisecond)
	if _, err := st.DB().Exec(`UPDATE monitoring_rules SET not_before=? WHERE id=?`, now.Add(-time.Minute).Format(time.RFC3339Nano), rule.ID); err != nil {
		t.Fatal(err)
	}
	if err := st.InsertNodeMetricSamples(t.Context(), node.ID, []store.NativeSample{
		nativeProbeSample(now.Add(-40*time.Second), 10, store.NativeQualityValid),
		nativeProbeSample(now.Add(-20*time.Second), 95, store.NativeQualityValid),
		nativeProbeSample(now.Add(-5*time.Second), 96, store.NativeQualityValid),
	}); err != nil {
		t.Fatal(err)
	}

	observation, err := observeNativeNodeMetric(t.Context(), nodes, st, target, now)
	if err != nil {
		t.Fatalf("probe failed: %v", err)
	}
	claim, err := repo.ProcessNodeSamples(t.Context(), rule, target, observation, true)
	if err != nil || claim == "" {
		t.Fatalf("native observation did not trigger rule: claim=%q err=%v", claim, err)
	}
	events, err := repo.RuleEvents(t.Context(), rule.ID)
	if err != nil || len(events) != 1 || events[0].Status != "pending" {
		t.Fatalf("events=%+v err=%v", events, err)
	}
	var stored struct {
		Value    json.Number `json:"value"`
		HasValue bool        `json:"has_value"`
	}
	if err := json.Unmarshal(events[0].Observation, &stored); err != nil {
		t.Fatal(err)
	}
	if !stored.HasValue || stored.Value != json.Number("96") {
		t.Fatalf("event observation = %s", events[0].Observation)
	}
}

func TestNativeNodeMetricProbeSupportsSlowSampling(t *testing.T) {
	for _, interval := range []time.Duration{time.Minute, 5 * time.Minute} {
		t.Run(interval.String(), func(t *testing.T) {
			st, nodes := nativeProbeStore(t)
			node := nativeProbeNode(t, st, models.NodeStatusOnline)
			now := time.Now().UTC().Truncate(time.Millisecond)
			samples := make([]store.NativeSample, 0, 4)
			for index := 3; index >= 0; index-- {
				samples = append(samples, nativeProbeSample(now.Add(-time.Duration(index)*interval), 95, store.NativeQualityValid))
			}
			if err := st.InsertNodeMetricSamples(t.Context(), node.ID, samples); err != nil {
				t.Fatal(err)
			}
			observation, err := observeNativeNodeMetric(t.Context(), nodes, st, nativeProbeTarget(node.ID, 60), now)
			if err != nil {
				t.Fatalf("probe failed for %s sampling: %v", interval, err)
			}
			if !observation.HasValue || len(observation.Samples) < 3 {
				t.Fatalf("observation = %+v", observation)
			}
			if !observation.ContinuityStart.IsZero() {
				t.Fatalf("slow sampling continuity reset: %s", observation.ContinuityStart)
			}
			if !observation.Samples[len(observation.Samples)-1].ObservedAt.Equal(observation.ObservedAt) {
				t.Fatalf("last sample does not match latest: %+v", observation.Samples)
			}
		})
	}
}
