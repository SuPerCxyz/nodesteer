package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

func nativeAPIRequest(handler http.Handler, method, path, token string) *httptest.ResponseRecorder {
	request := httptest.NewRequest(method, path, nil)
	if token != "" {
		request.Header.Set("Authorization", "Bearer "+token)
	}
	recorder := httptest.NewRecorder()
	handler.ServeHTTP(recorder, request)
	return recorder
}

func nativeAPIValue(value float64) *float64 { return &value }

func nativeAPISample(timestamp time.Time, metric string, dimensions map[string]string, value float64) store.NativeSample {
	return store.NativeSample{
		Timestamp:  timestamp,
		Metric:     metric,
		Dimensions: dimensions,
		Value:      nativeAPIValue(value),
		Quality:    store.NativeQualityValid,
	}
}

func createNativeAPINode(t *testing.T, st *store.SQLiteStore, id, status string) {
	t.Helper()
	if err := st.UpsertNode(t.Context(), &models.Node{
		ID:       id,
		AgentID:  "agent-" + id,
		Hostname: id,
		Status:   status,
	}); err != nil {
		t.Fatalf("create node %s: %v", id, err)
	}
}

func TestNativeMonitoringOverview(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	viewer := loginAs(t, s, "viewer", "password")

	createNativeAPINode(t, st, "online", models.NodeStatusOnline)
	createNativeAPINode(t, st, "no-data", models.NodeStatusOnline)
	createNativeAPINode(t, st, "missing", models.NodeStatusOnline)
	createNativeAPINode(t, st, "offline", models.NodeStatusOffline)

	base := time.Now().UTC().Add(-time.Minute)
	if err := st.InsertNodeMetricSamples(t.Context(), "online", []store.NativeSample{
		nativeAPISample(base, "cpu.usage", map[string]string{"cpu": "all"}, 12.3),
		nativeAPISample(base, "memory.usage", nil, 45.6),
		nativeAPISample(base, "load.1", nil, 0.8),
		nativeAPISample(base, "load.5", nil, 1.5),
		nativeAPISample(base, "load.15", nil, 2.5),
		nativeAPISample(base, "disk.usage", map[string]string{"mount": "/", "device": "/dev/sda1"}, 61.2),
		nativeAPISample(base, "network.rx_bps", map[string]string{"interface": "eth0"}, 1000),
		nativeAPISample(base, "network.rx_bps", map[string]string{"interface": "eth1"}, 234),
		nativeAPISample(base, "network.tx_bps", map[string]string{"interface": "eth0"}, 500),
		nativeAPISample(base, "network.tx_bps", map[string]string{"interface": "eth1"}, 67),
		nativeAPISample(base, "system.uptime", nil, 3600.5),
		nativeAPISample(base, "system.temperature_c", nil, 42.5),
	}); err != nil {
		t.Fatalf("insert online samples: %v", err)
	}
	if err := st.InsertNodeMetricSamples(t.Context(), "offline", []store.NativeSample{
		nativeAPISample(base, "cpu.usage", nil, 99),
	}); err != nil {
		t.Fatalf("insert offline samples: %v", err)
	}
	if err := st.InsertNodeMetricSamples(t.Context(), "missing", []store.NativeSample{
		{Timestamp: base, Metric: "cpu.usage", Quality: store.NativeQualityMissing},
	}); err != nil {
		t.Fatalf("insert missing samples: %v", err)
	}

	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/overview", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("overview = %d %s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Nodes []struct {
			NodeID        string     `json:"node_id"`
			Hostname      string     `json:"hostname"`
			Status        string     `json:"status"`
			LastSeen      *time.Time `json:"last_seen"`
			CPU           *float64   `json:"cpu"`
			Memory        *float64   `json:"memory"`
			Load1         *float64   `json:"load1"`
			Load5         *float64   `json:"load5"`
			Load15        *float64   `json:"load15"`
			Disk          *float64   `json:"disk"`
			NetworkRX     *float64   `json:"network_rx_bps"`
			NetworkTX     *float64   `json:"network_tx_bps"`
			NetworkTotal  *float64   `json:"network_total_bps"`
			UptimeSeconds *float64   `json:"uptime_seconds"`
			TemperatureC  *float64   `json:"temperature_c"`
			Alerts        int        `json:"alerts"`
			ObservedAt    *time.Time `json:"observed_at"`
			State         string     `json:"state"`
		} `json:"nodes"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode overview: %v", err)
	}
	byID := map[string]struct {
		CPU, Memory, Load1, Load5, Load15, Disk, RX, TX, Total, Uptime, Temperature *float64
		State                                                                       string
		ObservedAt                                                                  *time.Time
	}{}
	for _, node := range response.Nodes {
		byID[node.NodeID] = struct {
			CPU, Memory, Load1, Load5, Load15, Disk, RX, TX, Total, Uptime, Temperature *float64
			State                                                                       string
			ObservedAt                                                                  *time.Time
		}{node.CPU, node.Memory, node.Load1, node.Load5, node.Load15, node.Disk,
			node.NetworkRX, node.NetworkTX, node.NetworkTotal, node.UptimeSeconds,
			node.TemperatureC, node.State, node.ObservedAt}
	}
	online := byID["online"]
	if online.CPU == nil || *online.CPU != 12.3 || online.Memory == nil || *online.Memory != 45.6 {
		t.Fatalf("online cpu/memory = %v/%v", online.CPU, online.Memory)
	}
	if online.Load1 == nil || *online.Load1 != 0.8 || online.Load5 == nil || *online.Load5 != 1.5 || online.Load15 == nil || *online.Load15 != 2.5 {
		t.Fatalf("online load = %v/%v/%v", online.Load1, online.Load5, online.Load15)
	}
	if online.Disk == nil || *online.Disk != 61.2 {
		t.Fatalf("online disk = %v", online.Disk)
	}
	if online.RX == nil || *online.RX != 1234 || online.TX == nil || *online.TX != 567 {
		t.Fatalf("online network = %v/%v", online.RX, online.TX)
	}
	if online.Total == nil || *online.Total != 1801 {
		t.Fatalf("online network total = %v, want 1801", online.Total)
	}
	if online.Uptime == nil || *online.Uptime != 3600.5 || online.Temperature == nil || *online.Temperature != 42.5 {
		t.Fatalf("online uptime/temperature = %v/%v", online.Uptime, online.Temperature)
	}
	if online.State != "ready" || online.ObservedAt == nil {
		t.Fatalf("online state = %q, observed = %v", online.State, online.ObservedAt)
	}
	noData := byID["no-data"]
	if noData.CPU != nil || noData.Memory != nil || noData.Load5 != nil || noData.Load15 != nil ||
		noData.Total != nil || noData.Uptime != nil || noData.Temperature != nil ||
		noData.State != "no_data" || noData.ObservedAt != nil {
		t.Fatalf("no-data = %+v", noData)
	}
	missing := byID["missing"]
	if missing.State != "data_gap" || missing.CPU != nil || missing.Load5 != nil ||
		missing.Uptime != nil || missing.Temperature != nil || missing.Total != nil {
		t.Fatalf("missing = %+v", missing)
	}
	offline := byID["offline"]
	if offline.State != "agent_offline" || offline.Load5 != nil || offline.Uptime != nil || offline.Temperature != nil {
		t.Fatalf("offline = %+v", offline)
	}

	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/overview", viewer); recorder.Code != http.StatusForbidden {
		t.Fatalf("viewer overview = %d", recorder.Code)
	}
	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/overview", ""); recorder.Code != http.StatusUnauthorized {
		t.Fatalf("anonymous overview = %d", recorder.Code)
	}
	if recorder := nativeAPIRequest(handler, http.MethodPost, "/api/monitoring/nodes/overview", admin); recorder.Code != http.StatusMethodNotAllowed {
		t.Fatalf("post overview = %d", recorder.Code)
	}
}

func TestNativeOverviewExtendedFields(t *testing.T) {
	now := time.Now().UTC()
	base := now.Add(-time.Minute)
	node := &models.Node{ID: "node-1", Hostname: "node-1", Status: models.NodeStatusOnline}
	samples := []store.NativeSample{
		nativeAPISample(base, "load.5", nil, 1.5),
		nativeAPISample(base, "load.15", nil, 2.5),
		nativeAPISample(base, "system.uptime", nil, 3600),
		nativeAPISample(base, "network.rx_bps", map[string]string{"interface": "eth0"}, 100),
		{Timestamp: base, Metric: "system.temperature_c", Quality: store.NativeQualityMissing},
	}

	got := buildNativeOverview(node, samples, 30*time.Second, now)
	if got.Load5 == nil || *got.Load5 != 1.5 || got.Load15 == nil || *got.Load15 != 2.5 {
		t.Fatalf("load5/15 = %v/%v", got.Load5, got.Load15)
	}
	if got.UptimeSeconds == nil || *got.UptimeSeconds != 3600 {
		t.Fatalf("uptime = %v", got.UptimeSeconds)
	}
	if got.TemperatureC != nil {
		t.Fatalf("missing temperature = %v, want nil", got.TemperatureC)
	}
	if got.NetworkRX == nil || *got.NetworkRX != 100 || got.NetworkTX != nil {
		t.Fatalf("network rx/tx = %v/%v", got.NetworkRX, got.NetworkTX)
	}
	if got.NetworkTotal != nil {
		t.Fatalf("network total without tx = %v, want nil", got.NetworkTotal)
	}

	samples = append(samples,
		nativeAPISample(base, "network.tx_bps", map[string]string{"interface": "eth0"}, 50),
		nativeAPISample(base, "system.temperature_c", nil, 42.5),
	)
	got = buildNativeOverview(node, samples, 30*time.Second, now)
	if got.NetworkTotal == nil || *got.NetworkTotal != 150 {
		t.Fatalf("network total = %v, want 150", got.NetworkTotal)
	}
	if got.TemperatureC == nil || *got.TemperatureC != 42.5 {
		t.Fatalf("temperature = %v, want 42.5", got.TemperatureC)
	}

	// 过期样本沿用现有语义：值保留，state 标记 data_gap。
	stale := now.Add(-2 * time.Minute)
	got = buildNativeOverview(node, []store.NativeSample{
		nativeAPISample(stale, "load.5", nil, 1.5),
		nativeAPISample(stale, "system.uptime", nil, 10),
		nativeAPISample(stale, "system.temperature_c", nil, 40),
	}, 30*time.Second, now)
	if got.State != "data_gap" || got.Load5 == nil || got.UptimeSeconds == nil || got.TemperatureC == nil {
		t.Fatalf("stale overview = %+v", got)
	}

	// 离线节点即使有样本也标记 agent_offline。
	offline := *node
	offline.Status = models.NodeStatusOffline
	got = buildNativeOverview(&offline, samples, 30*time.Second, now)
	if got.State != "agent_offline" {
		t.Fatalf("offline state = %q", got.State)
	}
}

func TestNativeMonitoringSeries(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	viewer := loginAs(t, s, "viewer", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)

	base := time.Now().UTC().Add(-time.Minute)
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", []store.NativeSample{
		nativeAPISample(base, "cpu.usage", map[string]string{"cpu": "0"}, 10),
		nativeAPISample(base, "cpu.usage", map[string]string{"cpu": "all"}, 15),
	}); err != nil {
		t.Fatalf("insert: %v", err)
	}

	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/series?metric=cpu.usage", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("series = %d %s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Series []struct {
			SeriesKey  string            `json:"seriesKey"`
			Dimensions map[string]string `json:"dimensions"`
		} `json:"series"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode series: %v", err)
	}
	if len(response.Series) != 2 || response.Series[0].SeriesKey != "cpu.usage|cpu=0" || response.Series[1].SeriesKey != "cpu.usage|cpu=all" {
		t.Fatalf("series = %+v", response.Series)
	}
	if response.Series[0].Dimensions["cpu"] != "0" {
		t.Fatalf("dimensions = %v", response.Series[0].Dimensions)
	}

	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/series", admin); recorder.Code != http.StatusBadRequest {
		t.Fatalf("missing metric = %d", recorder.Code)
	}
	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/series?metric=bad%20metric", admin); recorder.Code != http.StatusBadRequest {
		t.Fatalf("invalid metric = %d", recorder.Code)
	}
	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/unknown/series?metric=cpu.usage", admin); recorder.Code != http.StatusNotFound {
		t.Fatalf("unknown node = %d", recorder.Code)
	}
	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/series?metric=cpu.usage", viewer); recorder.Code != http.StatusForbidden {
		t.Fatalf("viewer series = %d", recorder.Code)
	}
	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/unknown?metric=cpu.usage", admin); recorder.Code != http.StatusNotFound {
		t.Fatalf("unknown subpath = %d", recorder.Code)
	}
}

