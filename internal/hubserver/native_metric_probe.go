package hubserver

import (
	"context"
	"encoding/json"
	"errors"
	"strconv"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// metricNodeLookup 是原生指标 probe 需要的最小节点读取接口。
type metricNodeLookup interface {
	GetNode(ctx context.Context, id string) (*models.Node, error)
}

// observeNativeNodeMetric 从 Hub 原生样本构造 node_metric 观测。
//
// 只接受最新点新鲜、有效且序列匹配的样本；历史窗口内的原始点按时间排序
// 返回给 ProcessNodeSamples，用于 gap、回填、去重和续租语义。ContinuityStart
// 取最新点之前最近的 gap.End，没有缺口时保持零值。
func observeNativeNodeMetric(ctx context.Context, nodes metricNodeLookup, metrics store.NativeMetricStore, target monitoring.Target, now time.Time) (monitoring.HTTPObservation, error) {
	var empty monitoring.HTTPObservation
	if nodes == nil || metrics == nil {
		return empty, errors.New("native node metric probe unavailable")
	}
	node, err := nodes.GetNode(ctx, target.MonitoredNodeID)
	if err != nil {
		return empty, errors.New("monitored node unavailable")
	}
	if node.Status != models.NodeStatusOnline {
		return empty, errors.New("monitored node offline")
	}

	latestSamples, err := metrics.LatestNodeMetricSamples(ctx, target.MonitoredNodeID)
	if err != nil {
		return empty, errors.New("node metric samples unavailable")
	}
	var latest *store.NativeSample
	for index := range latestSamples {
		sample := &latestSamples[index]
		if sample.Metric == target.Metric && store.BuildSeriesKey(sample.Metric, sample.Dimensions) == target.Series {
			latest = sample
			break
		}
	}
	if latest == nil {
		return empty, errors.New("node metric series has no samples")
	}
	if latest.Timestamp.IsZero() || latest.Timestamp.After(now) {
		return empty, errors.New("node metric latest sample is stale or in the future")
	}
	if latest.Quality != store.NativeQualityValid || latest.Value == nil {
		return empty, errors.New("node metric latest sample is invalid")
	}

	// 初始窗口至少覆盖两个最大允许采样周期，才能从样本推导 300s 采样；
	// 随后用推导周期计算有效 max age，避免 60s/300s 采样被误判过期。
	initialWindow := target.MetricMaxAge()
	if minimum := 2 * store.NativeMaxSampleInterval; initialWindow < minimum {
		initialWindow = minimum
	}
	raw, err := metrics.QueryNodeMetricSamplesLimited(ctx, target.MonitoredNodeID, target.Metric, target.Series,
		latest.Timestamp.Add(-initialWindow), latest.Timestamp.Add(time.Nanosecond), store.NativeMaxQueryRows+1)
	if err != nil {
		return empty, errors.New("node metric samples unavailable")
	}
	if len(raw) == 0 || len(raw) > store.NativeMaxQueryRows {
		return empty, errors.New("node metric sample window is incomplete")
	}
	timestamps := make([]time.Time, 0, len(raw))
	for _, sample := range raw {
		timestamps = append(timestamps, sample.Timestamp)
	}
	sampleInterval := store.NativeIntervalFromTimes(timestamps)
	maxAge := target.MetricMaxAge()
	if minimum := 2 * sampleInterval; maxAge < minimum {
		maxAge = minimum
	}
	if now.Sub(latest.Timestamp) > maxAge {
		return empty, errors.New("node metric latest sample is stale or in the future")
	}
	windowStart := latest.Timestamp.Add(-maxAge)
	first := 0
	for first < len(raw) && raw[first].Timestamp.Before(windowStart) {
		first++
	}
	raw = raw[first:]
	if len(raw) == 0 {
		return empty, errors.New("node metric sample window is incomplete")
	}
	last := raw[len(raw)-1]
	if !last.Timestamp.Equal(latest.Timestamp) || last.Quality != store.NativeQualityValid || last.Value == nil {
		return empty, errors.New("node metric latest sample changed during read")
	}

	samples := make([]monitoring.MetricSample, 0, len(raw))
	for _, sample := range raw {
		item := monitoring.MetricSample{
			ObservedAt: sample.Timestamp.UTC(),
			Valid:      sample.Quality == store.NativeQualityValid && sample.Value != nil,
		}
		if item.Valid {
			item.Value = json.Number(strconv.FormatFloat(*sample.Value, 'f', -1, 64))
		}
		samples = append(samples, item)
	}

	// gap 由原生查询层按同一窗口和推导周期计算；取最新点之前最近的缺口结束时间。
	history, err := monitoring.QueryNativeHistory(ctx, metrics, monitoring.NativeQuery{
		NodeID:         target.MonitoredNodeID,
		Metric:         target.Metric,
		SeriesKey:      target.Series,
		Start:          windowStart,
		End:            latest.Timestamp.Add(time.Nanosecond),
		SampleInterval: sampleInterval,
	})
	if err != nil {
		return empty, errors.New("node metric gaps unavailable")
	}
	var continuityStart time.Time
	for _, gap := range history.Gaps {
		if gap.End.After(latest.Timestamp) {
			continue
		}
		if gap.End.After(continuityStart) {
			continuityStart = gap.End.UTC()
		}
	}

	value := json.Number(strconv.FormatFloat(*latest.Value, 'f', -1, 64))
	return monitoring.HTTPObservation{
		ObservedAt:      latest.Timestamp.UTC(),
		ContinuityStart: continuityStart,
		Value:           value,
		HasValue:        true,
		Samples:         samples,
	}, nil
}
