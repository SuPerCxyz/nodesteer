package api

import (
	"encoding/json"
	"net/http"
	"net/url"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func TestNativeSeriesBatchEndpoint(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)
	base := time.Now().UTC().Add(-time.Minute)
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", []store.NativeSample{
		nativeAPISample(base, "cpu.usage", map[string]string{"cpu": "all"}, 10),
		nativeAPISample(base, "cpu.usage", map[string]string{"cpu": "0"}, 12),
		nativeAPISample(base, "memory.usage", nil, 30),
	}); err != nil {
		t.Fatal(err)
	}

	recorder := nativeAPIRequest(handler, http.MethodGet,
		"/api/monitoring/nodes/node-1/series/batch?metric=cpu.usage&metric=memory.usage", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("series batch = %d %s", recorder.Code, recorder.Body.String())
	}
	var response nativeSeriesBatchResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if len(response.Items) != 2 {
		t.Fatalf("items = %d, want 2", len(response.Items))
	}
	if response.Items[0].Metric != "cpu.usage" || len(response.Items[0].Series) != 2 {
		t.Fatalf("cpu series = %+v", response.Items[0])
	}
	if response.Items[1].Metric != "memory.usage" || len(response.Items[1].Series) != 1 {
		t.Fatalf("memory series = %+v", response.Items[1])
	}

	recorder = nativeAPIRequest(handler, http.MethodGet,
		"/api/monitoring/nodes/node-1/series/batch", admin)
	if recorder.Code != http.StatusBadRequest {
		t.Fatalf("empty batch = %d, want 400", recorder.Code)
	}
}

func TestNativeHistoryBatchEndpoint(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)
	end := time.Now().UTC().Truncate(time.Minute)
	start := end.Add(-10 * time.Minute)
	samples := []store.NativeSample{}
	for i := 0; i < 10; i++ {
		ts := start.Add(time.Duration(i) * time.Minute)
		samples = append(samples,
			nativeAPISample(ts, "cpu.usage", map[string]string{"cpu": "all"}, float64(i)),
			nativeAPISample(ts, "memory.usage", nil, float64(i+1)),
		)
	}
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", samples); err != nil {
		t.Fatal(err)
	}
	values := url.Values{}
	values.Add("metric", "cpu.usage")
	values.Add("series", `cpu.usage|cpu=all`)
	values.Add("metric", "memory.usage")
	values.Add("series", "memory.usage")
	values.Set("start", start.Format(time.RFC3339Nano))
	values.Set("end", end.Add(time.Minute).Format(time.RFC3339Nano))
	values.Set("step", "1m")
	recorder := nativeAPIRequest(handler, http.MethodGet,
		"/api/monitoring/nodes/node-1/history/batch?"+values.Encode(), admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("history batch = %d %s", recorder.Code, recorder.Body.String())
	}
	var response nativeHistoryBatchResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatal(err)
	}
	if len(response.Items) != 2 {
		t.Fatalf("items = %d, want 2", len(response.Items))
	}
	for _, item := range response.Items {
		if item.History == nil || len(item.History.Points) == 0 {
			t.Fatalf("%s history = %+v", item.Metric, item.History)
		}
	}

	bad := nativeAPIRequest(handler, http.MethodGet,
		"/api/monitoring/nodes/node-1/history/batch?metric=cpu.usage", admin)
	if bad.Code != http.StatusBadRequest {
		t.Fatalf("unpaired batch = %d, want 400", bad.Code)
	}
}

func TestNativeNodeOverviewSingleEndpoint(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)
	base := time.Now().UTC().Add(-time.Minute)
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", []store.NativeSample{
		nativeAPISample(base, "cpu.usage", map[string]string{"cpu": "all"}, 42),
	}); err != nil {
		t.Fatal(err)
	}
	recorder := nativeAPIRequest(handler, http.MethodGet,
		"/api/monitoring/nodes/node-1/overview", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("node overview = %d %s", recorder.Code, recorder.Body.String())
	}
	var node nativeOverviewNode
	if err := json.Unmarshal(recorder.Body.Bytes(), &node); err != nil {
		t.Fatal(err)
	}
	if node.NodeID != "node-1" || node.CPU == nil || *node.CPU != 42 {
		t.Fatalf("node overview = %+v", node)
	}
}
