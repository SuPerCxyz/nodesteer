package store

import (
	"context"
	"database/sql"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
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
	// nativeValidationGrace 吸收调用链上的毫秒级延迟，避免恰好落在边界上的
	// 合法样本被误判；超出边界的样本仍会被拒绝。
	nativeValidationGrace = 30 * time.Second

	// NativeRawRetention 等是规格默认值；运行期使用 NativeRetention 覆盖。
	NativeRawRetention = 7 * 24 * time.Hour
	Native1mRetention  = 90 * 24 * time.Hour
	Native5mRetention  = 365 * 24 * time.Hour
	Native1hRetention  = 2 * 365 * 24 * time.Hour

	// NativeLateWindow 是补报/乱序样本的默认重算窗口。Agent 内存补报上限
	// 约 30 分钟；15 分钟窗口与 15 分钟分块配合，单 series/tier 约 1 个事务。
	NativeLateWindow = 15 * time.Minute
	// nativeAggregateChunk 是增量重算的分块大小，避免一次事务长时间持写锁。
	nativeAggregateChunk = 15 * time.Minute
	// nativeInitialChunk 用于新 series 的首次回填；避免 7d/90d/365d 单事务。
	nativeInitialChunk = 24 * time.Hour
	// nativeAggregateMaxCatchup 限制正常聚合每次向前追赶的最大范围；
	// 更早的历史缺口由 bounded backfill 命令处理。
	nativeAggregateMaxCatchup = 2 * time.Hour

	nativeTimeLayout     = "2006-01-02T15:04:05.000000000Z"
	nativeAggregateLimit = 200000
)

// NativeRetention 是原生指标各层保留期。零字段使用规格默认值。
type NativeRetention struct {
	Raw        time.Duration
	OneMinute  time.Duration
	FiveMinute time.Duration
	OneHour    time.Duration
}

// DefaultNativeRetention 返回规格默认保留期。
func DefaultNativeRetention() NativeRetention {
	return NativeRetention{
		Raw: NativeRawRetention, OneMinute: Native1mRetention,
		FiveMinute: Native5mRetention, OneHour: Native1hRetention,
	}
}

func (r NativeRetention) normalized() NativeRetention {
	def := DefaultNativeRetention()
	if r.Raw <= 0 {
		r.Raw = def.Raw
	}
	if r.OneMinute <= 0 {
		r.OneMinute = def.OneMinute
	}
	if r.FiveMinute <= 0 {
		r.FiveMinute = def.FiveMinute
	}
	if r.OneHour <= 0 {
		r.OneHour = def.OneHour
	}
	return r
}

// Validate 校验 raw <= 1m <= 5m <= 1h 且 raw >= 1h。
func (r NativeRetention) Validate() error {
	r = r.normalized()
	if r.Raw < time.Hour {
		return fmt.Errorf("native raw retention must be >= 1h, got %s", r.Raw)
	}
	if r.Raw > r.OneMinute || r.OneMinute > r.FiveMinute || r.FiveMinute > r.OneHour {
		return fmt.Errorf("native retention must satisfy raw <= 1m <= 5m <= 1h, got %s/%s/%s/%s",
			r.Raw, r.OneMinute, r.FiveMinute, r.OneHour)
	}
	return nil
}

var (
	ErrNativeInvalidSample = errors.New("invalid native metric sample")
	ErrNativeBatchTooLarge = errors.New("native metric batch too large")
	ErrNativeSampleTime    = errors.New("native metric sample timestamp out of range")
	ErrNativeInvalidQuery  = errors.New("invalid native metric query")
	ErrNativeSeriesLimit   = errors.New("native metric series limit exceeded")
)

// slowNativeQueryThreshold 是原生指标慢查询日志阈值；只记录操作与节点，
// 不记录样本值、维度或查询条件。
const slowNativeQueryThreshold = 200 * time.Millisecond

func logSlowNativeQuery(operation string, start time.Time, attrs ...any) {
	elapsed := time.Since(start)
	if elapsed < slowNativeQueryThreshold {
		return
	}
	args := append([]any{"operation", operation, "duration", elapsed}, attrs...)
	slog.Default().Warn("slow native metric query", args...)
}

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

// NativeMetricTuner 是 Hub 配置可调的原生指标运行参数；实现可选。
type NativeMetricTuner interface {
	SetNativeLateWindow(window time.Duration)
	SetNativeAggregateBudget(budget time.Duration)
	SetNativeRetention(retention NativeRetention) error
	SetNativeVacuumPages(pages int)
}

// NativeAggregateStats 是一轮聚合/回填的运行统计。
type NativeAggregateStats struct {
	Duration     time.Duration
	Series       int
	Transactions int
	BusyErrors   int
	Truncated    int
}

type nativeRunStats struct {
	series       int
	transactions int
	busyErrors   int
	truncated    int
}

// NativeAggregateReporter 由维护任务读取最近一轮统计；实现可选。
type NativeAggregateReporter interface {
	LastNativeAggregateStats() NativeAggregateStats
	LastNativeBackfillStats() NativeAggregateStats
}

