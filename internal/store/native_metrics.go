package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"math"
	"sort"
	"strings"
	"time"
)

// NodeSteer 原生节点指标存储。
//
// 时间统一以固定宽度 UTC RFC3339Nano 文本保存
// （2006-01-02T15:04:05.000000000Z），因此 SQL 文本比较等价于时间比较。
// Value 为 NULL 表示缺失，绝不把缺失写成 0。

const (
	NativeQualityValid   = 1
	NativeQualityMissing = 2

	NativeTierRaw = "raw"
	NativeTier1m  = "1m"
	NativeTier5m  = "5m"
	NativeTier1h  = "1h"

	// NativeRawStep 是原始指标的设计采样步长，也是聚合桶 expected 的计算基准。
	NativeRawStep = 30 * time.Second
	// NativeMinSampleInterval / NativeMaxSampleInterval 是样本周期推导的边界。
	NativeMinSampleInterval = 5 * time.Second
	NativeMaxSampleInterval = 300 * time.Second

	NativeMaxBatchSamples = 1000
	NativeMaxQueryRows    = 20000
	NativeMaxSeries       = 2000
	// nativeSeriesPageSize 是聚合 series 分页大小，避免单次结果集过大。
	nativeSeriesPageSize = 1000

	NativeMaxFutureSkew = 5 * time.Minute
	NativeMaxSampleAge  = 7 * 24 * time.Hour
	// nativeValidationGrace 吸收调用链上的毫秒级延迟，避免恰好落在边界上的
	// 合法样本被误判；超出边界的样本仍会被拒绝。
	nativeValidationGrace = 30 * time.Second

	NativeRawRetention = 7 * 24 * time.Hour
	Native1mRetention  = 90 * 24 * time.Hour
	Native5mRetention  = 365 * 24 * time.Hour
	Native1hRetention  = 2 * 365 * 24 * time.Hour

	nativeTimeLayout     = "2006-01-02T15:04:05.000000000Z"
	nativeAggregateLimit = 200000
)

var (
	ErrNativeInvalidSample = errors.New("invalid native metric sample")
	ErrNativeBatchTooLarge = errors.New("native metric batch too large")
	ErrNativeSampleTime    = errors.New("native metric sample timestamp out of range")
	ErrNativeInvalidQuery  = errors.New("invalid native metric query")
	ErrNativeSeriesLimit   = errors.New("native metric series limit exceeded")
)

// NativeSample 单点原始样本。Quality=1 表示有效，Quality=2 表示缺失；
// 缺失时 Value 必须为 nil。
type NativeSample struct {
	NodeID     string            `json:"node_id"`
	Timestamp  time.Time         `json:"timestamp"`
	Metric     string            `json:"metric"`
	Dimensions map[string]string `json:"dimensions,omitempty"`
	Value      *float64          `json:"value"`
	Quality    int               `json:"quality"`
}

// NativeSeries 一个指标序列及其维度。
type NativeSeries struct {
	Metric     string            `json:"-"`
	SeriesKey  string            `json:"seriesKey"`
	Dimensions map[string]string `json:"dimensions"`
}

// NativeRollup 聚合桶。Quality 固定为 0，表示这是聚合点而不是原始点；
// 前端按 quality==0 && count>0 识别有效桶。
type NativeRollup struct {
	NodeID      string
	Metric      string
	SeriesKey   string
	Tier        string
	BucketStart time.Time
	Count       uint64
	Expected    uint64
	Missing     uint64
	Sum         float64
	Min         float64
	Max         float64
	First       float64
	Last        float64
	FirstTS     time.Time
	LastTS      time.Time
	Quality     int
}

// NativeMetricReader 是监控查询层需要的最小只读接口。
type NativeMetricReader interface {
	QueryNodeMetricSamplesLimited(ctx context.Context, nodeID, metric, seriesKey string, start, end time.Time, limit int) ([]NativeSample, error)
	QueryNodeMetricRollups(ctx context.Context, nodeID, metric, seriesKey, tier string, start, end time.Time, limit int) ([]NativeRollup, error)
}

// NativeMetricStore 是原生指标存储的完整窄接口，供 API 层断言使用。
type NativeMetricStore interface {
	NativeMetricReader
	InsertNodeMetricSamples(ctx context.Context, nodeID string, samples []NativeSample) error
	NodeMetricSeries(ctx context.Context, nodeID, metric string) ([]NativeSeries, error)
	LatestNodeMetricSamples(ctx context.Context, nodeID string) ([]NativeSample, error)
	LatestNodeMetricSamplesBatch(ctx context.Context, nodeIDs []string) (map[string][]NativeSample, error)
	NodeMetricSampleInterval(ctx context.Context, nodeID string) (time.Duration, error)
	NodeMetricSampleIntervals(ctx context.Context, nodeIDs []string) (map[string]time.Duration, error)
	QueryNodeMetricSamples(ctx context.Context, nodeID, metric, seriesKey string, start, end time.Time) ([]NativeSample, error)
	AggregateNodeMetrics(ctx context.Context, now time.Time) error
	PruneNodeMetrics(ctx context.Context, now time.Time) error
}

var _ NativeMetricStore = (*SQLiteStore)(nil)

// ValidNativeMetric 校验指标名。指标名允许字母、数字、点、下划线和连字符。
func ValidNativeMetric(metric string) bool {
	if metric == "" || len(metric) > 128 {
		return false
	}
	for _, r := range metric {
		switch {
		case r >= 'a' && r <= 'z':
		case r >= 'A' && r <= 'Z':
		case r >= '0' && r <= '9':
		case r == '.', r == '_', r == '-':
		default:
			return false
		}
	}
	return true
}

// ValidNativeTier 校验聚合层级。
func ValidNativeTier(tier string) bool {
	switch tier {
	case NativeTier1m, NativeTier5m, NativeTier1h:
		return true
	default:
		return false
	}
}

