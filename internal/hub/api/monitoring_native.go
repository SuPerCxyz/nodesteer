package api

import (
	"database/sql"
	"errors"
	"math"
	"net/http"
	"strconv"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type nativeOverviewNode struct {
	NodeID        string     `json:"node_id"`
	Hostname      string     `json:"hostname"`
	DisplayName   string     `json:"display_name"`
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
	// ContainerMetricsAvailable 来自 Agent 能力上报；旧 Agent 无该能力时为 false。
	ContainerMetricsAvailable bool `json:"container_metrics_available"`
}

type nativeOverviewResponse struct {
	Nodes []nativeOverviewNode `json:"nodes"`
}

type nativeLatestMetric struct {
	Metric     string    `json:"metric"`
	Series     string    `json:"series"`
	Value      *float64  `json:"value"`
	State      string    `json:"state"`
	ObservedAt time.Time `json:"observed_at"`
}

type nativeLatestResponse struct {
	Metrics []nativeLatestMetric `json:"metrics"`
	// ContainerMetricsAvailable 与节点能力一致；为 false 时前端可提示容器监控未启用。
	ContainerMetricsAvailable bool `json:"container_metrics_available"`
}

type nativeSeriesResponse struct {
	Series []store.NativeSeries `json:"series"`
}

// handleMonitoringNodeOverview 返回节点列表与每节点最新原生指标。
// 无样本字段为 null，缺失值不会被伪造成 0。
func (s *Server) handleMonitoringNodeOverview(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, "GET")
		return
	}
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	nativeStore, ok := s.store.(store.NativeMetricStore)
	if !ok {
		writeErr(w, http.StatusServiceUnavailable, "native monitoring unavailable")
		return
	}
	nodes, err := s.store.ListNodes(r.Context())
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	now := time.Now().UTC()
	nodeIDs := make([]string, 0, len(nodes))
	for _, node := range nodes {
		nodeIDs = append(nodeIDs, node.ID)
	}
	// 批量查询所有节点的最新样本与采样周期，避免逐节点查询的 N+1。
	samplesByNode, err := nativeStore.LatestNodeMetricSamplesBatch(r.Context(), nodeIDs)
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	intervals, err := nativeStore.NodeMetricSampleIntervals(r.Context(), nodeIDs)
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	items := make([]nativeOverviewNode, 0, len(nodes))
	for _, node := range nodes {
		items = append(items, buildNativeOverview(node, samplesByNode[node.ID], intervals[node.ID], now))
	}
	writeJSON(w, http.StatusOK, nativeOverviewResponse{Nodes: items})
}

func buildNativeOverview(node *models.Node, samples []store.NativeSample, interval time.Duration, now time.Time) nativeOverviewNode {
	item := nativeOverviewNode{
		NodeID:                    node.ID,
		Hostname:                  node.Hostname,
		DisplayName:               node.DisplayName,
		Status:                    node.Status,
		ContainerMetricsAvailable: node.Capabilities[models.CapContainerMetrics],
	}
	if !node.LastSeen.IsZero() {
		lastSeen := node.LastSeen.UTC()
		item.LastSeen = &lastSeen
	}

	item.CPU = nativeSampleValue(pickNativeSample(samples, "cpu.usage", "cpu", "all"))
	item.Memory = nativeSampleValue(pickNativeSample(samples, "memory.usage", "", ""))
	item.Load1 = nativeSampleValue(pickNativeSample(samples, "load.1", "", ""))
	item.Load5 = nativeSampleValue(pickNativeSample(samples, "load.5", "", ""))
	item.Load15 = nativeSampleValue(pickNativeSample(samples, "load.15", "", ""))
	item.Disk = nativeSampleValue(pickNativeSample(samples, "disk.usage", "mount", "/"))
	item.NetworkRX = sumNativeMetric(samples, "network.rx_bps")
	item.NetworkTX = sumNativeMetric(samples, "network.tx_bps")
	// 只有 rx/tx 都有有效值时才给出总量，避免把缺失方向当成 0。
	if item.NetworkRX != nil && item.NetworkTX != nil {
		total := *item.NetworkRX + *item.NetworkTX
		item.NetworkTotal = &total
	}
	item.UptimeSeconds = nativeSampleValue(pickNativeSample(samples, "system.uptime", "", ""))
	item.TemperatureC = nativeSampleValue(pickNativeSample(samples, "system.temperature_c", "", ""))

	var observedAt time.Time
	valid := 0
	for _, sample := range samples {
		if sample.Timestamp.After(observedAt) {
			observedAt = sample.Timestamp.UTC()
		}
		if sample.Quality == store.NativeQualityValid && sample.Value != nil {
			valid++
		}
	}
	if !observedAt.IsZero() {
		item.ObservedAt = &observedAt
	}

	switch {
	case nativeNodeOffline(node.Status):
		item.State = "agent_offline"
	case len(samples) == 0:
		item.State = "no_data"
	case valid == 0:
		item.State = "data_gap"
	case nativeSampleStale(observedAt, now, interval):
		item.State = "data_gap"
	default:
		item.State = "ready"
	}
	return item
}