func TestNativeMonitoringMetrics(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	viewer := loginAs(t, s, "viewer", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)

	base := time.Now().UTC().Add(-10 * time.Minute).Truncate(time.Minute)
	samples := []store.NativeSample{
		nativeAPISample(base, "cpu.usage", nil, 10),
		nativeAPISample(base.Add(30*time.Second), "cpu.usage", nil, 20),
		nativeAPISample(base.Add(time.Minute), "cpu.usage", nil, 30),
		nativeAPISample(base.Add(90*time.Second), "cpu.usage", nil, 40),
		nativeAPISample(base.Add(2*time.Minute), "cpu.usage", nil, 50),
	}
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", samples); err != nil {
		t.Fatalf("insert: %v", err)
	}
	if err := st.AggregateNodeMetrics(t.Context(), time.Now().UTC()); err != nil {
		t.Fatalf("aggregate: %v", err)
	}

	// 24h 范围按点预算选择 1m 聚合层。
	start := base.Add(-12 * time.Hour).Format(time.RFC3339Nano)
	end := base.Add(12 * time.Hour).Format(time.RFC3339Nano)
	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/metrics?metric=cpu.usage&series=cpu.usage&start="+start+"&end="+end, admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("metrics = %d %s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		State  string `json:"state"`
		Tier   string `json:"tier"`
		StepNs int64  `json:"stepNs"`
		Unit   string `json:"unit"`
		Points []struct {
			Timestamp time.Time `json:"timestamp"`
			Value     *float64  `json:"value"`
			Quality   int       `json:"quality"`
			Count     uint64    `json:"count"`
			Min       *float64  `json:"min"`
			Max       *float64  `json:"max"`
		} `json:"points"`
		Gaps []struct {
			Start time.Time `json:"start"`
			End   time.Time `json:"end"`
		} `json:"gaps"`
		Summary *struct {
			Count    int      `json:"count"`
			Expected int      `json:"expected"`
			Missing  int      `json:"missing"`
			Average  *float64 `json:"average"`
		} `json:"summary"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode metrics: %v", err)
	}
	if response.Tier != store.NativeTier1m || response.StepNs != int64(time.Minute) {
		t.Fatalf("tier/step = %q/%d", response.Tier, response.StepNs)
	}
	if response.Unit != "%" || response.State != "data_gap" {
		t.Fatalf("unit/state = %q/%q", response.Unit, response.State)
	}
	if len(response.Points) != 3 {
		t.Fatalf("points = %d, want 3", len(response.Points))
	}
	first := response.Points[0]
	if first.Quality != 0 || first.Count != 2 || first.Value == nil || *first.Value != 15 {
		t.Fatalf("first point = %+v", first)
	}
	if first.Min == nil || *first.Min != 10 || first.Max == nil || *first.Max != 20 {
		t.Fatalf("first min/max = %v/%v", first.Min, first.Max)
	}
	if response.Summary == nil || response.Summary.Count != 3 || response.Summary.Expected != 6 || response.Summary.Missing != 1 || response.Summary.Average == nil || *response.Summary.Average != 30 {
		t.Fatalf("summary = %+v", response.Summary)
	}
	if len(response.Gaps) == 0 {
		t.Fatal("expected a gap after the last sample")
	}

	// step=5m 选择 5m 聚合桶。
	recorder = nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/metrics?metric=cpu.usage&series=cpu.usage&start="+start+"&end="+end+"&step=5m", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("5m metrics = %d %s", recorder.Code, recorder.Body.String())
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode 5m metrics: %v", err)
	}
	if response.Tier != store.NativeTier5m || response.StepNs != int64(5*time.Minute) {
		t.Fatalf("5m tier/step = %q/%d", response.Tier, response.StepNs)
	}

	// 无数据指标返回 no_data，而不是伪造 0。
	recorder = nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/metrics?metric=memory.usage&series=memory.usage&start="+start+"&end="+end, admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("no-data metrics = %d %s", recorder.Code, recorder.Body.String())
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode no-data metrics: %v", err)
	}
	if response.State != "no_data" || len(response.Points) != 0 || len(response.Gaps) != 0 {
		t.Fatalf("no-data response = %+v", response)
	}

	for _, path := range []string{
		"/api/monitoring/nodes/node-1/metrics",
		"/api/monitoring/nodes/node-1/metrics?metric=cpu.usage&series=cpu.usage&start=bad",
		"/api/monitoring/nodes/node-1/metrics?metric=cpu.usage&series=cpu.usage&end=bad",
		"/api/monitoring/nodes/node-1/metrics?metric=cpu.usage&series=cpu.usage&step=bad",
		"/api/monitoring/nodes/node-1/metrics?metric=cpu.usage&series=cpu.usage&start=" + end + "&end=" + start,
	} {
		if recorder := nativeAPIRequest(handler, http.MethodGet, path, admin); recorder.Code != http.StatusBadRequest {
			t.Fatalf("bad params %q = %d %s", path, recorder.Code, recorder.Body.String())
		}
	}
	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/metrics?metric=cpu.usage&series=cpu.usage&start="+start+"&end="+end, viewer); recorder.Code != http.StatusForbidden {
		t.Fatalf("viewer metrics = %d", recorder.Code)
	}
	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/unknown/metrics?metric=cpu.usage&series=cpu.usage", admin); recorder.Code != http.StatusNotFound {
		t.Fatalf("unknown node metrics = %d", recorder.Code)
	}
}

func TestNativeMonitoringMetricsDefaultRange(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)
	base := time.Now().UTC().Add(-time.Minute)
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", []store.NativeSample{
		nativeAPISample(base, "cpu.usage", nil, 10),
	}); err != nil {
		t.Fatalf("insert: %v", err)
	}
	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/metrics?metric=cpu.usage&series=cpu.usage", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("default range = %d %s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Tier   string `json:"tier"`
		StepNs int64  `json:"stepNs"`
		Points []struct {
			Value *float64 `json:"value"`
		} `json:"points"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode default range: %v", err)
	}
	if response.Tier != store.NativeTierRaw || response.StepNs != int64(store.NativeRawStep) {
		t.Fatalf("default tier/step = %q/%d", response.Tier, response.StepNs)
	}
	if len(response.Points) != 1 || response.Points[0].Value == nil || *response.Points[0].Value != 10 {
		t.Fatalf("default points = %+v", response.Points)
	}
}