// NativeMetricStore 是原生指标存储的完整窄接口，供 API 层断言使用。
type NativeMetricStore interface {
	NativeMetricReader
	InsertNodeMetricSamples(ctx context.Context, nodeID string, samples []NativeSample) error
	NodeMetricSeries(ctx context.Context, nodeID, metric string) ([]NativeSeries, error)
	LatestNodeMetricSamples(ctx context.Context, nodeID string) ([]NativeSample, error)
	LatestNodeMetricSamplesBatch(ctx context.Context, nodeIDs []string) (map[string][]NativeSample, error)
	// LatestNodeMetricSamplesForMetricsBatch 只取指定 metric 的最近窗口最新样本，
	// 供 overview 避免扫描全部 series/metric。
	LatestNodeMetricSamplesForMetricsBatch(ctx context.Context, nodeIDs, metrics []string, since time.Time) (map[string][]NativeSample, error)
	NodeMetricSeriesBatch(ctx context.Context, nodeID string, metrics []string) (map[string][]NativeSeries, error)
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
	lateCutoff := now.Add(-s.lateWindow())
	lateSamples := 0
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
		maxAge := s.retention().Raw + time.Hour
		if timestamp.After(now.Add(NativeMaxFutureSkew+nativeValidationGrace)) || timestamp.Before(now.Add(-maxAge-nativeValidationGrace)) {
			return fmt.Errorf("%w: %s", ErrNativeSampleTime, timestamp.Format(time.RFC3339Nano))
		}
		if timestamp.Before(lateCutoff) {
			lateSamples++
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

	// 每节点 series 基数上限：目录表只保存 series 元数据，避免写事务内
	// UNION 扫描该节点全部 raw/rollup（生产 4.3GB 库上约 0.41s/批）。
	var existingCount int
	if err := tx.QueryRowContext(ctx, `SELECT COUNT(*) FROM node_metric_series WHERE node_id = ?`, nodeID).Scan(&existingCount); err != nil {
		return err
	}
	type seriesMeta struct {
		metric     string
		dimensions string
	}
	distinct := make(map[string]seriesMeta)
	for _, sample := range prepared {
		if _, ok := distinct[sample.seriesKey]; ok {
			continue
		}
		distinct[sample.seriesKey] = seriesMeta{metric: sample.metric, dimensions: sample.dimensions}
	}
	keys := make([]string, 0, len(distinct))
	for key := range distinct {
		keys = append(keys, key)
	}
	known := make(map[string]bool, len(keys))
	if len(keys) > 0 {
		placeholders := strings.TrimSuffix(strings.Repeat("?,", len(keys)), ",")
		args := make([]any, 0, len(keys)+1)
		args = append(args, nodeID)
		for _, key := range keys {
			args = append(args, key)
		}
		rows, err := tx.QueryContext(ctx, fmt.Sprintf(
			`SELECT series_key FROM node_metric_series WHERE node_id = ? AND series_key IN (%s)`, placeholders), args...)
		if err != nil {
			return err
		}
		for rows.Next() {
			var key string
			if err := rows.Scan(&key); err != nil {
				rows.Close()
				return err
			}
			known[key] = true
		}
		if err := rows.Close(); err != nil {
			return err
		}
		if err := rows.Err(); err != nil {
			return err
		}
	}
	newKeys := make([]string, 0, len(keys))
	for _, key := range keys {
		if known[key] {
			continue
		}
		if existingCount+len(newKeys) >= NativeMaxSeries {
			return fmt.Errorf("%w: node %s has reached %d series", ErrNativeSeriesLimit, nodeID, NativeMaxSeries)
		}
		newKeys = append(newKeys, key)
	}
	if len(newKeys) > 0 {
		statement, err := tx.PrepareContext(ctx, `
			INSERT INTO node_metric_series (node_id, metric, series_key, dimensions_json, updated_at)
			VALUES (?, ?, ?, ?, ?)
			ON CONFLICT(node_id, metric, series_key) DO UPDATE SET
				dimensions_json = excluded.dimensions_json,
				updated_at = excluded.updated_at`)
		if err != nil {
			return err
		}
		defer statement.Close()
		updatedAt := formatNativeTime(time.Now().UTC())
		for _, key := range newKeys {
			meta := distinct[key]
			if _, err := statement.ExecContext(ctx, nodeID, meta.metric, key, meta.dimensions, updatedAt); err != nil {
				return err
			}
		}
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
	if err := tx.Commit(); err != nil {
		return err
	}
	s.metricGeneration.Add(1)
	if lateSamples > 0 {
		s.nativeLateSamples.Add(uint64(lateSamples))
		slog.Default().Warn("native metric samples older than late window; raw retained, rollup recompute truncated",
			"node", nodeID, "samples", lateSamples, "window", s.lateWindow())
	}
	return nil
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

	query := `SELECT metric, series_key, dimensions_json FROM node_metric_series WHERE node_id = ?`
	args := []any{nodeID}
	if metric != "" {
		query += ` AND metric = ?`
		args = append(args, metric)
	}
	query += ` ORDER BY metric, series_key LIMIT ?`
	args = append(args, NativeMaxSeries)
	rows, err := s.queryer(ctx).QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	series := make([]NativeSeries, 0, 16)
	for rows.Next() {
		var item NativeSeries
		var encoded string
		if err := rows.Scan(&item.Metric, &item.SeriesKey, &encoded); err != nil {
			return nil, err
		}
		item.Dimensions = nativeDimensionsForSeries(item.SeriesKey, encoded)
		series = append(series, item)
	}
	if err := rows.Err(); err != nil {
		return nil, err
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

// LatestNodeMetricSamplesForMetricsBatch 只返回指定 metric 在 since 之后的最新样本。
// overview 用它裁剪扫描范围，避免把 per-core / 容器 / 磁盘等全部 series 拉进内存。
func (s *SQLiteStore) LatestNodeMetricSamplesForMetricsBatch(ctx context.Context, nodeIDs, metrics []string, since time.Time) (map[string][]NativeSample, error) {
	start := time.Now()
	defer func() {
		logSlowNativeQuery("latest_metrics_batch", start, "nodes", len(nodeIDs), "metrics", len(metrics))
	}()
	result := make(map[string][]NativeSample, len(nodeIDs))
	uniqueNodes, err := uniqueNativeNodeIDs(nodeIDs, result)
	if err != nil {
		return nil, err
	}
	uniqueMetrics := make([]string, 0, len(metrics))
	seenMetrics := make(map[string]bool, len(metrics))
	for _, metric := range metrics {
		if !ValidNativeMetric(metric) {
			return nil, fmt.Errorf("%w: invalid metric", ErrNativeInvalidQuery)
		}
		if seenMetrics[metric] {
			continue
		}
		seenMetrics[metric] = true
		uniqueMetrics = append(uniqueMetrics, metric)
	}
	if len(uniqueNodes) == 0 || len(uniqueMetrics) == 0 {
		return result, nil
	}

	nodePlaceholders := strings.TrimSuffix(strings.Repeat("?,", len(uniqueNodes)), ",")
	metricPlaceholders := strings.TrimSuffix(strings.Repeat("?,", len(uniqueMetrics)), ",")
	args := make([]any, 0, len(uniqueNodes)+len(uniqueMetrics)+1)
	for _, nodeID := range uniqueNodes {
		args = append(args, nodeID)
	}
	for _, metric := range uniqueMetrics {
		args = append(args, metric)
	}
	args = append(args, formatNativeTime(since.UTC()))

	rows, err := s.queryer(ctx).QueryContext(ctx, fmt.Sprintf(`
		SELECT s.node_id, s.metric, s.series_key, s.timestamp, s.value, s.quality, s.dimensions_json
		FROM node_metric_samples s
		JOIN (
			SELECT node_id, metric, series_key, MAX(timestamp) AS max_timestamp
			FROM node_metric_samples
			WHERE node_id IN (%s) AND metric IN (%s) AND timestamp >= ?
			GROUP BY node_id, metric, series_key
		) latest ON latest.node_id = s.node_id
			AND latest.metric = s.metric
			AND latest.series_key = s.series_key
			AND latest.max_timestamp = s.timestamp
		ORDER BY s.node_id, s.metric, s.series_key`, nodePlaceholders, metricPlaceholders), args...)
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

func uniqueNativeNodeIDs(nodeIDs []string, result map[string][]NativeSample) ([]string, error) {
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
	return unique, nil
}

// 同一批次内多个 metric 共享时间戳，使用 DISTINCT 避免把并发序列误当成高频采样。
// NodeMetricSampleInterval 从最近 15 分钟的 distinct 原始时间戳推导节点采样周期。
// 同一批次内多个 metric 共享时间戳，使用 DISTINCT 避免把并发序列误当成高频采样。
func (s *SQLiteStore) NodeMetricSampleInterval(ctx context.Context, nodeID string) (time.Duration, error) {
	intervals, err := s.NodeMetricSampleIntervals(ctx, []string{nodeID})
	if err != nil {
		return 0, err
	}
	return intervals[nodeID], nil
}

// NodeMetricSampleIntervals 用单条 SQL 批量推导多个节点的采样周期。
// 每个节点最多取最近 500 个 distinct 时间戳；窗口 15 分钟足以覆盖 5s–300s 采样。
func (s *SQLiteStore) NodeMetricSampleIntervals(ctx context.Context, nodeIDs []string) (map[string]time.Duration, error) {
	start := time.Now()
	defer func() { logSlowNativeQuery("sample_intervals", start, "nodes", len(nodeIDs)) }()
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
	args = append(args, formatNativeTime(time.Now().UTC().Add(-15*time.Minute)), 500)
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

// NodeMetricSeriesBatch 一次返回多个 metric 的 series 目录，避免逐 metric
// 重复执行 UNION/分组查询。结果按 metric 分组，每组按 series_key 排序。
func (s *SQLiteStore) NodeMetricSeriesBatch(ctx context.Context, nodeID string, metrics []string) (map[string][]NativeSeries, error) {
	start := time.Now()
	defer func() { logSlowNativeQuery("series_batch", start, "metrics", len(metrics)) }()
	if !validNativeNodeID(nodeID) {
		return nil, fmt.Errorf("%w: invalid node id", ErrNativeInvalidQuery)
	}
	unique := make([]string, 0, len(metrics))
	seen := make(map[string]bool, len(metrics))
	for _, metric := range metrics {
		if !ValidNativeMetric(metric) {
			return nil, fmt.Errorf("%w: invalid metric", ErrNativeInvalidQuery)
		}
		if seen[metric] {
			continue
		}
		seen[metric] = true
		unique = append(unique, metric)
	}
	result := make(map[string][]NativeSeries, len(unique))
	for _, metric := range unique {
		result[metric] = []NativeSeries{}
	}
	if len(unique) == 0 {
		return result, nil
	}

	placeholders := strings.TrimSuffix(strings.Repeat("?,", len(unique)), ",")
	args := make([]any, 0, len(unique)+2)
	args = append(args, nodeID)
	for _, metric := range unique {
		args = append(args, metric)
	}
	args = append(args, NativeMaxSeries)

	query := fmt.Sprintf(`SELECT metric, series_key, dimensions_json
		FROM node_metric_series
		WHERE node_id = ? AND metric IN (%s)
		ORDER BY metric, series_key LIMIT ?`, placeholders)
	rows, err := s.queryer(ctx).QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var metric, seriesKey, encoded string
		if err := rows.Scan(&metric, &seriesKey, &encoded); err != nil {
			return nil, err
		}
		if len(result[metric]) >= NativeMaxSeries {
			continue
		}
		result[metric] = append(result[metric], NativeSeries{
			Metric:     metric,
			SeriesKey:  seriesKey,
			Dimensions: nativeDimensionsForSeries(seriesKey, encoded),
		})
	}
	if err := rows.Err(); err != nil {
		return nil, err
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
	started := time.Now()
	defer func() { logSlowNativeQuery("query_samples", started) }()
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
	started := time.Now()
	defer func() { logSlowNativeQuery("query_rollups", started, "tier", tier) }()
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

// SetNativeRetention 注入各层保留期；非法配置返回错误且不改变现有值。
func (s *SQLiteStore) SetNativeRetention(ret NativeRetention) error {
	if err := ret.Validate(); err != nil {
		return err
	}
	s.nativeRetention = ret.normalized()
	return nil
}

func (s *SQLiteStore) retention() NativeRetention {
	if s.nativeRetention.Raw <= 0 {
		return DefaultNativeRetention()
	}
	return s.nativeRetention.normalized()
}

const nativeAggregateWatermarkKey = "native_aggregate_watermark"

func (s *SQLiteStore) markAggregateSuccess(ctx context.Context, now time.Time) {
	if now.IsZero() {
		now = time.Now().UTC()
	}
	now = now.UTC()
	s.aggregateWatermark.Store(now.UnixNano())
	if err := s.SetSetting(ctx, nativeAggregateWatermarkKey, formatNativeTime(now)); err != nil {
		slog.Default().Warn("persist native aggregate watermark failed", "error", err)
	}
}

func (s *SQLiteStore) loadAggregateWatermark(ctx context.Context) {
	raw, err := s.GetSetting(ctx, nativeAggregateWatermarkKey)
	if err != nil || raw == "" {
		return
	}
	parsed, err := parseNativeTime(raw)
	if err != nil {
		return
	}
	s.aggregateWatermark.Store(parsed.UTC().UnixNano())
}

func (s *SQLiteStore) aggregateWatermarkTime() time.Time {
	value := s.aggregateWatermark.Load()
	if value <= 0 {
		return time.Time{}
	}
	return time.Unix(0, value).UTC()
}

// SetNativeLateWindow 覆盖迟到重算窗口；仅影响聚合范围，不改变 raw 保留期。
func (s *SQLiteStore) SetNativeLateWindow(window time.Duration) {
	if window > 0 {
		s.nativeLateWindow = window
	}
}

func (s *SQLiteStore) lateWindow() time.Duration {
	if s.nativeLateWindow > 0 {
		return s.nativeLateWindow
	}
	return NativeLateWindow
}

// LateSampleCount 返回入库时早于迟到窗口的样本计数（观测用，不代表丢弃）。
func (s *SQLiteStore) LateSampleCount() uint64 {
	return s.nativeLateSamples.Load()
}

// SetNativeAggregateBudget 设置每轮聚合时间预算；<=0 使用默认 10s。
func (s *SQLiteStore) SetNativeAggregateBudget(budget time.Duration) {
	if budget > 0 {
		s.nativeAggregateBudget = budget
	}
}

func (s *SQLiteStore) aggregateBudget() time.Duration {
	if s.nativeAggregateBudget > 0 {
		return s.nativeAggregateBudget
	}
	return 10 * time.Second
}

func (s *SQLiteStore) aggregateCursor(tier string) (nativeAggregateSeriesKey, bool) {
	s.aggregateMu.Lock()
	defer s.aggregateMu.Unlock()
	cursor, ok := s.aggregateCursors[tier]
	return cursor, ok
}

func (s *SQLiteStore) setAggregateCursor(tier string, cursor nativeAggregateSeriesKey) {
	s.aggregateMu.Lock()
	s.aggregateCursors[tier] = cursor
	s.aggregateMu.Unlock()
}

func (s *SQLiteStore) clearAggregateCursor(tier string) {
	s.aggregateMu.Lock()
	delete(s.aggregateCursors, tier)
	s.aggregateMu.Unlock()
}

// LastNativeAggregateStats 返回最近一轮聚合统计。
func (s *SQLiteStore) LastNativeAggregateStats() NativeAggregateStats {
	s.aggregateStatsMu.Lock()
	defer s.aggregateStatsMu.Unlock()
	return s.lastAggregateStats
}

// LastNativeBackfillStats 返回最近一次回填统计。
func (s *SQLiteStore) LastNativeBackfillStats() NativeAggregateStats {
	s.aggregateStatsMu.Lock()
	defer s.aggregateStatsMu.Unlock()
	return s.lastBackfillStats
}

// AggregateNodeMetrics 执行 raw -> 1m -> 5m -> 1h 聚合。
//
// 增量重算范围只覆盖最近 15 分钟迟到窗口，并确保包含最后一个 rollup 桶；
// 正常追赶最多向前 2 小时，更早缺口由 BackfillNativeMetrics 处理。
// 每轮最多运行 10s（可配置），按 tier 游标轮转 series，避免总从第一个节点开始。
func (s *SQLiteStore) AggregateNodeMetrics(ctx context.Context, now time.Time) error {
	started := time.Now()
	run := &nativeRunStats{}
	defer func() {
		logSlowNativeQuery("aggregate", started)
		s.aggregateStatsMu.Lock()
		s.lastAggregateStats = NativeAggregateStats{
			Duration: time.Since(started), Series: run.series,
			Transactions: run.transactions, BusyErrors: run.busyErrors, Truncated: run.truncated,
		}
		s.aggregateStatsMu.Unlock()
	}()
	now = now.UTC()
	if now.IsZero() {
		now = time.Now().UTC()
	}
	deadline := time.Now().Add(s.aggregateBudget())
	retention := s.retention()
	specs := []nativeAggregateSpec{
		{
			tier:            NativeTier1m,
			step:            time.Minute,
			source:          nativeSourceRaw,
			sourceRetention: retention.Raw,
			initialLookback: retention.Raw,
		},
		{
			tier:            NativeTier5m,
			step:            5 * time.Minute,
			source:          NativeTier1m,
			sourceStep:      time.Minute,
			sourceRetention: retention.OneMinute,
			initialLookback: retention.OneMinute,
		},
		{
			tier:            NativeTier1h,
			step:            time.Hour,
			source:          NativeTier5m,
			sourceStep:      5 * time.Minute,
			sourceRetention: retention.FiveMinute,
			initialLookback: retention.FiveMinute,
		},
	}
	for _, spec := range specs {
		if !time.Now().Before(deadline) {
			return nil
		}
		if err := s.aggregateNativeTier(ctx, spec, now, deadline, run); err != nil {
			return err
		}
	}
	s.markAggregateSuccess(ctx, now)
	return nil
}

func (s *SQLiteStore) aggregateNativeTier(ctx context.Context, spec nativeAggregateSpec, now, deadline time.Time, run *nativeRunStats) error {
	end := now.Truncate(spec.step)
	if end.IsZero() {
		return nil
	}
	sourceStart := now.Add(-spec.sourceRetention)
	after, hasAfter := s.aggregateCursor(spec.tier)
	for {
		page, err := nativeListAggregateSeries(ctx, s.db, after, hasAfter, nativeSeriesPageSize)
		if err != nil {
			return err
		}
		if len(page) == 0 {
			s.clearAggregateCursor(spec.tier)
			return nil
		}
		for index, item := range page {
			if !time.Now().Before(deadline) {
				if index > 0 {
					s.setAggregateCursor(spec.tier, page[index-1])
				} else {
					s.setAggregateCursor(spec.tier, after)
				}
				return nil
			}
			if err := s.aggregateNativeSeries(ctx, spec, item, now, sourceStart, end, run); err != nil {
				return err
			}
			run.series++
			s.setAggregateCursor(spec.tier, item)
		}
		if len(page) < nativeSeriesPageSize {
			s.clearAggregateCursor(spec.tier)
			return nil
		}
		after = page[len(page)-1]
		hasAfter = true
	}
}

// nativeListAggregateSeries 从 series 目录分页读取，避免每次聚合扫描 raw。
func nativeListAggregateSeries(ctx context.Context, q txQueryer, after nativeAggregateSeriesKey, hasAfter bool, limit int) ([]nativeAggregateSeriesKey, error) {
	query := `SELECT node_id, metric, series_key FROM node_metric_series`
	args := []any{}
	if hasAfter {
		query += ` WHERE node_id > ? OR (node_id = ? AND metric > ?) OR (node_id = ? AND metric = ? AND series_key > ?)`
		args = append(args, after.nodeID, after.nodeID, after.metric, after.nodeID, after.metric, after.seriesKey)
	}
	query += ` ORDER BY node_id, metric, series_key LIMIT ?`
	args = append(args, limit)
	rows, err := q.QueryContext(ctx, query, args...)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]nativeAggregateSeriesKey, 0, limit)
	for rows.Next() {
		var item nativeAggregateSeriesKey
		if err := rows.Scan(&item.nodeID, &item.metric, &item.seriesKey); err != nil {
			return nil, err
		}
		out = append(out, item)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) aggregateNativeSeries(
	ctx context.Context,
	spec nativeAggregateSpec,
	item nativeAggregateSeriesKey,
	now time.Time,
	sourceStart, end time.Time,
	run *nativeRunStats,
) error {
	last, err := nativeLastRollupBucket(ctx, s.db, item, spec.tier)
	if err != nil {
		return err
	}
	initial := last.IsZero()
	start := now.Add(-s.lateWindow())
	if initial {
		start = now.Add(-spec.initialLookback)
		earliest, err := nativeSeriesEarliestSource(ctx, s.db, spec, item)
		if err != nil {
			return err
		}
		if !earliest.IsZero() && earliest.After(start) {
			start = earliest
		}
	}
	if !initial && last.Before(start) {
		// 最后一个桶早于窗口时仍重算它，保证 1h 桶能在小时后收敛。
		start = last
	}
	if !initial {
		if catchup := now.Add(-nativeAggregateMaxCatchup); start.Before(catchup) {
			start = catchup
			run.truncated++
			slog.Default().Debug("native metric aggregation truncated catchup window",
				"node", item.nodeID, "metric", item.metric, "series", item.seriesKey,
				"tier", spec.tier, "start", start)
		}
	}
	if start.Before(sourceStart) {
		start = sourceStart
	}
	start = start.Truncate(spec.step)
	if !start.Before(end) {
		return nil
	}
	if !initial {
		hasSource, err := nativeSeriesHasSource(ctx, s.db, spec, item, start, end)
		if err != nil {
			return err
		}
		if !hasSource {
			return nil
		}
	}
	expected := NativeExpectedCount(spec.step, spec.sourceStep)
	if spec.source == nativeSourceRaw {
		interval, err := nativeRawSeriesInterval(ctx, s.db, item, start, end)
		if err != nil {
			return err
		}
		expected = NativeExpectedCount(spec.step, interval)
	}
	chunk := nativeAggregateChunk
	if initial {
		chunk = nativeInitialChunk
	}
	if chunk < spec.step {
		chunk = spec.step
	}
	for chunkStart := start; chunkStart.Before(end); {
		chunkEnd := chunkStart.Add(chunk)
		if chunkEnd.After(end) {
			chunkEnd = end
		}
		if err := s.aggregateNativeSeriesRange(ctx, spec, item, chunkStart, chunkEnd, expected, run); err != nil {
			return err
		}
		chunkStart = chunkEnd
	}
	return nil
}

func nativeSeriesHasSource(ctx context.Context, q txQueryer, spec nativeAggregateSpec, item nativeAggregateSeriesKey, start, end time.Time) (bool, error) {
	var query string
	args := []any{item.nodeID, item.metric, item.seriesKey, formatNativeTime(start), formatNativeTime(end)}
	if spec.source == nativeSourceRaw {
		query = `SELECT 1 FROM node_metric_samples
			WHERE node_id = ? AND metric = ? AND series_key = ? AND timestamp >= ? AND timestamp < ? LIMIT 1`
	} else {
		query = `SELECT 1 FROM node_metric_rollups
			WHERE node_id = ? AND metric = ? AND series_key = ? AND tier = ? AND bucket_start >= ? AND bucket_start < ? LIMIT 1`
		args = []any{item.nodeID, item.metric, item.seriesKey, spec.source, formatNativeTime(start), formatNativeTime(end)}
	}
	var one int
	err := q.QueryRowContext(ctx, query, args...).Scan(&one)
	if errors.Is(err, sql.ErrNoRows) {
		return false, nil
	}
	return err == nil, err
}

func nativeSeriesEarliestSource(ctx context.Context, q txQueryer, spec nativeAggregateSpec, item nativeAggregateSeriesKey) (time.Time, error) {
	var query string
	var args []any
	if spec.source == nativeSourceRaw {
		query = `SELECT MIN(timestamp) FROM node_metric_samples WHERE node_id = ? AND metric = ? AND series_key = ?`
		args = []any{item.nodeID, item.metric, item.seriesKey}
	} else {
		query = `SELECT MIN(bucket_start) FROM node_metric_rollups WHERE node_id = ? AND metric = ? AND series_key = ? AND tier = ?`
		args = []any{item.nodeID, item.metric, item.seriesKey, spec.source}
	}
	var raw sql.NullString
	if err := q.QueryRowContext(ctx, query, args...).Scan(&raw); err != nil {
		return time.Time{}, err
	}
	if !raw.Valid || raw.String == "" {
		return time.Time{}, nil
	}
	return parseNativeTime(raw.String)
}

// NativeBackfillOptions 控制一次性 bounded backfill。
type NativeBackfillOptions struct {
	// Chunk 是每个事务覆盖的时间范围；零值使用 15m（1m/5m tier）或 1h（1h tier）。
	Chunk time.Duration
	// Pause 是每个 chunk 之间的限速等待；零值使用 10ms。
	Pause time.Duration
	// Progress 每处理若干 series 回调一次（可选）。
	Progress func(tier string, seriesDone, seriesTotal int)
}

// BackfillNativeMetrics 从 from 到 to 分块重算 raw -> 1m -> 5m -> 1h。
// 只删除/重写目标 rollup，不删除 raw；重复执行幂等。用于恢复长期缺口，
// 例如生产 14:09–14:39 的 1m/5m、13:00–14:00 的 1h 缺失。
func (s *SQLiteStore) BackfillNativeMetrics(ctx context.Context, from, to time.Time, opts NativeBackfillOptions) error {
	from, to = from.UTC(), to.UTC()
	if from.IsZero() || to.IsZero() || !from.Before(to) {
		return fmt.Errorf("%w: invalid backfill range", ErrNativeInvalidQuery)
	}
	retention := s.retention()
	specs := []nativeAggregateSpec{
		{tier: NativeTier1m, step: time.Minute, source: nativeSourceRaw, sourceRetention: retention.Raw},
		{tier: NativeTier5m, step: 5 * time.Minute, source: NativeTier1m, sourceStep: time.Minute, sourceRetention: retention.OneMinute},
		{tier: NativeTier1h, step: time.Hour, source: NativeTier5m, sourceStep: 5 * time.Minute, sourceRetention: retention.FiveMinute},
	}
	series, err := nativeListAllSeries(ctx, s.db)
	if err != nil {
		return err
	}
	started := time.Now()
	run := &nativeRunStats{}
	defer func() {
		s.aggregateStatsMu.Lock()
		s.lastBackfillStats = NativeAggregateStats{
			Duration: time.Since(started), Series: run.series,
			Transactions: run.transactions, BusyErrors: run.busyErrors, Truncated: run.truncated,
		}
		s.aggregateStatsMu.Unlock()
	}()
	for _, spec := range specs {
		if err := ctx.Err(); err != nil {
			return err
		}
		chunk := opts.Chunk
		if chunk <= 0 {
			chunk = nativeAggregateChunk
		}
		if chunk < spec.step {
			chunk = spec.step
		}
		end := to.Truncate(spec.step)
		start := from.UTC().Truncate(spec.step)
		if !start.Before(end) {
			continue
		}
		for index, item := range series {
			if err := ctx.Err(); err != nil {
				return err
			}
			expected := NativeExpectedCount(spec.step, spec.sourceStep)
			if spec.source == nativeSourceRaw {
				interval, err := nativeRawSeriesInterval(ctx, s.db, item, start, end)
				if err != nil {
					return err
				}
				expected = NativeExpectedCount(spec.step, interval)
			}
			for chunkStart := start; chunkStart.Before(end); {
				chunkEnd := chunkStart.Add(chunk)
				if chunkEnd.After(end) {
					chunkEnd = end
				}
				hasSource, err := nativeSeriesHasSource(ctx, s.db, spec, item, chunkStart, chunkEnd)
				if err != nil {
					return err
				}
				if hasSource {
					if err := s.aggregateNativeSeriesRange(ctx, spec, item, chunkStart, chunkEnd, expected, run); err != nil {
						return err
					}
				}
				chunkStart = chunkEnd
				if opts.Pause > 0 {
					select {
					case <-ctx.Done():
						return ctx.Err()
					case <-time.After(opts.Pause):
					}
				}
			}
			run.series++
			if opts.Progress != nil && (index+1)%50 == 0 {
				opts.Progress(spec.tier, index+1, len(series))
			}
		}
		if opts.Progress != nil {
			opts.Progress(spec.tier, len(series), len(series))
		}
	}
	return nil
}

func nativeListAllSeries(ctx context.Context, q txQueryer) ([]nativeAggregateSeriesKey, error) {
	rows, err := q.QueryContext(ctx, `SELECT node_id, metric, series_key FROM node_metric_series ORDER BY node_id, metric, series_key`)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	out := make([]nativeAggregateSeriesKey, 0, 256)
	for rows.Next() {
		var item nativeAggregateSeriesKey
		if err := rows.Scan(&item.nodeID, &item.metric, &item.seriesKey); err != nil {
			return nil, err
		}
		out = append(out, item)
	}
	return out, rows.Err()
}

func (s *SQLiteStore) aggregateNativeSeriesRange(
	ctx context.Context,
	spec nativeAggregateSpec,
	item nativeAggregateSeriesKey,
	start, end time.Time,
	expected uint64,
	run *nativeRunStats,
) (err error) {
	defer func() {
		if err != nil && IsBusyError(err) {
			run.busyErrors++
		}
	}()
	tx, err := s.db.BeginTx(ctx, nil)
	if err != nil {
		return err
	}
	defer tx.Rollback()
	// 重算窗口内先删除目标桶，避免 valid→missing 等更新后旧 rollup 残留。
	if err := nativeDeleteRollupWindow(ctx, tx, item, spec.tier, start, end); err != nil {
		return err
	}
	buckets, err := nativeReadBuckets(ctx, tx, spec, item, start, end)
	if err != nil {
		return err
	}
	if err := nativeWriteBuckets(ctx, tx, item, spec, expected, buckets); err != nil {
		return err
	}
	if err := tx.Commit(); err != nil {
		return err
	}
	run.transactions++
	return nil
}

// nativeRawSeriesInterval 从最近原始样本时间戳推导单个 series 的采样周期。
// 倒序限制 200 条即可覆盖 300s 采样下的 16 小时，足够稳定推导。
func nativeRawSeriesInterval(ctx context.Context, tx txQueryer, series nativeAggregateSeriesKey, start, end time.Time) (time.Duration, error) {
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

func nativeLastRollupBucket(ctx context.Context, tx txQueryer, series nativeAggregateSeriesKey, tier string) (time.Time, error) {
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
// NativeDefaultVacuumPages 是每轮 prune 默认回收的 freelist 页数（约 20MB）。
const NativeDefaultVacuumPages = 5000

// nativeVacuumBatchPages 是单次 PRAGMA incremental_vacuum 的最大页数，
// 避免一次持锁过久；每轮总页数由 NativeDefaultVacuumPages/配置控制。
const nativeVacuumBatchPages = 500

// NativeDrainOptions 控制一次性 freelist 回收。
type NativeDrainOptions struct {
	// MaxPages <=0 表示回收当前全部 freelist（仍受 Budget 限制）。
	MaxPages   int
	BatchPages int
	Budget     time.Duration
	DryRun     bool
}

// NativeVacuumStats 是一次 incremental_vacuum 的统计。
type NativeVacuumStats struct {
	AutoVacuum     int
	PageSize       int64
	FreelistBefore int64
	FreelistAfter  int64
	PagesReclaimed int64
	Batches        int
	BusyErrors     int
	Duration       time.Duration
	BudgetHit      bool
	DryRun         bool
}

func (s *SQLiteStore) SetNativeVacuumPages(pages int) {
	if pages > 0 {
		s.nativeVacuumPages = pages
	}
}

func (s *SQLiteStore) vacuumPages() int {
	if s.nativeVacuumPages > 0 {
		return s.nativeVacuumPages
	}
	return NativeDefaultVacuumPages
}

// DrainFreePages 在 Hub 运行或停机时回收 freelist；默认分批执行。
func (s *SQLiteStore) DrainFreePages(ctx context.Context, opts NativeDrainOptions) (NativeVacuumStats, error) {
	stats, err := s.drainFreePages(ctx, opts)
	attrs := []any{
		"duration", stats.Duration, "freelist_before", stats.FreelistBefore,
		"freelist_after", stats.FreelistAfter, "pages", stats.PagesReclaimed,
		"batches", stats.Batches, "busy_errors", stats.BusyErrors,
		"budget_hit", stats.BudgetHit, "dry_run", stats.DryRun,
	}
	if err != nil {
		slog.Default().Warn("incremental vacuum completed with errors", append(attrs, "error", err)...)
	} else {
		slog.Default().Info("incremental vacuum completed", attrs...)
	}
	return stats, err
}

func (s *SQLiteStore) drainFreePages(ctx context.Context, opts NativeDrainOptions) (NativeVacuumStats, error) {
	started := time.Now()
	stats := NativeVacuumStats{DryRun: opts.DryRun}
	defer func() { stats.Duration = time.Since(started) }()
	if err := s.db.QueryRowContext(ctx, `PRAGMA auto_vacuum`).Scan(&stats.AutoVacuum); err != nil {
		return stats, err
	}
	if stats.AutoVacuum != 2 {
		return stats, nil
	}
	if err := s.db.QueryRowContext(ctx, `PRAGMA page_size`).Scan(&stats.PageSize); err != nil {
		return stats, err
	}
	if err := s.db.QueryRowContext(ctx, `PRAGMA freelist_count`).Scan(&stats.FreelistBefore); err != nil {
		return stats, err
	}
	stats.FreelistAfter = stats.FreelistBefore
	if opts.DryRun || stats.FreelistBefore == 0 {
		return stats, nil
	}
	maxPages := opts.MaxPages
	if maxPages <= 0 {
		maxPages = int(stats.FreelistBefore)
	}
	if maxPages > int(stats.FreelistBefore) {
		maxPages = int(stats.FreelistBefore)
	}
	batch := opts.BatchPages
	if batch <= 0 {
		batch = nativeVacuumBatchPages
	}
	var deadline time.Time
	if opts.Budget > 0 {
		deadline = time.Now().Add(opts.Budget)
	}
	var firstErr error
	for stats.PagesReclaimed < int64(maxPages) && stats.FreelistBefore-stats.PagesReclaimed > 0 {
		if !deadline.IsZero() && !time.Now().Before(deadline) {
			stats.BudgetHit = true
			break
		}
		pages := int64(batch)
		remaining := int64(maxPages) - stats.PagesReclaimed
		if pages > remaining {
			pages = remaining
		}
		free := stats.FreelistBefore - stats.PagesReclaimed
		if pages > free {
			pages = free
		}
		if pages <= 0 {
			break
		}
		if _, err := s.db.ExecContext(ctx, fmt.Sprintf(`PRAGMA incremental_vacuum(%d)`, pages)); err != nil {
			if IsBusyError(err) {
				stats.BusyErrors++
			}
			if firstErr == nil {
				firstErr = err
			}
			break
		}
		stats.Batches++
		if err := s.db.QueryRowContext(ctx, `PRAGMA freelist_count`).Scan(&stats.FreelistAfter); err != nil {
			if firstErr == nil {
				firstErr = err
			}
			break
		}
		stats.PagesReclaimed = stats.FreelistBefore - stats.FreelistAfter
	}
	stats.Duration = time.Since(started)
	return stats, firstErr
}

// NativePruneBatchRows / NativePruneBudget 是清理默认批量与时间预算。
const (
	NativePruneBatchRows = 10000
	NativePruneBudget    = 5 * time.Second
	nativePrunePause     = 15 * time.Millisecond
	// nativePruneWatermarkMaxAge 限制 raw 清理依赖的聚合成功新鲜度。
	nativePruneWatermarkMaxAge = 30 * time.Minute
)

// NativePruneOptions 控制一次清理；零值使用默认批量/预算/限速。
type NativePruneOptions struct {
	DryRun    bool
	BatchRows int
	Budget    time.Duration
	Pause     time.Duration
	// VacuumPages <=0 使用 SQLiteStore 配置（默认 5000）。
	VacuumPages int
}

// NativePruneStats 是一轮清理的统计。
type NativePruneStats struct {
	Duration    time.Duration
	DeletedRaw  int64
	Deleted1m   int64
	Deleted5m   int64
	Deleted1h   int64
	Batches     int
	BusyErrors  int
	LastSuccess time.Time
	LastError   string
	RawSkipped  bool
	DryRun      bool
	BudgetHit   bool
	// Vacuum 统计（仅 auto_vacuum=INCREMENTAL 且实际删除后）。
	VacuumPages      int64
	VacuumBatches    int
	FreelistBefore   int64
	FreelistAfter    int64
	VacuumDuration   time.Duration
	VacuumBusyErrors int
}

// LastNativePruneStats 返回最近一轮清理统计。
func (s *SQLiteStore) LastNativePruneStats() NativePruneStats {
	s.pruneStatsMu.Lock()
	defer s.pruneStatsMu.Unlock()
	return s.lastPruneStats
}

// PruneNodeMetrics 兼容旧调用：按默认批量/预算执行一次清理。
func (s *SQLiteStore) PruneNodeMetrics(ctx context.Context, now time.Time) error {
	_, err := s.PruneNodeMetricsWithOptions(ctx, now, NativePruneOptions{})
	return err
}

// PruneNodeMetricsWithOptions 分批删除过期 raw/rollup。rollup 独立清理；
// raw 仅在上一次聚合成功且不超过 30 分钟时删除，上界取 min(now-rawRetention, watermark)。
// 每批独立事务，某层失败不影响其他层；预算耗尽后停止后续层。
func (s *SQLiteStore) PruneNodeMetricsWithOptions(ctx context.Context, now time.Time, opts NativePruneOptions) (NativePruneStats, error) {
	started := time.Now()
	stats := NativePruneStats{DryRun: opts.DryRun}
	if opts.BatchRows <= 0 {
		opts.BatchRows = NativePruneBatchRows
	}
	if opts.Budget <= 0 {
		opts.Budget = NativePruneBudget
	}
	if opts.Pause <= 0 {
		opts.Pause = nativePrunePause
	}
	now = now.UTC()
	if now.IsZero() {
		now = time.Now().UTC()
	}
	retention := s.retention()
	deadline := started.Add(opts.Budget)

	rawCutoff := now.Add(-retention.Raw)
	watermark := s.aggregateWatermarkTime()
	if watermark.IsZero() || now.Sub(watermark) > nativePruneWatermarkMaxAge {
		stats.RawSkipped = true
	} else {
		if watermark.Before(rawCutoff) {
			rawCutoff = watermark
		}
		if rawCutoff.After(now) {
			rawCutoff = now
		}
	}

	var firstErr error
	recordErr := func(err error) {
		if err == nil {
			return
		}
		if firstErr == nil {
			firstErr = err
		}
		stats.LastError = err.Error()
		if IsBusyError(err) {
			stats.BusyErrors++
		}
	}

	runLayer := func(table, where string, args []any) (int64, int) {
		if stats.BudgetHit || !time.Now().Before(deadline) {
			stats.BudgetHit = true
			return 0, 0
		}
		if opts.DryRun {
			var count int64
			if err := s.queryer(ctx).QueryRowContext(ctx, `SELECT COUNT(*) FROM `+table+` WHERE `+where, args...).Scan(&count); err != nil {
				recordErr(err)
				return 0, 0
			}
			return count, 0
		}
		var deleted int64
		batches := 0
		for {
			if !time.Now().Before(deadline) {
				stats.BudgetHit = true
				return deleted, batches
			}
			tx, err := s.db.BeginTx(ctx, nil)
			if err != nil {
				recordErr(err)
				return deleted, batches
			}
			result, err := tx.ExecContext(ctx, `DELETE FROM `+table+` WHERE rowid IN (
				SELECT rowid FROM `+table+` WHERE `+where+` LIMIT ?)`, append(args, opts.BatchRows)...)
			if err != nil {
				tx.Rollback()
				recordErr(err)
				return deleted, batches
			}
			affected, _ := result.RowsAffected()
			if err := tx.Commit(); err != nil {
				recordErr(err)
				return deleted, batches
			}
			deleted += affected
			batches++
			if affected < int64(opts.BatchRows) {
				return deleted, batches
			}
			if opts.Pause > 0 {
				select {
				case <-ctx.Done():
					recordErr(ctx.Err())
					return deleted, batches
				case <-time.After(opts.Pause):
				}
			}
		}
	}

	if !stats.RawSkipped {
		deleted, n := runLayer("node_metric_samples", `timestamp < ?`, []any{formatNativeTime(rawCutoff)})
		stats.DeletedRaw += deleted
		stats.Batches += n
	}
	if !stats.BudgetHit {
		deleted, n := runLayer("node_metric_rollups", `tier = ? AND bucket_start < ?`,
			[]any{NativeTier1m, formatNativeTime(now.Add(-retention.OneMinute))})
		stats.Deleted1m += deleted
		stats.Batches += n
	}
	if !stats.BudgetHit {
		deleted, n := runLayer("node_metric_rollups", `tier = ? AND bucket_start < ?`,
			[]any{NativeTier5m, formatNativeTime(now.Add(-retention.FiveMinute))})
		stats.Deleted5m += deleted
		stats.Batches += n
	}
	if !stats.BudgetHit {
		deleted, n := runLayer("node_metric_rollups", `tier = ? AND bucket_start < ?`,
			[]any{NativeTier1h, formatNativeTime(now.Add(-retention.OneHour))})
		stats.Deleted1h += deleted
		stats.Batches += n
	}

	if !opts.DryRun && (stats.DeletedRaw+stats.Deleted1m+stats.Deleted5m+stats.Deleted1h) > 0 {
		s.metricGeneration.Add(1)
		// 仅 auto_vacuum=INCREMENTAL 时小批量归还页；仍受本轮 prune 预算约束。
		if time.Now().Before(deadline) {
			pages := opts.VacuumPages
			if pages <= 0 {
				pages = s.vacuumPages()
			}
			vstats, err := s.drainFreePages(ctx, NativeDrainOptions{
				MaxPages: pages, BatchPages: nativeVacuumBatchPages,
				Budget: time.Until(deadline),
			})
			stats.VacuumPages = vstats.PagesReclaimed
			stats.VacuumBatches = vstats.Batches
			stats.FreelistBefore = vstats.FreelistBefore
			stats.FreelistAfter = vstats.FreelistAfter
			stats.VacuumDuration = vstats.Duration
			stats.VacuumBusyErrors = vstats.BusyErrors
			if err != nil {
				recordErr(err)
			}
		}
	}
	stats.Duration = time.Since(started)
	if firstErr == nil {
		stats.LastSuccess = now
	}
	s.pruneStatsMu.Lock()
	s.lastPruneStats = stats
	s.pruneStatsMu.Unlock()
	attrs := []any{
		"duration", stats.Duration, "raw", stats.DeletedRaw, "1m", stats.Deleted1m,
		"5m", stats.Deleted5m, "1h", stats.Deleted1h, "batches", stats.Batches,
		"busy_errors", stats.BusyErrors, "raw_skipped", stats.RawSkipped,
		"dry_run", stats.DryRun, "budget_hit", stats.BudgetHit,
		"vacuum_pages", stats.VacuumPages, "vacuum_batches", stats.VacuumBatches,
		"freelist_before", stats.FreelistBefore, "freelist_after", stats.FreelistAfter,
		"vacuum_duration", stats.VacuumDuration, "vacuum_busy", stats.VacuumBusyErrors,
	}
	if firstErr != nil {
		slog.Default().Warn("native metric prune completed with errors", append(attrs, "error", firstErr)...)
	} else {
		slog.Default().Info("native metric prune completed", attrs...)
	}
	return stats, firstErr
}

// EnableIncrementalVacuum 停机执行 auto_vacuum=INCREMENTAL + VACUUM。
// 调用方必须确保没有其他 Hub 实例/长事务；VACUUM 需要约 2x 磁盘空间。
func (s *SQLiteStore) EnableIncrementalVacuum(ctx context.Context) error {
	if _, err := s.db.ExecContext(ctx, `PRAGMA auto_vacuum=INCREMENTAL`); err != nil {
		return err
	}
	if _, err := s.db.ExecContext(ctx, `VACUUM`); err != nil {
		return err
	}
	var mode int
	if err := s.db.QueryRowContext(ctx, `PRAGMA auto_vacuum`).Scan(&mode); err != nil {
		return err
	}
	if mode != 2 {
		return fmt.Errorf("auto_vacuum = %d, want 2 (INCREMENTAL)", mode)
	}
	return nil
}

// NativeMetricGeneration 返回 raw 指标写入/清理代次；变化表示 overview 缓存失效。
func (s *SQLiteStore) NativeMetricGeneration() uint64 {
	return s.metricGeneration.Load()
}