// BuildSeriesKey 生成稳定、可解析的序列键：
//
//	metric
//	metric|k1=v1,k2=v2   （维度键排序）
//
// 维度键/值中的反斜杠、逗号、等号和竖线会转义，因此序列键可安全解析。
func BuildSeriesKey(metric string, dimensions map[string]string) string {
	keys := make([]string, 0, len(dimensions))
	for key := range dimensions {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	var builder strings.Builder
	builder.WriteString(metric)
	if len(keys) == 0 {
		return builder.String()
	}
	builder.WriteByte('|')
	for index, key := range keys {
		if index > 0 {
			builder.WriteByte(',')
		}
		builder.WriteString(escapeSeriesPart(key))
		builder.WriteByte('=')
		builder.WriteString(escapeSeriesPart(dimensions[key]))
	}
	return builder.String()
}

// ParseSeriesKey 解析 BuildSeriesKey 生成的序列键。
func ParseSeriesKey(key string) (string, map[string]string, bool) {
	dimensions := map[string]string{}
	separator := strings.IndexByte(key, '|')
	if separator < 0 {
		if !ValidNativeMetric(key) {
			return "", nil, false
		}
		return key, dimensions, true
	}
	metric := key[:separator]
	if !ValidNativeMetric(metric) || separator == len(key)-1 {
		return "", nil, false
	}
	for _, part := range splitEscaped(key[separator+1:], ',') {
		pair := splitEscaped(part, '=')
		if len(pair) != 2 || pair[0] == "" {
			return "", nil, false
		}
		name := unescapeSeriesPart(pair[0])
		if name == "" {
			return "", nil, false
		}
		dimensions[name] = unescapeSeriesPart(pair[1])
	}
	return metric, dimensions, true
}

// NativeIntervalFromTimes 从样本时间推导有效采样周期。
// 取相邻时间差（忽略非正差值）的下中位数，并限制在 5s–300s；
// 样本不足或无法推导时回退 NativeRawStep。使用下中位数可以在样本较少
// 且包含断档时仍保留正常采样周期，而不会被单个大缺口拉高。
func NativeIntervalFromTimes(times []time.Time) time.Duration {
	if len(times) < 2 {
		return NativeRawStep
	}
	sorted := make([]time.Time, len(times))
	copy(sorted, times)
	sort.Slice(sorted, func(left, right int) bool { return sorted[left].Before(sorted[right]) })
	diffs := make([]time.Duration, 0, len(sorted)-1)
	for index := 1; index < len(sorted); index++ {
		if diff := sorted[index].Sub(sorted[index-1]); diff > 0 {
			diffs = append(diffs, diff)
		}
	}
	if len(diffs) == 0 {
		return NativeRawStep
	}
	sort.Slice(diffs, func(left, right int) bool { return diffs[left] < diffs[right] })
	interval := diffs[(len(diffs)-1)/2]
	if interval < NativeMinSampleInterval {
		interval = NativeMinSampleInterval
	}
	if interval > NativeMaxSampleInterval {
		interval = NativeMaxSampleInterval
	}
	return interval
}

// NativeExpectedCount 返回一个 step 桶在给定采样周期下期望的样本数，至少为 1。
func NativeExpectedCount(step, interval time.Duration) uint64 {
	if step <= 0 {
		return 0
	}
	if interval <= 0 {
		interval = NativeRawStep
	}
	expected := int64((step + interval/2) / interval)
	if expected < 1 {
		expected = 1
	}
	return uint64(expected)
}

func nativeFinite(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}

func escapeSeriesPart(value string) string {
	value = strings.ReplaceAll(value, `\`, `\\`)
	value = strings.ReplaceAll(value, `,`, `\,`)
	value = strings.ReplaceAll(value, `=`, `\=`)
	value = strings.ReplaceAll(value, `|`, `\|`)
	return value
}

// splitEscaped 按未转义的分隔符切分，保留转义序列供后续 unescape 使用。
func splitEscaped(value string, separator byte) []string {
	parts := make([]string, 0, 2)
	var builder strings.Builder
	escaped := false
	for index := 0; index < len(value); index++ {
		character := value[index]
		if escaped {
			builder.WriteByte('\\')
			builder.WriteByte(character)
			escaped = false
			continue
		}
		if character == '\\' {
			escaped = true
			continue
		}
		if character == separator {
			parts = append(parts, builder.String())
			builder.Reset()
			continue
		}
		builder.WriteByte(character)
	}
	if escaped {
		builder.WriteByte('\\')
	}
	parts = append(parts, builder.String())
	return parts
}

func unescapeSeriesPart(value string) string {
	var builder strings.Builder
	escaped := false
	for index := 0; index < len(value); index++ {
		character := value[index]
		if escaped {
			builder.WriteByte(character)
			escaped = false
			continue
		}
		if character == '\\' {
			escaped = true
			continue
		}
		builder.WriteByte(character)
	}
	if escaped {
		builder.WriteByte('\\')
	}
	return builder.String()
}

func validNativeNodeID(nodeID string) bool {
	if nodeID == "" || len(nodeID) > 128 {
		return false
	}
	return !strings.ContainsAny(nodeID, "\x00\r\n")
}

func validNativeSeriesKey(key string) bool {
	return key == "" || (len(key) <= 1024 && !strings.ContainsAny(key, "\x00\r\n"))
}

func nativeSeriesKey(metric string, dimensions map[string]string) (string, error) {
	if len(dimensions) > 16 {
		return "", errors.New("too many dimensions")
	}
	for key, value := range dimensions {
		if key == "" || len(key) > 64 || len(value) > 256 {
			return "", errors.New("dimension is too long")
		}
		if strings.ContainsAny(key, "\x00\r\n") || strings.ContainsAny(value, "\x00\r\n") {
			return "", errors.New("dimension contains control characters")
		}
	}
	seriesKey := BuildSeriesKey(metric, dimensions)
	if len(seriesKey) > 1024 {
		return "", errors.New("series key is too long")
	}
	return seriesKey, nil
}

func formatNativeTime(value time.Time) string {
	return value.UTC().Format(nativeTimeLayout)
}

func parseNativeTime(value string) (time.Time, error) {
	return time.Parse(nativeTimeLayout, value)
}

func nativeDimensionsJSON(dimensions map[string]string) (string, error) {
	if dimensions == nil {
		dimensions = map[string]string{}
	}
	encoded, err := json.Marshal(dimensions)
	if err != nil {
		return "", err
	}
	if len(encoded) > 4096 {
		return "", errors.New("dimensions are too large")
	}
	return string(encoded), nil
}

func decodeNativeDimensions(encoded string) map[string]string {
	dimensions := map[string]string{}
	if encoded != "" {
		_ = json.Unmarshal([]byte(encoded), &dimensions)
	}
	if dimensions == nil {
		dimensions = map[string]string{}
	}
	return dimensions
}

// nativeDimensionsForSeries 优先使用 dimensions_json；该列为空或损坏时
// 从可解析的 series_key 还原，保证读路径不因单列异常丢失维度。
func nativeDimensionsForSeries(seriesKey, encoded string) map[string]string {
	dimensions := decodeNativeDimensions(encoded)
	if len(dimensions) == 0 {
		if _, parsed, ok := ParseSeriesKey(seriesKey); ok {
			return parsed
		}
	}
	return dimensions
}

func clampNativeQueryLimit(limit int) int {
	if limit <= 0 {
		return NativeMaxQueryRows
	}
	// 查询层会请求 NativeMaxQueryRows+1 用于检测超预算；允许该上限。
	if limit > NativeMaxQueryRows+1 {
		return NativeMaxQueryRows + 1
	}
	return limit
}

// InsertNodeMetricSamples 在单个事务内批量写入样本。
// 同一主键重复写入采用 UPSERT，后写入的 value/quality/dimensions 覆盖旧值。
func (s *SQLiteStore) InsertNodeMetricSamples(ctx context.Context, nodeID string, samples []NativeSample) error {
	if !validNativeNodeID(nodeID) {
		return fmt.Errorf("%w: invalid node id", ErrNativeInvalidSample)
	}
	if len(samples) > NativeMaxBatchSamples {
		return fmt.Errorf("%w: %d samples (max %d)", ErrNativeBatchTooLarge, len(samples), NativeMaxBatchSamples)
	}
	if len(samples) == 0 {
		return nil
	}

	type preparedSample struct {
		metric     string
		seriesKey  string
		timestamp  string
		value      any
		quality    int
		dimensions string
	}
	prepared := make([]preparedSample, 0, len(samples))
	now := time.Now().UTC()
	for _, sample := range samples {
		if sample.NodeID != "" && sample.NodeID != nodeID {
			return fmt.Errorf("%w: sample node id does not match", ErrNativeInvalidSample)
		}
		if !ValidNativeMetric(sample.Metric) {
			return fmt.Errorf("%w: invalid metric", ErrNativeInvalidSample)
		}
		if sample.Timestamp.IsZero() {
			return fmt.Errorf("%w: zero timestamp", ErrNativeInvalidSample)
		}
		timestamp := sample.Timestamp.UTC()
		if timestamp.After(now.Add(NativeMaxFutureSkew+nativeValidationGrace)) || timestamp.Before(now.Add(-NativeMaxSampleAge-nativeValidationGrace)) {
			return fmt.Errorf("%w: %s", ErrNativeSampleTime, timestamp.Format(time.RFC3339Nano))
		}
		if sample.Quality != NativeQualityValid && sample.Quality != NativeQualityMissing {
			return fmt.Errorf("%w: invalid quality %d", ErrNativeInvalidSample, sample.Quality)
		}
		var value any
		if sample.Value != nil {
			numeric := *sample.Value
			if math.IsNaN(numeric) || math.IsInf(numeric, 0) {
				return fmt.Errorf("%w: non-finite value", ErrNativeInvalidSample)
			}
			value = numeric
		}
		if sample.Quality == NativeQualityValid && value == nil {
			return fmt.Errorf("%w: valid sample requires a value", ErrNativeInvalidSample)
		}
		if sample.Quality == NativeQualityMissing && value != nil {
			return fmt.Errorf("%w: missing sample must not carry a value", ErrNativeInvalidSample)
		}
		seriesKey, err := nativeSeriesKey(sample.Metric, sample.Dimensions)
		if err != nil {
			return fmt.Errorf("%w: %v", ErrNativeInvalidSample, err)
		}
		dimensions, err := nativeDimensionsJSON(sample.Dimensions)
		if err != nil {
			return fmt.Errorf("%w: %v", ErrNativeInvalidSample, err)
		}
		prepared = append(prepared, preparedSample{
			metric:     sample.Metric,
			seriesKey:  seriesKey,
			timestamp:  formatNativeTime(timestamp),
			value:      value,
			quality:    sample.Quality,
			dimensions: dimensions,
		})
	}

	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	// 每节点 series 基数上限：只拒绝新 series，已有 series 的更新继续允许。
	existingSeries := map[string]struct{}{}
	rows, err := tx.QueryContext(ctx, `SELECT series_key FROM (
			SELECT series_key FROM node_metric_samples WHERE node_id = ?
			UNION
			SELECT series_key FROM node_metric_rollups WHERE node_id = ?
		) LIMIT ?`, nodeID, nodeID, NativeMaxSeries+1)
	if err != nil {
		return err
	}
	for rows.Next() {
		var key string
		if err := rows.Scan(&key); err != nil {
			rows.Close()
			return err
		}
		existingSeries[key] = struct{}{}
	}
	if err := rows.Close(); err != nil {
		return err
	}
	if err := rows.Err(); err != nil {
		return err
	}
	newSeries := map[string]struct{}{}
	for _, sample := range prepared {
		if _, ok := existingSeries[sample.seriesKey]; ok {
			continue
		}
		if _, ok := newSeries[sample.seriesKey]; ok {
			continue
		}
		if len(existingSeries)+len(newSeries) >= NativeMaxSeries {
			return fmt.Errorf("%w: node %s has reached %d series", ErrNativeSeriesLimit, nodeID, NativeMaxSeries)
		}
		newSeries[sample.seriesKey] = struct{}{}
	}

	statement, err := tx.PrepareContext(ctx, `
		INSERT INTO node_metric_samples (node_id, metric, series_key, timestamp, value, quality, dimensions_json)
		VALUES (?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(node_id, metric, series_key, timestamp) DO UPDATE SET
			value = excluded.value,
			quality = excluded.quality,
			dimensions_json = excluded.dimensions_json`)
	if err != nil {
		return err
	}
	defer statement.Close()

	for _, sample := range prepared {
		if _, err := statement.ExecContext(ctx, nodeID, sample.metric, sample.seriesKey, sample.timestamp, sample.value, sample.quality, sample.dimensions); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// NodeMetricSeries 返回节点的序列列表。原始样本已裁剪但聚合桶仍存在时，
// 会从聚合表补充序列，并从 series_key 还原维度。
func (s *SQLiteStore) NodeMetricSeries(ctx context.Context, nodeID, metric string) ([]NativeSeries, error) {
	if !validNativeNodeID(nodeID) {
		return nil, fmt.Errorf("%w: invalid node id", ErrNativeInvalidQuery)
	}
	if metric != "" && !ValidNativeMetric(metric) {
		return nil, fmt.Errorf("%w: invalid metric", ErrNativeInvalidQuery)
	}

	dimensionsByKey := map[string]map[string]string{}
	order := make([]string, 0, 8)
	add := func(seriesKey string, dimensions map[string]string) {
		if _, exists := dimensionsByKey[seriesKey]; exists {
			return
		}
		if dimensions == nil {
			dimensions = map[string]string{}
		}
		dimensionsByKey[seriesKey] = dimensions
		order = append(order, seriesKey)
	}

	rawQuery := `SELECT series_key, MAX(dimensions_json) FROM node_metric_samples WHERE node_id = ?`
	rawArgs := []any{nodeID}
	if metric != "" {
		rawQuery += ` AND metric = ?`
		rawArgs = append(rawArgs, metric)
	}
	rawQuery += ` GROUP BY series_key ORDER BY series_key LIMIT ?`
	rawArgs = append(rawArgs, NativeMaxSeries)
	rows, err := s.queryer(ctx).QueryContext(ctx, rawQuery, rawArgs...)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var seriesKey, encoded string
		if err := rows.Scan(&seriesKey, &encoded); err != nil {
			rows.Close()
			return nil, err
		}
		add(seriesKey, nativeDimensionsForSeries(seriesKey, encoded))
	}
	if err := rows.Close(); err != nil {
		return nil, err
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	rollupQuery := `SELECT DISTINCT series_key FROM node_metric_rollups WHERE node_id = ?`
	rollupArgs := []any{nodeID}
	if metric != "" {
		rollupQuery += ` AND metric = ?`
		rollupArgs = append(rollupArgs, metric)
	}
	rollupQuery += ` ORDER BY series_key LIMIT ?`
	rollupArgs = append(rollupArgs, NativeMaxSeries)
	rows, err = s.queryer(ctx).QueryContext(ctx, rollupQuery, rollupArgs...)
	if err != nil {
		return nil, err
	}
	for rows.Next() {
		var seriesKey string
		if err := rows.Scan(&seriesKey); err != nil {
			rows.Close()
			return nil, err
		}
		if _, exists := dimensionsByKey[seriesKey]; exists {
			continue
		}
		_, dimensions, ok := ParseSeriesKey(seriesKey)
		if !ok {
			dimensions = map[string]string{}
		}
		add(seriesKey, dimensions)
	}
	if err := rows.Close(); err != nil {
		return nil, err
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}

	sort.Strings(order)
	if len(order) > NativeMaxSeries {
		order = order[:NativeMaxSeries]
	}
	series := make([]NativeSeries, 0, len(order))
	for _, seriesKey := range order {
		seriesMetric, _, ok := ParseSeriesKey(seriesKey)
		if !ok || metric != "" {
			seriesMetric = metric
		}
		series = append(series, NativeSeries{
			Metric:     seriesMetric,
			SeriesKey:  seriesKey,
			Dimensions: dimensionsByKey[seriesKey],
		})
	}
	return series, nil
}

// LatestNodeMetricSamples 返回每个 metric/series 的最新样本（包含缺失样本）。
func (s *SQLiteStore) LatestNodeMetricSamples(ctx context.Context, nodeID string) ([]NativeSample, error) {
	samplesByNode, err := s.LatestNodeMetricSamplesBatch(ctx, []string{nodeID})
	if err != nil {
		return nil, err
	}
	return samplesByNode[nodeID], nil
}

// LatestNodeMetricSamplesBatch 用单条 SQL 返回多个节点各自每个 metric/series 的
// 最新样本，避免 overview 等批量视图逐节点查询造成 N+1。
func (s *SQLiteStore) LatestNodeMetricSamplesBatch(ctx context.Context, nodeIDs []string) (map[string][]NativeSample, error) {
	result := make(map[string][]NativeSample, len(nodeIDs))
	if len(nodeIDs) == 0 {
		return result, nil
	}
	unique := make([]string, 0, len(nodeIDs))
	seen := make(map[string]bool, len(nodeIDs))
	for _, nodeID := range nodeIDs {
		if !validNativeNodeID(nodeID) {
			return nil, fmt.Errorf("%w: invalid node id", ErrNativeInvalidQuery)
		}
		if seen[nodeID] {
			continue
		}
		seen[nodeID] = true
		unique = append(unique, nodeID)
		result[nodeID] = []NativeSample{}
	}

	placeholders := strings.TrimSuffix(strings.Repeat("?,", len(unique)), ",")
	args := make([]any, 0, len(unique))
	for _, nodeID := range unique {
		args = append(args, nodeID)
	}
	rows, err := s.queryer(ctx).QueryContext(ctx, fmt.Sprintf(`
		SELECT s.node_id, s.metric, s.series_key, s.timestamp, s.value, s.quality, s.dimensions_json
		FROM node_metric_samples s
		JOIN (
			SELECT node_id, metric, series_key, MAX(timestamp) AS max_timestamp
			FROM node_metric_samples
			WHERE node_id IN (%s)
			GROUP BY node_id, metric, series_key
		) latest ON latest.node_id = s.node_id
			AND latest.metric = s.metric
			AND latest.series_key = s.series_key
			AND latest.max_timestamp = s.timestamp
		ORDER BY s.node_id, s.metric, s.series_key`, placeholders), args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	for rows.Next() {
		var sample NativeSample
		var seriesKey, timestamp, dimensions string
		var value sql.NullFloat64
		if err := rows.Scan(&sample.NodeID, &sample.Metric, &seriesKey, &timestamp, &value, &sample.Quality, &dimensions); err != nil {
			return nil, err
		}
		parsed, err := parseNativeTime(timestamp)
		if err != nil {
			return nil, err
		}
		sample.Timestamp = parsed
		if value.Valid {
			numeric := value.Float64
			sample.Value = &numeric
		}
		sample.Dimensions = nativeDimensionsForSeries(seriesKey, dimensions)
		result[sample.NodeID] = append(result[sample.NodeID], sample)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	return result, nil
}

// NodeMetricSampleInterval 从最近 6 小时的 distinct 原始时间戳推导节点采样周期。
// 同一批次内多个 metric 共享时间戳，使用 DISTINCT 避免把并发序列误当成高频采样。
func (s *SQLiteStore) NodeMetricSampleInterval(ctx context.Context, nodeID string) (time.Duration, error) {
	intervals, err := s.NodeMetricSampleIntervals(ctx, []string{nodeID})
	if err != nil {
		return 0, err
	}
	return intervals[nodeID], nil
}

// NodeMetricSampleIntervals 用单条 SQL 批量推导多个节点的采样周期。
// 每个节点最多取最近 500 个 distinct 时间戳，与单节点查询语义一致。
func (s *SQLiteStore) NodeMetricSampleIntervals(ctx context.Context, nodeIDs []string) (map[string]time.Duration, error) {
	result := make(map[string]time.Duration, len(nodeIDs))
	if len(nodeIDs) == 0 {
		return result, nil
	}
	unique := make([]string, 0, len(nodeIDs))
	seen := make(map[string]bool, len(nodeIDs))
	for _, nodeID := range nodeIDs {
		if !validNativeNodeID(nodeID) {
			return nil, fmt.Errorf("%w: invalid node id", ErrNativeInvalidQuery)
		}
		if seen[nodeID] {
			continue
		}
		seen[nodeID] = true
		unique = append(unique, nodeID)
		result[nodeID] = NativeRawStep
	}

	placeholders := strings.TrimSuffix(strings.Repeat("?,", len(unique)), ",")
	args := make([]any, 0, len(unique)+2)
	for _, nodeID := range unique {
		args = append(args, nodeID)
	}
	args = append(args, formatNativeTime(time.Now().UTC().Add(-6*time.Hour)), 500)
	rows, err := s.queryer(ctx).QueryContext(ctx, fmt.Sprintf(`
		SELECT node_id, timestamp FROM (
			SELECT node_id, timestamp,
				ROW_NUMBER() OVER (PARTITION BY node_id ORDER BY timestamp DESC) AS rn
			FROM (
				SELECT DISTINCT node_id, timestamp FROM node_metric_samples
				WHERE node_id IN (%s) AND timestamp >= ?
			)
		)
		WHERE rn <= ?
		ORDER BY node_id, timestamp`, placeholders), args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	timestampsByNode := make(map[string][]time.Time, len(unique))
	for rows.Next() {
		var nodeID, raw string
		if err := rows.Scan(&nodeID, &raw); err != nil {
			return nil, err
		}
		parsed, err := parseNativeTime(raw)
		if err != nil {
			return nil, err
		}
		timestampsByNode[nodeID] = append(timestampsByNode[nodeID], parsed)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	for nodeID, timestamps := range timestampsByNode {
		result[nodeID] = NativeIntervalFromTimes(timestamps)
	}
	return result, nil
}

// QueryNodeMetricSamples 查询原始样本，默认最多 NativeMaxQueryRows 行。
func (s *SQLiteStore) QueryNodeMetricSamples(ctx context.Context, nodeID, metric, seriesKey string, start, end time.Time) ([]NativeSample, error) {
	return s.QueryNodeMetricSamplesLimited(ctx, nodeID, metric, seriesKey, start, end, NativeMaxQueryRows)
}

// QueryNodeMetricSamplesLimited 查询原始样本，limit 为行数上限。
// metric/seriesKey 为空表示不过滤。
func (s *SQLiteStore) QueryNodeMetricSamplesLimited(ctx context.Context, nodeID, metric, seriesKey string, start, end time.Time, limit int) ([]NativeSample, error) {
	if !validNativeNodeID(nodeID) {
		return nil, fmt.Errorf("%w: invalid node id", ErrNativeInvalidQuery)
	}
	if metric != "" && !ValidNativeMetric(metric) {
		return nil, fmt.Errorf("%w: invalid metric", ErrNativeInvalidQuery)
	}
	if !validNativeSeriesKey(seriesKey) {
		return nil, fmt.Errorf("%w: invalid series key", ErrNativeInvalidQuery)
	}
	if !start.IsZero() && !end.IsZero() && !start.Before(end) {
		return nil, fmt.Errorf("%w: invalid time range", ErrNativeInvalidQuery)
	}

	query := `SELECT node_id, metric, series_key, timestamp, value, quality, dimensions_json
		FROM node_metric_samples WHERE node_id = ?`
	args := []any{nodeID}
	if metric != "" {
		query += ` AND metric = ?`
		args = append(args, metric)
	}
	if seriesKey != "" {
		query += ` AND series_key = ?`
		args = append(args, seriesKey)
	}
	if !start.IsZero() {
		query += ` AND timestamp >= ?`
		args = append(args, formatNativeTime(start))
	}
	if !end.IsZero() {
		query += ` AND timestamp < ?`
		args = append(args, formatNativeTime(end))
	}
	query += ` ORDER BY metric, series_key, timestamp LIMIT ?`
	args = append(args, clampNativeQueryLimit(limit))

	rows, err := s.queryer(ctx).QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	samples := make([]NativeSample, 0, 64)
	for rows.Next() {
		var sample NativeSample
		var seriesKey, timestamp, dimensions string
		var value sql.NullFloat64
		if err := rows.Scan(&sample.NodeID, &sample.Metric, &seriesKey, &timestamp, &value, &sample.Quality, &dimensions); err != nil {
			return nil, err
		}
		parsed, err := parseNativeTime(timestamp)
		if err != nil {
			return nil, err
		}
		sample.Timestamp = parsed
		if value.Valid {
			numeric := value.Float64
			sample.Value = &numeric
		}
		sample.Dimensions = nativeDimensionsForSeries(seriesKey, dimensions)
		samples = append(samples, sample)
	}
	return samples, rows.Err()
}

// QueryNodeMetricRollups 查询指定 tier 的聚合桶，limit 为行数上限。
func (s *SQLiteStore) QueryNodeMetricRollups(ctx context.Context, nodeID, metric, seriesKey, tier string, start, end time.Time, limit int) ([]NativeRollup, error) {
	if !validNativeNodeID(nodeID) {
		return nil, fmt.Errorf("%w: invalid node id", ErrNativeInvalidQuery)
	}
	if metric != "" && !ValidNativeMetric(metric) {
		return nil, fmt.Errorf("%w: invalid metric", ErrNativeInvalidQuery)
	}
	if !validNativeSeriesKey(seriesKey) {
		return nil, fmt.Errorf("%w: invalid series key", ErrNativeInvalidQuery)
	}
	if !ValidNativeTier(tier) {
		return nil, fmt.Errorf("%w: invalid tier", ErrNativeInvalidQuery)
	}
	if !start.IsZero() && !end.IsZero() && !start.Before(end) {
		return nil, fmt.Errorf("%w: invalid time range", ErrNativeInvalidQuery)
	}

	query := `SELECT node_id, metric, series_key, tier, bucket_start,
			"count", "expected", "missing", "sum", "min", "max", "first", "last",
			first_ts, last_ts, quality
		FROM node_metric_rollups WHERE node_id = ? AND tier = ?`
	args := []any{nodeID, tier}
	if metric != "" {
		query += ` AND metric = ?`
		args = append(args, metric)
	}
	if seriesKey != "" {
		query += ` AND series_key = ?`
		args = append(args, seriesKey)
	}
	if !start.IsZero() {
		query += ` AND bucket_start >= ?`
		args = append(args, formatNativeTime(start))
	}
	if !end.IsZero() {
		query += ` AND bucket_start < ?`
		args = append(args, formatNativeTime(end))
	}
	query += ` ORDER BY metric, series_key, bucket_start LIMIT ?`
	args = append(args, clampNativeQueryLimit(limit))

	rows, err := s.queryer(ctx).QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	rollups := make([]NativeRollup, 0, 64)
	for rows.Next() {
		var rollup NativeRollup
		var bucketStart, firstTS, lastTS string
		var count, expected, missing int64
		if err := rows.Scan(&rollup.NodeID, &rollup.Metric, &rollup.SeriesKey, &rollup.Tier,
			&bucketStart, &count, &expected, &missing,
			&rollup.Sum, &rollup.Min, &rollup.Max, &rollup.First, &rollup.Last,
			&firstTS, &lastTS, &rollup.Quality); err != nil {
			return nil, err
		}
		parsed, err := parseNativeTime(bucketStart)
		if err != nil {
			return nil, err
		}
		rollup.BucketStart = parsed
		parsed, err = parseNativeTime(firstTS)
		if err != nil {
			return nil, err
		}
		rollup.FirstTS = parsed
		parsed, err = parseNativeTime(lastTS)
		if err != nil {
			return nil, err
		}
		rollup.LastTS = parsed
		if count > 0 {
			rollup.Count = uint64(count)
		}
		if expected > 0 {
			rollup.Expected = uint64(expected)
		}
		if missing > 0 {
			rollup.Missing = uint64(missing)
		}
		rollups = append(rollups, rollup)
	}
	return rollups, rows.Err()
}

const nativeSourceRaw = "raw"

type nativeAggregateSpec struct {
	tier            string
	step            time.Duration
	source          string
	sourceStep      time.Duration
	sourceRetention time.Duration
	initialLookback time.Duration
}

type nativeAggregateSeriesKey struct {
	nodeID    string
	metric    string
	seriesKey string
}

type nativeBucket struct {
	start   time.Time
	count   uint64
	sum     float64
	min     float64
	max     float64
	first   float64
	last    float64
	firstTS time.Time
	lastTS  time.Time
}

func (bucket *nativeBucket) addRaw(value float64, timestamp time.Time) {
	if bucket.count == 0 {
		bucket.min = value
		bucket.max = value
		bucket.first = value
		bucket.firstTS = timestamp
	} else {
		if value < bucket.min {
			bucket.min = value
		}
		if value > bucket.max {
			bucket.max = value
		}
	}
	bucket.count++
	bucket.sum += value
	bucket.last = value
	bucket.lastTS = timestamp
}

func (bucket *nativeBucket) addRollup(count uint64, sum, minimum, maximum, first, last float64, firstTS, lastTS time.Time) {
	if bucket.count == 0 {
		bucket.min = minimum
		bucket.max = maximum
		bucket.first = first
		bucket.firstTS = firstTS
	} else {
		if minimum < bucket.min {
			bucket.min = minimum
		}
		if maximum > bucket.max {
			bucket.max = maximum
		}
	}
	bucket.count += count
	bucket.sum += sum
	bucket.last = last
	bucket.lastTS = lastTS
}

// AggregateNodeMetrics 执行 raw -> 1m -> 5m -> 1h 聚合。
//
// 每个 tier 从已有最大桶向前重叠两个桶重新计算，避免把正在写入的桶永久固化；
// 首次聚合从来源保留窗口回填。只写入 count>0 的桶，空桶不写入，查询层据此生成 gap。
func (s *SQLiteStore) AggregateNodeMetrics(ctx context.Context, now time.Time) error {
	now = now.UTC()
	if now.IsZero() {
		now = time.Now().UTC()
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	specs := []nativeAggregateSpec{
		{
			tier:            NativeTier1m,
			step:            time.Minute,
			source:          nativeSourceRaw,
			sourceRetention: NativeRawRetention,
			initialLookback: NativeRawRetention,
		},
		{
			tier:            NativeTier5m,
			step:            5 * time.Minute,
			source:          NativeTier1m,
			sourceStep:      time.Minute,
			sourceRetention: Native1mRetention,
			initialLookback: Native1mRetention,
		},
		{
			tier:            NativeTier1h,
			step:            time.Hour,
			source:          NativeTier5m,
			sourceStep:      5 * time.Minute,
			sourceRetention: Native5mRetention,
			initialLookback: Native5mRetention,
		},
	}
	for _, spec := range specs {
		if err := aggregateNativeTier(ctx, tx, spec, now); err != nil {
			return err
		}
	}
	return tx.Commit()
}

func aggregateNativeTier(ctx context.Context, tx *sql.Tx, spec nativeAggregateSpec, now time.Time) error {
	end := now.Truncate(spec.step)
	if end.IsZero() {
		return nil
	}
	sourceStart := now.Add(-spec.sourceRetention)
	return nativeAggregateSeries(ctx, tx, spec, sourceStart, end, func(item nativeAggregateSeriesKey) error {
		last, err := nativeLastRollupBucket(ctx, tx, item, spec.tier)
		if err != nil {
			return err
		}
		start := now.Add(-spec.initialLookback)
		if !last.IsZero() {
			// 向前重叠 10 个桶，吸收 Agent 断线缓冲/重连回填造成的晚到样本。
			start = last.Add(-10 * spec.step)
		}
		if start.Before(sourceStart) {
			start = sourceStart
		}
		start = start.Truncate(spec.step)
		if !start.Before(end) {
			return nil
		}
		expected := NativeExpectedCount(spec.step, spec.sourceStep)
		if spec.source == nativeSourceRaw {
			interval, err := nativeRawSeriesInterval(ctx, tx, item, sourceStart, end)
			if err != nil {
				return err
			}
			expected = NativeExpectedCount(spec.step, interval)
		}
		// 重算窗口内先删除目标桶，避免 valid→missing 等更新后旧 rollup 残留。
		if err := nativeDeleteRollupWindow(ctx, tx, item, spec.tier, start, end); err != nil {
			return err
		}
		buckets, err := nativeReadBuckets(ctx, tx, spec, item, start, end)
		if err != nil {
			return err
		}
		return nativeWriteBuckets(ctx, tx, item, spec, expected, buckets)
	})
}

// nativeRawSeriesInterval 从最近原始样本时间戳推导单个 series 的采样周期。
// 倒序限制 200 条即可覆盖 300s 采样下的 16 小时，足够稳定推导。
func nativeRawSeriesInterval(ctx context.Context, tx *sql.Tx, series nativeAggregateSeriesKey, start, end time.Time) (time.Duration, error) {
	rows, err := tx.QueryContext(ctx, `SELECT timestamp FROM node_metric_samples
		WHERE node_id = ? AND metric = ? AND series_key = ?
			AND timestamp >= ? AND timestamp < ?
		ORDER BY timestamp DESC LIMIT ?`,
		series.nodeID, series.metric, series.seriesKey, formatNativeTime(start), formatNativeTime(end), 200)
	if err != nil {
		return 0, err
	}
	defer rows.Close()
	timestamps := make([]time.Time, 0, 32)
	for rows.Next() {
		var raw string
		if err := rows.Scan(&raw); err != nil {
			return 0, err
		}
		parsed, err := parseNativeTime(raw)
		if err != nil {
			return 0, err
		}
		timestamps = append(timestamps, parsed)
	}
	if err := rows.Err(); err != nil {
		return 0, err
	}
	for left, right := 0, len(timestamps)-1; left < right; left, right = left+1, right-1 {
		timestamps[left], timestamps[right] = timestamps[right], timestamps[left]
	}
	return NativeIntervalFromTimes(timestamps), nil
}

// nativeDeleteRollupWindow 删除重算窗口内的目标桶，并级联删除依赖该窗口的
// 更粗 tier 桶。级联只删除下一次聚合会重算的桶（bucket_start < end.Truncate(step)），
// 避免删除尚未到聚合边界的当前桶。
func nativeDeleteRollupWindow(ctx context.Context, tx *sql.Tx, series nativeAggregateSeriesKey, tier string, start, end time.Time) error {
	if _, err := tx.ExecContext(ctx, `DELETE FROM node_metric_rollups
		WHERE node_id = ? AND metric = ? AND series_key = ? AND tier = ?
			AND bucket_start >= ? AND bucket_start < ?`,
		series.nodeID, series.metric, series.seriesKey, tier,
		formatNativeTime(start), formatNativeTime(end)); err != nil {
		return err
	}
	dependents := map[string]time.Duration{}
	switch tier {
	case NativeTier1m:
		dependents[NativeTier5m] = 5 * time.Minute
		dependents[NativeTier1h] = time.Hour
	case NativeTier5m:
		dependents[NativeTier1h] = time.Hour
	}
	for dependent, step := range dependents {
		dependentStart := start.Truncate(step)
		dependentEnd := end.Truncate(step)
		if !dependentStart.Before(dependentEnd) {
			continue
		}
		if _, err := tx.ExecContext(ctx, `DELETE FROM node_metric_rollups
			WHERE node_id = ? AND metric = ? AND series_key = ? AND tier = ?
				AND bucket_start >= ? AND bucket_start < ?`,
			series.nodeID, series.metric, series.seriesKey, dependent,
			formatNativeTime(dependentStart), formatNativeTime(dependentEnd)); err != nil {
			return err
		}
	}
	return nil
}

func nativeAggregateSeries(ctx context.Context, tx *sql.Tx, spec nativeAggregateSpec, start, end time.Time, visit func(nativeAggregateSeriesKey) error) error {
	lastNode, lastMetric, lastSeries := "", "", ""
	for {
		query := ""
		args := []any{}
		if spec.source == nativeSourceRaw {
			query = `SELECT DISTINCT node_id, metric, series_key FROM node_metric_samples
				WHERE timestamp >= ? AND timestamp < ?`
			args = append(args, formatNativeTime(start), formatNativeTime(end))
		} else {
			query = `SELECT DISTINCT node_id, metric, series_key FROM node_metric_rollups
				WHERE tier = ? AND bucket_start >= ? AND bucket_start < ?`
			args = append(args, spec.source, formatNativeTime(start), formatNativeTime(end))
		}
		if lastNode != "" || lastMetric != "" || lastSeries != "" {
			query += ` AND (node_id > ? OR (node_id = ? AND metric > ?) OR (node_id = ? AND metric = ? AND series_key > ?))`
			args = append(args, lastNode, lastNode, lastMetric, lastNode, lastMetric, lastSeries)
		}
		query += ` ORDER BY node_id, metric, series_key LIMIT ?`
		args = append(args, nativeSeriesPageSize)

		rows, err := tx.QueryContext(ctx, query, args...)
		if err != nil {
			return err
		}
		page := make([]nativeAggregateSeriesKey, 0, nativeSeriesPageSize)
		for rows.Next() {
			var item nativeAggregateSeriesKey
			if err := rows.Scan(&item.nodeID, &item.metric, &item.seriesKey); err != nil {
				rows.Close()
				return err
			}
			page = append(page, item)
		}
		if err := rows.Close(); err != nil {
			return err
		}
		if err := rows.Err(); err != nil {
			return err
		}
		for _, item := range page {
			if err := visit(item); err != nil {
				return err
			}
		}
		if len(page) < nativeSeriesPageSize {
			return nil
		}
		last := page[len(page)-1]
		lastNode, lastMetric, lastSeries = last.nodeID, last.metric, last.seriesKey
	}
}

func nativeLastRollupBucket(ctx context.Context, tx *sql.Tx, series nativeAggregateSeriesKey, tier string) (time.Time, error) {
	var last string
	err := tx.QueryRowContext(ctx, `SELECT COALESCE(MAX(bucket_start), '') FROM node_metric_rollups
		WHERE node_id = ? AND metric = ? AND series_key = ? AND tier = ?`,
		series.nodeID, series.metric, series.seriesKey, tier).Scan(&last)
	if err != nil || last == "" {
		return time.Time{}, err
	}
	return parseNativeTime(last)
}

func nativeReadBuckets(ctx context.Context, tx *sql.Tx, spec nativeAggregateSpec, series nativeAggregateSeriesKey, start, end time.Time) ([]nativeBucket, error) {
	if spec.source == nativeSourceRaw {
		rows, err := tx.QueryContext(ctx, `SELECT timestamp, value FROM node_metric_samples
			WHERE node_id = ? AND metric = ? AND series_key = ?
				AND timestamp >= ? AND timestamp < ?
				AND quality = ? AND value IS NOT NULL
			ORDER BY timestamp LIMIT ?`,
			series.nodeID, series.metric, series.seriesKey,
			formatNativeTime(start), formatNativeTime(end),
			NativeQualityValid, nativeAggregateLimit+1)
		if err != nil {
			return nil, err
		}
		defer rows.Close()

		buckets := make([]nativeBucket, 0, 64)
		sourceRows := 0
		for rows.Next() {
			sourceRows++
			if sourceRows > nativeAggregateLimit {
				return nil, fmt.Errorf("native aggregation source exceeds %d rows", nativeAggregateLimit)
			}
			var timestamp string
			var value float64
			if err := rows.Scan(&timestamp, &value); err != nil {
				return nil, err
			}
			parsed, err := parseNativeTime(timestamp)
			if err != nil {
				return nil, err
			}
			if !nativeFinite(value) {
				continue
			}
			bucketStart := parsed.Truncate(spec.step)
			if len(buckets) == 0 || !buckets[len(buckets)-1].start.Equal(bucketStart) {
				buckets = append(buckets, nativeBucket{start: bucketStart})
			}
			buckets[len(buckets)-1].addRaw(value, parsed)
		}
		if err := rows.Err(); err != nil {
			return nil, err
		}
		if len(buckets) > nativeAggregateLimit {
			return nil, fmt.Errorf("%w: too many aggregate buckets", ErrNativeInvalidQuery)
		}
		return buckets, nil
	}

	rows, err := tx.QueryContext(ctx, `SELECT bucket_start, "count", "sum", "min", "max", "first", "last", first_ts, last_ts
		FROM node_metric_rollups
		WHERE node_id = ? AND metric = ? AND series_key = ? AND tier = ?
			AND bucket_start >= ? AND bucket_start < ? AND "count" > 0
		ORDER BY bucket_start LIMIT ?`,
		series.nodeID, series.metric, series.seriesKey, spec.source,
		formatNativeTime(start), formatNativeTime(end), nativeAggregateLimit+1)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	buckets := make([]nativeBucket, 0, 64)
	sourceRows := 0
	for rows.Next() {
		sourceRows++
		if sourceRows > nativeAggregateLimit {
			return nil, fmt.Errorf("native aggregation source exceeds %d rows", nativeAggregateLimit)
		}
		var bucketStart, firstTS, lastTS string
		var count int64
		var sum, minimum, maximum, first, last float64
		if err := rows.Scan(&bucketStart, &count, &sum, &minimum, &maximum, &first, &last, &firstTS, &lastTS); err != nil {
			return nil, err
		}
		if count <= 0 || !nativeFinite(sum) || !nativeFinite(minimum) || !nativeFinite(maximum) || !nativeFinite(first) || !nativeFinite(last) {
			continue
		}
		parsedStart, err := parseNativeTime(bucketStart)
		if err != nil {
			return nil, err
		}
		parsedFirst, err := parseNativeTime(firstTS)
		if err != nil {
			return nil, err
		}
		parsedLast, err := parseNativeTime(lastTS)
		if err != nil {
			return nil, err
		}
		aligned := parsedStart.Truncate(spec.step)
		if len(buckets) == 0 || !buckets[len(buckets)-1].start.Equal(aligned) {
			buckets = append(buckets, nativeBucket{start: aligned})
		}
		buckets[len(buckets)-1].addRollup(uint64(count), sum, minimum, maximum, first, last, parsedFirst, parsedLast)
	}
	if err := rows.Err(); err != nil {
		return nil, err
	}
	if len(buckets) > nativeAggregateLimit {
		return nil, fmt.Errorf("%w: too many aggregate buckets", ErrNativeInvalidQuery)
	}
	return buckets, nil
}

func nativeWriteBuckets(ctx context.Context, tx *sql.Tx, series nativeAggregateSeriesKey, spec nativeAggregateSpec, expected uint64, buckets []nativeBucket) error {
	if len(buckets) == 0 {
		return nil
	}
	statement, err := tx.PrepareContext(ctx, `
		INSERT INTO node_metric_rollups (
			node_id, metric, series_key, tier, bucket_start,
			"count", "expected", "missing", "sum", "min", "max", "first", "last",
			first_ts, last_ts, quality)
		VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
		ON CONFLICT(node_id, metric, series_key, tier, bucket_start) DO UPDATE SET
			"count" = excluded."count",
			"expected" = excluded."expected",
			"missing" = excluded."missing",
			"sum" = excluded."sum",
			"min" = excluded."min",
			"max" = excluded."max",
			"first" = excluded."first",
			"last" = excluded."last",
			first_ts = excluded.first_ts,
			last_ts = excluded.last_ts,
			quality = excluded.quality`)
	if err != nil {
		return err
	}
	defer statement.Close()

	if expected == 0 {
		expected = 1
	}
	for _, bucket := range buckets {
		if bucket.count == 0 {
			continue
		}
		if !nativeFinite(bucket.sum) || !nativeFinite(bucket.min) || !nativeFinite(bucket.max) || !nativeFinite(bucket.first) || !nativeFinite(bucket.last) {
			continue
		}
		missing := uint64(0)
		if bucket.count < expected {
			missing = expected - bucket.count
		}
		if _, err := statement.ExecContext(ctx,
			series.nodeID, series.metric, series.seriesKey, spec.tier, formatNativeTime(bucket.start),
			int64(bucket.count), int64(expected), int64(missing),
			bucket.sum, bucket.min, bucket.max, bucket.first, bucket.last,
			formatNativeTime(bucket.firstTS), formatNativeTime(bucket.lastTS), 0); err != nil {
			return err
		}
	}
	return nil
}

// PruneNodeMetrics 按保留策略清理原始样本与聚合桶。
func (s *SQLiteStore) PruneNodeMetrics(ctx context.Context, now time.Time) error {
	now = now.UTC()
	if now.IsZero() {
		now = time.Now().UTC()
	}
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if _, err := tx.ExecContext(ctx, `DELETE FROM node_metric_samples WHERE timestamp < ?`,
		formatNativeTime(now.Add(-NativeRawRetention))); err != nil {
		return err
	}
	retentions := map[string]time.Duration{
		NativeTier1m: Native1mRetention,
		NativeTier5m: Native5mRetention,
		NativeTier1h: Native1hRetention,
	}
	for tier, retention := range retentions {
		if _, err := tx.ExecContext(ctx, `DELETE FROM node_metric_rollups WHERE tier = ? AND bucket_start < ?`,
			tier, formatNativeTime(now.Add(-retention))); err != nil {
			return err
		}
	}
	return tx.Commit()
}