func TestNativeMonitoringMetricsRawFallback(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)

	base := time.Now().UTC().Add(-time.Hour).Truncate(time.Minute)
	samples := make([]store.NativeSample, 0, 60)
	for index := 0; index < 60; index++ {
		samples = append(samples, nativeAPISample(base.Add(time.Duration(index)*30*time.Second), "load.5", nil, float64(index)))
	}
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", samples); err != nil {
		t.Fatalf("insert: %v", err)
	}
	start := base.Format(time.RFC3339Nano)
	end := base.Add(30 * time.Minute).Format(time.RFC3339Nano)
	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/metrics?metric=load.5&series=load.5&start="+start+"&end="+end+"&step=1m", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("raw fallback = %d %s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Tier   string `json:"tier"`
		StepNs int64  `json:"stepNs"`
		Points []struct {
			Quality int    `json:"quality"`
			Count   uint64 `json:"count"`
		} `json:"points"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode raw fallback: %v", err)
	}
	if response.Tier != store.NativeTierRaw || response.StepNs != int64(store.NativeRawStep) {
		t.Fatalf("raw fallback tier/step = %q/%d", response.Tier, response.StepNs)
	}
	if len(response.Points) != 60 || response.Points[0].Quality != 1 {
		t.Fatalf("raw fallback points = %d, first = %+v", len(response.Points), response.Points[0])
	}
}

func TestNativeMonitoringMetricsHourlyTier(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)

	base := time.Now().UTC().Truncate(time.Hour).Add(-2 * time.Hour)
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", []store.NativeSample{
		nativeAPISample(base, "cpu.usage", nil, 10),
		nativeAPISample(base.Add(30*time.Second), "cpu.usage", nil, 20),
	}); err != nil {
		t.Fatalf("insert: %v", err)
	}
	if err := st.AggregateNodeMetrics(t.Context(), time.Now().UTC()); err != nil {
		t.Fatalf("aggregate: %v", err)
	}
	start := base.Format(time.RFC3339Nano)
	end := base.Add(3 * time.Hour).Format(time.RFC3339Nano)
	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/metrics?metric=cpu.usage&series=cpu.usage&start="+start+"&end="+end+"&step=1h", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("hourly = %d %s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Tier   string `json:"tier"`
		StepNs int64  `json:"stepNs"`
		Points []struct {
			Quality int    `json:"quality"`
			Count   uint64 `json:"count"`
		} `json:"points"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode hourly: %v", err)
	}
	if response.Tier != store.NativeTier1h || response.StepNs != int64(time.Hour) {
		t.Fatalf("hourly tier/step = %q/%d", response.Tier, response.StepNs)
	}
	if len(response.Points) != 1 || response.Points[0].Count != 2 || response.Points[0].Quality != 0 {
		t.Fatalf("hourly points = %+v", response.Points)
	}
}

