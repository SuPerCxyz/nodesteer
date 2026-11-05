package api

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/hub/auth"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// nativeQueryCountingStore 记录 overview 是否仍走逐节点查询。
type nativeQueryCountingStore struct {
	*store.SQLiteStore
	latestSingle   int
	latestBatch    int
	intervalSingle int
	intervalBatch  int
}

func (s *nativeQueryCountingStore) LatestNodeMetricSamples(ctx context.Context, nodeID string) ([]store.NativeSample, error) {
	s.latestSingle++
	return s.SQLiteStore.LatestNodeMetricSamples(ctx, nodeID)
}

func (s *nativeQueryCountingStore) LatestNodeMetricSamplesBatch(ctx context.Context, nodeIDs []string) (map[string][]store.NativeSample, error) {
	s.latestBatch++
	return s.SQLiteStore.LatestNodeMetricSamplesBatch(ctx, nodeIDs)
}

func (s *nativeQueryCountingStore) NodeMetricSampleInterval(ctx context.Context, nodeID string) (time.Duration, error) {
	s.intervalSingle++
	return s.SQLiteStore.NodeMetricSampleInterval(ctx, nodeID)
}

func (s *nativeQueryCountingStore) NodeMetricSampleIntervals(ctx context.Context, nodeIDs []string) (map[string]time.Duration, error) {
	s.intervalBatch++
	return s.SQLiteStore.NodeMetricSampleIntervals(ctx, nodeIDs)
}

func newCountingNativeServer(t *testing.T) (*Server, *nativeQueryCountingStore) {
	t.Helper()
	st, err := store.OpenInMemory()
	if err != nil {
		t.Fatalf("open in-memory store: %v", err)
	}
	t.Cleanup(func() { _ = st.Close() })
	counting := &nativeQueryCountingStore{SQLiteStore: st}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	return New(counting, auth.New(counting, time.Hour), nil, nil, nil, nil, nil, nil, nil, logger), counting
}

func TestNativeOverviewUsesBatchQueries(t *testing.T) {
	s, counting := newCountingNativeServer(t)
	seedMonitoringUsers(t, counting.SQLiteStore)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")

	for _, nodeID := range []string{"node-a", "node-b", "node-c"} {
		createNativeAPINode(t, counting.SQLiteStore, nodeID, models.NodeStatusOnline)
	}
	base := time.Now().UTC().Add(-time.Minute)
	for _, nodeID := range []string{"node-a", "node-b"} {
		if err := counting.InsertNodeMetricSamples(t.Context(), nodeID, []store.NativeSample{
			nativeAPISample(base, "cpu.usage", nil, 10),
			nativeAPISample(base.Add(30*time.Second), "cpu.usage", nil, 20),
		}); err != nil {
			t.Fatalf("insert %s: %v", nodeID, err)
		}
	}

	counting.latestSingle = 0
	counting.latestBatch = 0
	counting.intervalSingle = 0
	counting.intervalBatch = 0
	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/overview", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("overview = %d %s", recorder.Code, recorder.Body.String())
	}
	if counting.latestSingle != 0 || counting.intervalSingle != 0 {
		t.Fatalf("overview must not query per node: latest=%d interval=%d",
			counting.latestSingle, counting.intervalSingle)
	}
	if counting.latestBatch != 1 || counting.intervalBatch != 1 {
		t.Fatalf("overview batch calls = latest:%d interval:%d, want 1/1",
			counting.latestBatch, counting.intervalBatch)
	}

	var response nativeOverviewResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode overview: %v", err)
	}
	if len(response.Nodes) != 3 {
		t.Fatalf("overview nodes = %d, want 3", len(response.Nodes))
	}
	byID := map[string]nativeOverviewNode{}
	for _, node := range response.Nodes {
		byID[node.NodeID] = node
	}
	if byID["node-a"].CPU == nil || *byID["node-a"].CPU != 20 {
		t.Fatalf("node-a cpu = %v, want 20", byID["node-a"].CPU)
	}
	if byID["node-b"].CPU == nil || *byID["node-b"].CPU != 20 {
		t.Fatalf("node-b cpu = %v, want 20", byID["node-b"].CPU)
	}
	if byID["node-c"].State != "no_data" {
		t.Fatalf("node-c state = %q, want no_data", byID["node-c"].State)
	}
}

func TestNativeContainerMetricsCapabilitySignal(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")

	for _, node := range []*models.Node{
		{
			ID: "container-on", AgentID: "agent-container-on", Hostname: "container-on",
			Status: models.NodeStatusOnline,
			Capabilities: map[string]bool{
				models.CapNodeMetrics:      true,
				models.CapContainerMetrics: true,
			},
		},
		{
			ID: "container-off", AgentID: "agent-container-off", Hostname: "container-off",
			Status:       models.NodeStatusOnline,
			Capabilities: map[string]bool{models.CapNodeMetrics: true},
		},
		{
			ID: "legacy-agent", AgentID: "agent-legacy", Hostname: "legacy-agent",
			Status:       models.NodeStatusOnline,
			Capabilities: map[string]bool{models.CapNodeMetrics: true},
		},
	} {
		if err := st.UpsertNode(t.Context(), node); err != nil {
			t.Fatalf("create node %s: %v", node.ID, err)
		}
	}

	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/overview", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("overview = %d %s", recorder.Code, recorder.Body.String())
	}
	var overview nativeOverviewResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &overview); err != nil {
		t.Fatalf("decode overview: %v", err)
	}
	byID := map[string]nativeOverviewNode{}
	for _, node := range overview.Nodes {
		byID[node.NodeID] = node
	}
	if !byID["container-on"].ContainerMetricsAvailable {
		t.Fatal("container-on must report container_metrics_available=true")
	}
	if byID["container-off"].ContainerMetricsAvailable || byID["legacy-agent"].ContainerMetricsAvailable {
		t.Fatal("nodes without container_metrics capability must report false")
	}

	for nodeID, want := range map[string]bool{"container-on": true, "container-off": false} {
		recorder = nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/"+nodeID+"/latest", admin)
		if recorder.Code != http.StatusOK {
			t.Fatalf("%s latest = %d %s", nodeID, recorder.Code, recorder.Body.String())
		}
		var latest nativeLatestResponse
		if err := json.Unmarshal(recorder.Body.Bytes(), &latest); err != nil {
			t.Fatalf("decode %s latest: %v", nodeID, err)
		}
		if latest.ContainerMetricsAvailable != want {
			t.Fatalf("%s latest container_metrics_available = %v, want %v",
				nodeID, latest.ContainerMetricsAvailable, want)
		}
	}

}