// pickNativeSample 优先返回指定维度匹配的样本；该样本缺失时尽量回退到
// 同指标的第一个有效样本，避免 per-core/per-mount 数据因首选序列缺失而整体为空。
func pickNativeSample(samples []store.NativeSample, metric, dimensionKey, dimensionValue string) *store.NativeSample {
	var preferred, first, firstValid *store.NativeSample
	for index := range samples {
		sample := &samples[index]
		if sample.Metric != metric {
			continue
		}
		if first == nil {
			first = sample
		}
		valid := sample.Quality == store.NativeQualityValid && sample.Value != nil
		if valid && firstValid == nil {
			firstValid = sample
		}
		if dimensionKey != "" && sample.Dimensions[dimensionKey] == dimensionValue {
			if preferred == nil {
				preferred = sample
			}
			if valid {
				return sample
			}
		}
	}
	if preferred != nil && preferred.Quality == store.NativeQualityValid && preferred.Value != nil {
		return preferred
	}
	if firstValid != nil {
		return firstValid
	}
	if preferred != nil {
		return preferred
	}
	return first
}

func nativeSampleValue(sample *store.NativeSample) *float64 {
	if sample == nil || sample.Quality != store.NativeQualityValid || sample.Value == nil {
		return nil
	}
	value := *sample.Value
	if math.IsNaN(value) || math.IsInf(value, 0) {
		return nil
	}
	return &value
}

// nativeNodeOffline 只有明确非 online 的状态才视为离线；空状态按 online
// 处理，避免手工/测试创建的节点因未设置 status 被误判。
func nativeNodeOffline(status string) bool {
	switch status {
	case models.NodeStatusOnline, "":
		return false
	default:
		return true
	}
}

// nativeStaleAfter 按有效采样周期给出最大允许延迟：3 个周期。
func nativeStaleAfter(interval time.Duration) time.Duration {
	if interval <= 0 {
		interval = store.NativeRawStep
	}
	return 3 * interval
}

func nativeSampleStale(timestamp, now time.Time, interval time.Duration) bool {
	if timestamp.IsZero() || timestamp.After(now) {
		return true
	}
	return now.Sub(timestamp) > nativeStaleAfter(interval)
}

func sumNativeMetric(samples []store.NativeSample, metric string) *float64 {
	var total float64
	found := false
	for _, sample := range samples {
		if sample.Metric != metric || sample.Quality != store.NativeQualityValid || sample.Value == nil {
			continue
		}
		value := *sample.Value
		if math.IsNaN(value) || math.IsInf(value, 0) {
			continue
		}
		total += value
		found = true
	}
	if !found {
		return nil
	}
	return &total
}

// handleMonitoringNode 分发 /api/monitoring/nodes/{id}/series|metrics|latest。
func (s *Server) handleMonitoringNode(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		methodNotAllowed(w, "GET")
		return
	}
	if !s.canAdmin(r) {
		writeErr(w, http.StatusForbidden, "permission denied")
		return
	}
	id, rest := splitResourcePath(r.URL.Path, "/api/monitoring/nodes/")
	if id == "" || len(rest) != 1 {
		writeErr(w, http.StatusNotFound, "node not found")
		return
	}
	node, err := s.store.GetNode(r.Context(), id)
	if errors.Is(err, sql.ErrNoRows) {
		writeErr(w, http.StatusNotFound, "node not found")
		return
	}
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	switch rest[0] {
	case "series":
		s.handleMonitoringNodeSeries(w, r, node)
	case "metrics":
		s.handleMonitoringNodeMetrics(w, r, node)
	case "latest":
		s.handleMonitoringNodeLatest(w, r, node)
	default:
		writeErr(w, http.StatusNotFound, "node not found")
	}
}

func (s *Server) handleMonitoringNodeSeries(w http.ResponseWriter, r *http.Request, node *models.Node) {
	nativeStore, ok := s.store.(store.NativeMetricStore)
	if !ok {
		writeErr(w, http.StatusServiceUnavailable, "native monitoring unavailable")
		return
	}
	metric := r.URL.Query().Get("metric")
	if !store.ValidNativeMetric(metric) {
		writeErr(w, http.StatusBadRequest, "invalid metric")
		return
	}
	series, err := nativeStore.NodeMetricSeries(r.Context(), node.ID, metric)
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	if series == nil {
		series = []store.NativeSeries{}
	}
	writeJSON(w, http.StatusOK, nativeSeriesResponse{Series: series})
}