func TestNativeMonitoringLatest(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	viewer := loginAs(t, s, "viewer", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)
	createNativeAPINode(t, st, "offline", models.NodeStatusOffline)

	base := time.Now().UTC().Add(-time.Minute)
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", []store.NativeSample{
		nativeAPISample(base, "cpu.usage", map[string]string{"cpu": "all"}, 12.3),
		{Timestamp: base, Metric: "memory.usage", Quality: store.NativeQualityMissing},
	}); err != nil {
		t.Fatalf("insert: %v", err)
	}
	if err := st.InsertNodeMetricSamples(t.Context(), "offline", []store.NativeSample{
		nativeAPISample(base, "cpu.usage", nil, 50),
	}); err != nil {
		t.Fatalf("insert offline: %v", err)
	}

	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/latest", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("latest = %d %s", recorder.Code, recorder.Body.String())
	}
	var response struct {
		Metrics []struct {
			Metric     string    `json:"metric"`
			Series     string    `json:"series"`
			Value      *float64  `json:"value"`
			State      string    `json:"state"`
			ObservedAt time.Time `json:"observed_at"`
		} `json:"metrics"`
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode latest: %v", err)
	}
	if len(response.Metrics) != 2 {
		t.Fatalf("latest metrics = %d", len(response.Metrics))
	}
	byMetric := map[string]struct {
		Series string
		Value  *float64
		State  string
	}{}
	for _, metric := range response.Metrics {
		byMetric[metric.Metric] = struct {
			Series string
			Value  *float64
			State  string
		}{metric.Series, metric.Value, metric.State}
	}
	cpu := byMetric["cpu.usage"]
	if cpu.Series != "cpu.usage|cpu=all" || cpu.Value == nil || *cpu.Value != 12.3 || cpu.State != "ready" {
		t.Fatalf("cpu latest = %+v", cpu)
	}
	memory := byMetric["memory.usage"]
	if memory.Value != nil || memory.State != "data_gap" {
		t.Fatalf("memory latest = %+v", memory)
	}

	recorder = nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/offline/latest", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("offline latest = %d", recorder.Code)
	}
	if err := json.Unmarshal(recorder.Body.Bytes(), &response); err != nil {
		t.Fatalf("decode offline latest: %v", err)
	}
	if len(response.Metrics) != 1 || response.Metrics[0].State != "agent_offline" {
		t.Fatalf("offline latest = %+v", response.Metrics)
	}

	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/latest", viewer); recorder.Code != http.StatusForbidden {
		t.Fatalf("viewer latest = %d", recorder.Code)
	}
	if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/unknown/latest", admin); recorder.Code != http.StatusNotFound {
		t.Fatalf("unknown latest = %d", recorder.Code)
	}
}

