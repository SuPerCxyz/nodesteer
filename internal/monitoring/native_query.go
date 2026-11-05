package monitoring

import (
	"context"
	"errors"
	"math"
	"sort"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// NativePoint 历史查询返回点。原始点带 quality（1 有效、2 缺失）；
// 聚合桶带 quality=0、count>0，并可选带 min/max。缺失点 value 为 null，绝不为 0。
type NativePoint struct {
	Timestamp time.Time `json:"timestamp"`
	Value     *float64  `json:"value"`
	Quality   int       `json:"quality"`
	Count     uint64    `json:"count,omitempty"`
	Expected  uint64    `json:"-"`
	Missing   uint64    `json:"-"`
	Min       *float64  `json:"min,omitempty"`
	Max       *float64  `json:"max,omitempty"`
	// FirstTS/LastTS 是桶内有效原始样本的首末时间，用于裁剪桶覆盖范围。
	FirstTS time.Time `json:"-"`
	LastTS  time.Time `json:"-"`
}

// NativeGap 数据缺口，区间语义为 [Start, End)。
type NativeGap struct {
	Start time.Time `json:"start"`
	End   time.Time `json:"end"`
}

// NativeSummary 区间摘要。统计口径是响应点（原始点或聚合桶），不是底层样本总数。
// Sum 是有效样本值之和（聚合桶按 Count 加权）；Average 是加权平均。
type NativeSummary struct {
	Count    int      `json:"count"`
	Expected int      `json:"expected"`
	Missing  int      `json:"missing"`
	Sum      float64  `json:"sum"`
	Min      *float64 `json:"min"`
	Max      *float64 `json:"max"`
	Average  *float64 `json:"average"`
	Coverage float64  `json:"coverage"`
}

// NativeHistory 原生指标历史响应。
type NativeHistory struct {
	State   string         `json:"state"`
	Tier    string         `json:"tier"`
	Step    time.Duration  `json:"stepNs"`
	Unit    string         `json:"unit,omitempty"`
	Points  []NativePoint  `json:"points"`
	Gaps    []NativeGap    `json:"gaps"`
	Summary *NativeSummary `json:"summary,omitempty"`
}

// NativeQuery 历史查询参数。Start/End 为零值时分别回退到 24 小时前和当前时间。
// Offline 由 API 层根据节点状态传入；为 true 时 state 固定为 agent_offline。
type NativeQuery struct {
	NodeID    string
	Metric    string
	SeriesKey string
	Start     time.Time
	End       time.Time
	Step      time.Duration
	Budget    int
	Offline   bool
	// SampleInterval 是调用方已知的有效采样周期；为 0 时由返回的 raw 点推导。
	SampleInterval time.Duration
}

const (
	// NativeDefaultBudget 默认点预算。
	NativeDefaultBudget = 2000
	// NativeMaxPoints 单次响应的硬上限，避免无界内存。
	NativeMaxPoints = store.NativeMaxQueryRows
)

var (
	ErrNativeInvalidQuery  = errors.New("invalid native metric query")
	ErrNativeInvalidRange  = errors.New("invalid native metric time range")
	ErrNativeRangeTooLarge = errors.New("native metric range exceeds point budget")
)

type nativeTier struct {
	name string
	step time.Duration
}

var nativeTiers = []nativeTier{
	{name: store.NativeTierRaw, step: store.NativeRawStep},
	{name: store.NativeTier1m, step: time.Minute},
	{name: store.NativeTier5m, step: 5 * time.Minute},
	{name: store.NativeTier1h, step: time.Hour},
}

// QueryNativeHistory 选择 tier、读取原始样本或聚合桶，并生成点、缺口和摘要。
//
// tier 选择规则：
//   - 显式 step 时，从 step 不小于请求值的最细 tier 开始；
//   - 按 range/step 估算点数，选择不超过 budget 的最细 tier；
//   - 所有 tier 都超过预算时使用 1h；仍超过硬上限则返回 ErrNativeRangeTooLarge；
//   - 所选 tier 无数据时逐级回退到更细 tier，最终回退 raw，并标注实际 tier。
//
// 回退 raw 且实际点数超过预算时，会在内存中按所选 tier 步长重新聚合，
// 仍然标注 tier=raw，stepNs 为实际聚合步长。
func QueryNativeHistory(ctx context.Context, reader store.NativeMetricReader, query NativeQuery) (*NativeHistory, error) {
	if reader == nil {
		return nil, errors.New("native metric reader is nil")
	}
	if !store.ValidNativeMetric(query.Metric) {
		return nil, ErrNativeInvalidQuery
	}
	if query.SeriesKey != "" && (len(query.SeriesKey) > 1024 || containsControl(query.SeriesKey)) {
		return nil, ErrNativeInvalidQuery
	}

	end := query.End.UTC()
	if query.End.IsZero() {
		end = time.Now().UTC()
	}
	start := query.Start.UTC()
	if query.Start.IsZero() {
		start = end.Add(-24 * time.Hour)
	}
	if !start.Before(end) {
		return nil, ErrNativeInvalidRange
	}
	// 回填默认范围，后续 tier 查询必须使用归一化后的 UTC 时间。
	query.Start = start
	query.End = end

	budget := query.Budget
	if budget <= 0 {
		budget = NativeDefaultBudget
	}
	if budget > NativeMaxPoints {
		budget = NativeMaxPoints
	}
	selected, err := selectNativeTier(end.Sub(start), query.Step, budget)
	if err != nil {
		return nil, err
	}

	points := []NativePoint{}
	tierName := nativeTiers[selected].name
	step := nativeTiers[selected].step
	selectedStep := nativeTiers[selected].step
	isBucket := false
	for index := selected; index >= 0; index-- {
		fetched, bucket, err := fetchNativeTier(ctx, reader, query, index)
		if err != nil {
			return nil, err
		}
		if len(fetched) == 0 {
			continue
		}
		points = fetched
		tierName = nativeTiers[index].name
		step = nativeTiers[index].step
		isBucket = bucket
		break
	}

	hadPoints := len(points) > 0
	sampleInterval := query.SampleInterval
	if sampleInterval <= 0 {
		if isBucket {
			sampleInterval = nativeBucketSampleInterval(points, step, tierName)
		} else {
			sampleInterval = store.NativeIntervalFromTimes(nativePointTimes(points))
		}
	}
	if sampleInterval <= 0 {
		sampleInterval = store.NativeRawStep
	}
	// raw 回退或实际采样频率高于设计步长时，按所选 tier 步长降采样，
	// 避免 24h/5s 采样把数万个原始点直接返回。
	if !isBucket && len(points) > budget {
		points = downsampleNativePoints(points, selectedStep, sampleInterval)
		step = selectedStep
		isBucket = true
	}
	if len(points) > NativeMaxPoints {
		return nil, ErrNativeRangeTooLarge
	}

	gaps := buildNativeGaps(points, start, end, step, isBucket, sampleInterval)
	valid := 0
	for _, point := range points {
		if nativePointValid(point) {
			valid++
		}
	}
	state := "ready"
	switch {
	case query.Offline:
		state = "agent_offline"
	case len(points) == 0 && !hadPoints:
		state = "no_data"
	case valid == 0:
		state = "data_gap"
	case len(gaps) > 0:
		state = "data_gap"
	}

	return &NativeHistory{
		State:   state,
		Tier:    tierName,
		Step:    step,
		Unit:    nativeMetricUnit(query.Metric),
		Points:  points,
		Gaps:    gaps,
		Summary: buildNativeSummary(points, start, end, step, sampleInterval, isBucket),
	}, nil
}

func nativePointTimes(points []NativePoint) []time.Time {
	times := make([]time.Time, 0, len(points))
	for _, point := range points {
		times = append(times, point.Timestamp)
	}
	return times
}

func nativeTierSourceInterval(tier string) time.Duration {
	switch tier {
	case store.NativeTier1m:
		return store.NativeRawStep
	case store.NativeTier5m:
		return time.Minute
	case store.NativeTier1h:
		return 5 * time.Minute
	default:
		return store.NativeRawStep
	}
}

// nativeBucketSampleInterval 从 rollup 的 Expected 还原源采样周期；
// 老数据缺少 Expected 时回退到该 tier 的源步长。
func nativeBucketSampleInterval(points []NativePoint, step time.Duration, tier string) time.Duration {
	for _, point := range points {
		if point.Expected == 0 {
			continue
		}
		interval := step / time.Duration(point.Expected)
		if interval >= store.NativeMinSampleInterval && interval <= step {
			return interval
		}
	}
	return nativeTierSourceInterval(tier)
}

func containsControl(value string) bool {
	for index := 0; index < len(value); index++ {
		if value[index] < 0x20 || value[index] == 0x7f {
			return true
		}
	}
	return false
}

func selectNativeTier(rangeDuration, requestedStep time.Duration, budget int) (int, error) {
	startIndex := 0
	if requestedStep > 0 {
		startIndex = len(nativeTiers) - 1
		for index, tier := range nativeTiers {
			if tier.step >= requestedStep {
				startIndex = index
				break
			}
		}
	}

	selected := len(nativeTiers) - 1
	for index := startIndex; index < len(nativeTiers); index++ {
		points := ceilDuration(rangeDuration, nativeTiers[index].step)
		if points <= int64(budget) {
			selected = index
			break
		}
	}
	if estimated := ceilDuration(rangeDuration, nativeTiers[selected].step); estimated > int64(NativeMaxPoints) {
		return 0, ErrNativeRangeTooLarge
	}
	return selected, nil
}

func ceilDuration(total, step time.Duration) int64 {
	if total <= 0 || step <= 0 {
		return 0
	}
	// 极端范围（例如 0001 年到 9999 年）会让 total+step-1 溢出；
	// 饱和到最大 duration 后由调用方按超预算处理。
	if total > time.Duration(1<<63-1)-step {
		return int64(1<<63 - 1)
	}
	return int64((total + step - 1) / step)
}

func fetchNativeTier(ctx context.Context, reader store.NativeMetricReader, query NativeQuery, index int) ([]NativePoint, bool, error) {
	tier := nativeTiers[index]
	limit := NativeMaxPoints + 1
	if tier.name == store.NativeTierRaw {
		samples, err := queryNativeRawSamples(ctx, reader, query)
		if err != nil {
			return nil, false, err
		}
		points := make([]NativePoint, 0, len(samples))
		for _, sample := range samples {
			point := NativePoint{
				Timestamp: sample.Timestamp.UTC(),
				Value:     sample.Value,
				Quality:   sample.Quality,
			}
			if point.Value != nil && !nativeFiniteValue(*point.Value) {
				point.Value = nil
				point.Quality = store.NativeQualityMissing
			}
			points = append(points, point)
		}
		sortNativePoints(points)
		return points, false, nil
	}

	// 聚合桶可能跨越查询起点；包含与 start 重叠的首个桶，避免把部分桶误报为 gap。
	rollupStart := query.Start.Truncate(tier.step)
	rollups, err := reader.QueryNodeMetricRollups(ctx, query.NodeID, query.Metric, query.SeriesKey, tier.name, rollupStart, query.End, limit)
	if err != nil {
		return nil, false, err
	}
	if len(rollups) > NativeMaxPoints {
		return nil, false, ErrNativeRangeTooLarge
	}
	points := make([]NativePoint, 0, len(rollups))
	for _, rollup := range rollups {
		if rollup.Count == 0 {
			continue
		}
		value := rollup.Sum / float64(rollup.Count)
		point := NativePoint{
			Timestamp: rollup.BucketStart.UTC(),
			Quality:   0,
			Count:     rollup.Count,
			Expected:  rollup.Expected,
			Missing:   rollup.Missing,
			FirstTS:   rollup.FirstTS.UTC(),
			LastTS:    rollup.LastTS.UTC(),
		}
		if nativeFiniteValue(value) {
			point.Value = &value
		} else {
			// 非有限值不进入 JSON；按缺失点返回，避免 200 空 body。
			point.Quality = store.NativeQualityMissing
		}
		if nativeFiniteValue(rollup.Min) && nativeFiniteValue(rollup.Max) {
			minimum, maximum := rollup.Min, rollup.Max
			point.Min, point.Max = &minimum, &maximum
		}
		points = append(points, point)
	}
	sortNativePoints(points)
	return points, true, nil
}

// nativeRawMaxRows 是 raw 回退分块读取的硬上限，避免异常高频数据无界占用内存。
const nativeRawMaxRows = 10 * store.NativeMaxQueryRows

// queryNativeRawSamples 分块读取 raw 样本；store 单次 limit 不再是总量上限。
func queryNativeRawSamples(ctx context.Context, reader store.NativeMetricReader, query NativeQuery) ([]store.NativeSample, error) {
	const chunk = store.NativeMaxQueryRows
	start := query.Start
	samples := make([]store.NativeSample, 0, chunk)
	for {
		part, err := reader.QueryNodeMetricSamplesLimited(ctx, query.NodeID, query.Metric, query.SeriesKey, start, query.End, chunk)
		if err != nil {
			return nil, err
		}
		samples = append(samples, part...)
		if len(samples) > nativeRawMaxRows {
			return nil, ErrNativeRangeTooLarge
		}
		if len(part) < chunk {
			return samples, nil
		}
		last := part[len(part)-1].Timestamp
		next := last.Add(time.Nanosecond)
		if !next.Before(query.End) {
			return samples, nil
		}
		start = next
	}
}

func nativeFiniteValue(value float64) bool {
	return !math.IsNaN(value) && !math.IsInf(value, 0)
}

func sortNativePoints(points []NativePoint) {
	sort.SliceStable(points, func(left, right int) bool {
		return points[left].Timestamp.Before(points[right].Timestamp)
	})
}

func nativePointValid(point NativePoint) bool {
	if point.Value == nil || math.IsNaN(*point.Value) || math.IsInf(*point.Value, 0) {
		return false
	}
	if point.Quality == store.NativeQualityValid {
		return true
	}
	return point.Quality == 0 && point.Count > 0
}

type nativeDownsampleBucket struct {
	start   time.Time
	count   uint64
	sum     float64
	minimum float64
	maximum float64
	firstTS time.Time
	lastTS  time.Time
}

func downsampleNativePoints(points []NativePoint, step, sampleInterval time.Duration) []NativePoint {
	if step <= 0 {
		return points
	}
	expected := store.NativeExpectedCount(step, sampleInterval)
	buckets := make([]nativeDownsampleBucket, 0, len(points)/2)
	for _, point := range points {
		start := point.Timestamp.Truncate(step)
		if len(buckets) == 0 || !buckets[len(buckets)-1].start.Equal(start) {
			buckets = append(buckets, nativeDownsampleBucket{start: start})
		}
		bucket := &buckets[len(buckets)-1]
		if point.Value == nil || point.Quality != store.NativeQualityValid || !nativeFiniteValue(*point.Value) {
			continue
		}
		value := *point.Value
		if bucket.count == 0 {
			bucket.minimum = value
			bucket.maximum = value
			bucket.firstTS = point.Timestamp.UTC()
		} else {
			if value < bucket.minimum {
				bucket.minimum = value
			}
			if value > bucket.maximum {
				bucket.maximum = value
			}
		}
		bucket.count++
		bucket.sum += value
		bucket.lastTS = point.Timestamp.UTC()
	}

	result := make([]NativePoint, 0, len(buckets))
	for _, bucket := range buckets {
		missing := uint64(0)
		if bucket.count < expected {
			missing = expected - bucket.count
		}
		point := NativePoint{
			Timestamp: bucket.start,
			Quality:   store.NativeQualityMissing,
			Count:     bucket.count,
			Expected:  expected,
			Missing:   missing,
		}
		if bucket.count > 0 {
			value := bucket.sum / float64(bucket.count)
			minimum, maximum := bucket.minimum, bucket.maximum
			point.Value = &value
			point.Quality = 0
			point.Min, point.Max = &minimum, &maximum
			point.FirstTS, point.LastTS = bucket.firstTS, bucket.lastTS
		}
		result = append(result, point)
	}
	return result
}

func buildNativeGaps(points []NativePoint, start, end time.Time, step time.Duration, bucket bool, sampleInterval time.Duration) []NativeGap {
	if !start.Before(end) {
		return []NativeGap{}
	}
	if bucket {
		return buildBucketGaps(points, start, end, step, sampleInterval)
	}
	return buildRawGaps(points, start, end, sampleInterval)
}

// buildBucketGaps 用桶内 FirstTS/LastTS 裁剪覆盖范围。完整桶不再无条件覆盖
// 整个 bucket，避免首桶/尾桶只含少量样本时把真实缺口隐藏掉。
func buildBucketGaps(points []NativePoint, start, end time.Time, step, sampleInterval time.Duration) []NativeGap {
	if step <= 0 {
		return []NativeGap{}
	}
	coverages := make([]NativeGap, 0, len(points))
	for _, point := range points {
		if !nativePointValid(point) {
			continue
		}
		bucketStart := point.Timestamp.UTC().Truncate(step)
		coverageStart := point.FirstTS.UTC()
		if coverageStart.IsZero() {
			coverageStart = bucketStart
		}
		coverageEnd := point.LastTS.UTC()
		if coverageEnd.IsZero() {
			coverageEnd = bucketStart.Add(step)
		} else if sampleInterval > 0 {
			coverageEnd = coverageEnd.Add(sampleInterval)
		}
		if coverageStart.Before(start) {
			coverageStart = start
		}
		if coverageEnd.After(end) {
			coverageEnd = end
		}
		coverages = appendNativeGap(coverages, coverageStart, coverageEnd)
	}
	gaps := make([]NativeGap, 0)
	cursor := start.UTC()
	for _, coverage := range coverages {
		if coverage.Start.After(cursor) {
			gaps = appendNativeGap(gaps, cursor, coverage.Start)
		}
		if coverage.End.After(cursor) {
			cursor = coverage.End
		}
	}
	if cursor.Before(end) {
		gaps = appendNativeGap(gaps, cursor, end)
	}
	return gaps
}

func buildRawGaps(points []NativePoint, start, end time.Time, sampleInterval time.Duration) []NativeGap {
	validTimes := make([]time.Time, 0, len(points))
	for _, point := range points {
		if nativePointValid(point) {
			validTimes = append(validTimes, point.Timestamp.UTC())
		}
	}
	sort.Slice(validTimes, func(left, right int) bool { return validTimes[left].Before(validTimes[right]) })
	if len(validTimes) == 0 {
		if len(points) > 0 {
			return []NativeGap{{Start: start, End: end}}
		}
		return []NativeGap{}
	}
	if sampleInterval <= 0 {
		sampleInterval = store.NativeRawStep
	}
	threshold := sampleInterval + sampleInterval/2
	gaps := []NativeGap{}
	if validTimes[0].Sub(start) > threshold {
		gaps = appendNativeGap(gaps, start, validTimes[0])
	}
	for index := 1; index < len(validTimes); index++ {
		if validTimes[index].Sub(validTimes[index-1]) > threshold {
			gaps = appendNativeGap(gaps, validTimes[index-1].Add(sampleInterval), validTimes[index])
		}
	}
	if end.Sub(validTimes[len(validTimes)-1]) > threshold {
		gaps = appendNativeGap(gaps, validTimes[len(validTimes)-1].Add(sampleInterval), end)
	}
	return gaps
}

func appendNativeGap(gaps []NativeGap, start, end time.Time) []NativeGap {
	start = start.UTC()
	end = end.UTC()
	if !start.Before(end) {
		return gaps
	}
	if len(gaps) > 0 && !start.After(gaps[len(gaps)-1].End) {
		if end.After(gaps[len(gaps)-1].End) {
			gaps[len(gaps)-1].End = end
		}
		return gaps
	}
	return append(gaps, NativeGap{Start: start, End: end})
}

func buildNativeSummary(points []NativePoint, start, end time.Time, step, sampleInterval time.Duration, bucket bool) *NativeSummary {
	if len(points) == 0 {
		return nil
	}
	summary := &NativeSummary{}
	var sum float64
	var weightedCount int
	var minimum, maximum float64
	for _, point := range points {
		if bucket {
			summary.Expected += int(point.Expected)
			summary.Missing += int(point.Missing)
		}
		if !nativePointValid(point) {
			continue
		}
		count := 1
		if bucket {
			count = int(point.Count)
		}
		if count < 1 {
			continue
		}
		summary.Count++
		weightedCount += count
		sum += *point.Value * float64(count)
		valueMin, valueMax := *point.Value, *point.Value
		if point.Min != nil {
			valueMin = *point.Min
		}
		if point.Max != nil {
			valueMax = *point.Max
		}
		if summary.Count == 1 || valueMin < minimum {
			minimum = valueMin
		}
		if summary.Count == 1 || valueMax > maximum {
			maximum = valueMax
		}
	}
	if bucket {
		if summary.Expected == 0 {
			summary.Expected = int(ceilDuration(end.Sub(start), step))
		}
		if summary.Missing > summary.Expected {
			summary.Missing = summary.Expected
		}
	} else {
		summary.Expected = int(ceilDuration(end.Sub(start), sampleInterval))
		if summary.Expected > summary.Count {
			summary.Missing = summary.Expected - summary.Count
		}
	}
	summary.Sum = sum
	if weightedCount > 0 {
		average := sum / float64(weightedCount)
		summary.Average = &average
		summary.Min = &minimum
		summary.Max = &maximum
	}
	if summary.Expected > 0 {
		summary.Coverage = float64(summary.Count) / float64(summary.Expected)
		if summary.Coverage > 1 {
			summary.Coverage = 1
		}
	}
	return summary
}

func nativeMetricUnit(metric string) string {
	switch metric {
	case "cpu.usage", "cpu.user", "cpu.nice", "cpu.system", "cpu.idle",
		"cpu.iowait", "cpu.irq", "cpu.softirq", "cpu.steal",
		"memory.usage", "disk.usage", "memory.swap_usage", "container.cpu.usage", "container.memory.usage", "filesystem.inodes_usage":
		return "%"
	case "memory.total_bytes", "memory.used_bytes", "memory.available_bytes",
		"memory.cached_bytes", "memory.buffers_bytes", "memory.free_bytes",
		"filesystem.total_bytes", "filesystem.used_bytes", "memory.swap_total_bytes", "memory.swap_used_bytes", "memory.swap_free_bytes":
		return "B"
	case "network.rx_bps", "network.tx_bps", "disk.read_bps", "disk.write_bps", "container.network.rx_bps", "container.network.tx_bps":
		return "B/s"
	case "network.rx_errors", "network.tx_errors", "network.rx_dropped", "network.tx_dropped":
		return "/s"
	case "disk.read_iops", "disk.write_iops":
		return "ops/s"
	case "load.1", "load.5", "load.15":
		return "load"
	case "system.uptime":
		return "s"
	case "system.temperature_c":
		return "°C"
	case "system.processes", "system.processes_running",
		"container.collection.discovered_count", "container.collection.sampled_count", "container.collection.limit":
		return "count"
	default:
		return ""
	}
}