func (s *Server) handleMonitoringNodeMetrics(w http.ResponseWriter, r *http.Request, node *models.Node) {
	reader, ok := s.store.(store.NativeMetricReader)
	if !ok {
		writeErr(w, http.StatusServiceUnavailable, "native monitoring unavailable")
		return
	}
	values := r.URL.Query()
	metric := values.Get("metric")
	if !store.ValidNativeMetric(metric) {
		writeErr(w, http.StatusBadRequest, "invalid metric")
		return
	}
	seriesKey := values.Get("series")
	if seriesKey == "" {
		writeErr(w, http.StatusBadRequest, "series is required")
		return
	}
	if len(seriesKey) > 1024 || hasControlCharacters(seriesKey) {
		writeErr(w, http.StatusBadRequest, "invalid series")
		return
	}

	start, hasStart, err := parseNativeTimeParam(values.Get("start"))
	if err != nil {
		writeErr(w, http.StatusBadRequest, "invalid start")
		return
	}
	end, hasEnd, err := parseNativeTimeParam(values.Get("end"))
	if err != nil {
		writeErr(w, http.StatusBadRequest, "invalid end")
		return
	}
	if hasStart && hasEnd && !start.Before(end) {
		writeErr(w, http.StatusBadRequest, "invalid time range")
		return
	}
	step, _, err := parseNativeStepParam(values.Get("step"))
	if err != nil {
		writeErr(w, http.StatusBadRequest, "invalid step")
		return
	}

	history, err := monitoring.QueryNativeHistory(r.Context(), reader, monitoring.NativeQuery{
		NodeID:    node.ID,
		Metric:    metric,
		SeriesKey: seriesKey,
		Start:     start,
		End:       end,
		Step:      step,
		Offline:   nativeNodeOffline(node.Status),
	})
	if err != nil {
		switch {
		case errors.Is(err, monitoring.ErrNativeInvalidRange):
			writeErr(w, http.StatusBadRequest, "invalid time range")
		case errors.Is(err, monitoring.ErrNativeRangeTooLarge):
			writeErr(w, http.StatusBadRequest, "time range is too large")
		case errors.Is(err, monitoring.ErrNativeInvalidQuery):
			writeErr(w, http.StatusBadRequest, "invalid query")
		default:
			s.writeInternalError(w, r, err)
		}
		return
	}
	writeJSON(w, http.StatusOK, history)
}

func (s *Server) handleMonitoringNodeLatest(w http.ResponseWriter, r *http.Request, node *models.Node) {
	nativeStore, ok := s.store.(store.NativeMetricStore)
	if !ok {
		writeErr(w, http.StatusServiceUnavailable, "native monitoring unavailable")
		return
	}
	samples, err := nativeStore.LatestNodeMetricSamples(r.Context(), node.ID)
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	interval, err := nativeStore.NodeMetricSampleInterval(r.Context(), node.ID)
	if err != nil {
		s.writeInternalError(w, r, err)
		return
	}
	now := time.Now().UTC()
	metrics := make([]nativeLatestMetric, 0, len(samples))
	for _, sample := range samples {
		state := "ready"
		value := nativeSampleValue(&sample)
		switch {
		case nativeNodeOffline(node.Status):
			state = "agent_offline"
		case value == nil:
			state = "data_gap"
		case nativeSampleStale(sample.Timestamp, now, interval):
			state = "data_gap"
		}
		metrics = append(metrics, nativeLatestMetric{
			Metric:     sample.Metric,
			Series:     store.BuildSeriesKey(sample.Metric, sample.Dimensions),
			Value:      value,
			State:      state,
			ObservedAt: sample.Timestamp.UTC(),
		})
	}
	writeJSON(w, http.StatusOK, nativeLatestResponse{
		Metrics:                   metrics,
		ContainerMetricsAvailable: node.Capabilities[models.CapContainerMetrics],
	})
}

func hasControlCharacters(value string) bool {
	for index := 0; index < len(value); index++ {
		if value[index] < 0x20 || value[index] == 0x7f {
			return true
		}
	}
	return false
}

// parseNativeTimeParam 接受 RFC3339/RFC3339Nano 或 Unix 秒/毫秒整数。
func parseNativeTimeParam(raw string) (time.Time, bool, error) {
	if raw == "" {
		return time.Time{}, false, nil
	}
	if parsed, err := time.Parse(time.RFC3339Nano, raw); err == nil {
		return parsed.UTC(), true, nil
	}
	integer, err := strconv.ParseInt(raw, 10, 64)
	if err != nil {
		return time.Time{}, false, errors.New("invalid time")
	}
	if len(raw) >= 13 {
		return time.UnixMilli(integer).UTC(), true, nil
	}

	return time.Unix(integer, 0).UTC(), true, nil
}

// parseNativeStepParam 接受 Go duration（30s/1m/5m/1h）或纳秒整数。
func parseNativeStepParam(raw string) (time.Duration, bool, error) {
	if raw == "" {
		return 0, false, nil
	}
	var step time.Duration
	if parsed, err := time.ParseDuration(raw); err == nil {
		step = parsed
	} else if integer, err := strconv.ParseInt(raw, 10, 64); err == nil {
		// 大整数按纳秒（与 stepNs 响应一致），小整数按秒，兼容 step=60 这类写法。
		if integer >= int64(time.Second) {
			step = time.Duration(integer)
		} else {
			step = time.Duration(integer) * time.Second
		}
	} else {
		return 0, false, errors.New("invalid step")
	}
	if step < time.Second || step > 30*24*time.Hour {
		return 0, false, errors.New("invalid step")
	}
	return step, true, nil
}