func TestNativeMonitoringTimeParamFormats(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)
	base := time.Now().UTC().Add(-time.Hour).Truncate(time.Second)
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", []store.NativeSample{
		nativeAPISample(base, "cpu.usage", nil, 10),
	}); err != nil {
		t.Fatalf("insert: %v", err)
	}
	start := base.Add(-time.Minute)
	end := base.Add(time.Minute)
	for _, query := range []string{
		"?metric=cpu.usage&series=cpu.usage&start=" + start.Format(time.RFC3339Nano) + "&end=" + end.Format(time.RFC3339Nano),
		"?metric=cpu.usage&series=cpu.usage&start=" + formatUnix(start.Unix()) + "&end=" + formatUnix(end.Unix()),
		"?metric=cpu.usage&series=cpu.usage&start=" + formatUnixMilli(start.UnixMilli()) + "&end=" + formatUnixMilli(end.UnixMilli()),
		"?metric=cpu.usage&series=cpu.usage&start=" + start.Format(time.RFC3339Nano) + "&end=" + end.Format(time.RFC3339Nano) + "&step=" + formatUnix(int64(time.Minute)),
		"?metric=cpu.usage&series=cpu.usage&start=" + start.Format(time.RFC3339Nano) + "&end=" + end.Format(time.RFC3339Nano) + "&step=60",
	} {
		if recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/metrics"+query, admin); recorder.Code != http.StatusOK {
			t.Fatalf("params %q = %d %s", query, recorder.Code, recorder.Body.String())
		}
	}
}

func formatUnix(value int64) string {
	return strconv.FormatInt(value, 10)
}

func formatUnixMilli(value int64) string {
	return strconv.FormatInt(value, 10)
}

func TestNativeMonitoringNoSQLLeak(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)
	for _, table := range []string{"node_metric_samples", "node_metric_series"} {
		if _, err := st.DB().Exec(`DROP TABLE ` + table); err != nil {
			t.Fatalf("drop table %s: %v", table, err)
		}
	}

	for _, path := range []string{
		"/api/monitoring/nodes/overview",
		"/api/monitoring/nodes/node-1/latest",
		"/api/monitoring/nodes/node-1/series?metric=cpu.usage",
		"/api/monitoring/nodes/node-1/metrics?metric=cpu.usage&series=cpu.usage",
	} {
		recorder := nativeAPIRequest(handler, http.MethodGet, path, admin)
		if recorder.Code != http.StatusInternalServerError {
			t.Fatalf("%s = %d %s", path, recorder.Code, recorder.Body.String())
		}
		body := strings.ToLower(recorder.Body.String())
		for _, leaked := range []string{"no such table", "sqlite", "select ", "syntax error"} {
			if strings.Contains(body, leaked) {
				t.Fatalf("%s leaked %q: %s", path, leaked, recorder.Body.String())
			}
		}
	}
}

func TestNativeMonitoringMetricsRequiresSeries(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)
	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/metrics?metric=cpu.usage", admin)
	if recorder.Code != http.StatusBadRequest || !strings.Contains(recorder.Body.String(), "series is required") {
		t.Fatalf("missing series = %d %s, want 400 series is required", recorder.Code, recorder.Body.String())
	}
}

func TestNativeOverviewFallsBackFromMissingPreferredDimension(t *testing.T) {
	base := time.Now().UTC().Add(-time.Minute)
	samples := []store.NativeSample{
		{Timestamp: base, Metric: "cpu.usage", Dimensions: map[string]string{"cpu": "all"}, Quality: store.NativeQualityMissing},
		nativeAPISample(base, "cpu.usage", map[string]string{"cpu": "0"}, 42),
	}
	got := pickNativeSample(samples, "cpu.usage", "cpu", "all")
	if got == nil || got.Dimensions["cpu"] != "0" || got.Value == nil || *got.Value != 42 {
		t.Fatalf("fallback sample = %+v", got)
	}
}

func TestNativeMonitoringStaleSamplesAreDataGap(t *testing.T) {
	s, st := newMeTestServer(t)
	seedMonitoringUsers(t, st)
	handler := s.Routes()
	admin := loginAs(t, s, "admin", "password")
	createNativeAPINode(t, st, "node-1", models.NodeStatusOnline)
	old := time.Now().UTC().Add(-10 * time.Minute)
	if err := st.InsertNodeMetricSamples(t.Context(), "node-1", []store.NativeSample{
		nativeAPISample(old, "cpu.usage", nil, 10),
	}); err != nil {
		t.Fatal(err)
	}

	recorder := nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/overview", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("overview = %d %s", recorder.Code, recorder.Body.String())
	}
	var overview nativeOverviewResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &overview); err != nil {
		t.Fatal(err)
	}
	if len(overview.Nodes) != 1 || overview.Nodes[0].State != "data_gap" {
		t.Fatalf("overview state = %+v", overview.Nodes)
	}

	recorder = nativeAPIRequest(handler, http.MethodGet, "/api/monitoring/nodes/node-1/latest", admin)
	if recorder.Code != http.StatusOK {
		t.Fatalf("latest = %d %s", recorder.Code, recorder.Body.String())
	}
	var latest nativeLatestResponse
	if err := json.Unmarshal(recorder.Body.Bytes(), &latest); err != nil {
		t.Fatal(err)
	}
	if len(latest.Metrics) != 1 || latest.Metrics[0].State != "data_gap" {
		t.Fatalf("latest state = %+v", latest.Metrics)
	}
}
