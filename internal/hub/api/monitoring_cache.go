package api

import (
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/monitoring"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

// overviewMetricsCache 只缓存昂贵的 latest/interval 查询结果；
// 节点状态每次从 ListNodes 读取，避免缓存 offline 状态。
type overviewMetricsCache struct {
	mu      sync.Mutex
	entries map[string]overviewMetricsEntry
	ttl     time.Duration
	max     int
}

type overviewMetricsEntry struct {
	at         time.Time
	generation uint64
	samples    map[string][]store.NativeSample
	intervals  map[string]time.Duration
}

func newOverviewMetricsCache() *overviewMetricsCache {
	return &overviewMetricsCache{entries: map[string]overviewMetricsEntry{}, ttl: 15 * time.Second, max: 64}
}

func (c *overviewMetricsCache) get(key string, generation uint64, now time.Time) (map[string][]store.NativeSample, map[string]time.Duration, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	entry, ok := c.entries[key]
	if !ok || entry.generation != generation || now.Sub(entry.at) >= c.ttl {
		delete(c.entries, key)
		return nil, nil, false
	}
	return entry.samples, entry.intervals, true
}

func (c *overviewMetricsCache) put(key string, generation uint64, samples map[string][]store.NativeSample, intervals map[string]time.Duration, now time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.entries) >= c.max {
		c.entries = map[string]overviewMetricsEntry{}
	}
	c.entries[key] = overviewMetricsEntry{
		at: now, generation: generation, samples: samples, intervals: intervals,
	}
}

func (c *overviewMetricsCache) invalidate() {
	c.mu.Lock()
	c.entries = map[string]overviewMetricsEntry{}
	c.mu.Unlock()
}

type seriesCacheEntry struct {
	at     time.Time
	series map[string][]store.NativeSeries
}

type seriesCache struct {
	mu      sync.Mutex
	entries map[string]seriesCacheEntry
	ttl     time.Duration
	max     int
}

func newSeriesCache() *seriesCache {
	return &seriesCache{entries: map[string]seriesCacheEntry{}, ttl: time.Minute, max: 256}
}

func (c *seriesCache) get(key string, now time.Time) (map[string][]store.NativeSeries, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	entry, ok := c.entries[key]
	if !ok || now.Sub(entry.at) >= c.ttl {
		delete(c.entries, key)
		return nil, false
	}
	return entry.series, true
}

func (c *seriesCache) put(key string, series map[string][]store.NativeSeries, now time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.entries) >= c.max {
		c.entries = map[string]seriesCacheEntry{}
	}
	c.entries[key] = seriesCacheEntry{at: now, series: series}
}

type historyCacheEntry struct {
	at      time.Time
	history *monitoring.NativeHistory
}

type historyCache struct {
	mu      sync.Mutex
	entries map[string]historyCacheEntry
	ttl     time.Duration
	max     int
}

func newHistoryCache() *historyCache {
	return &historyCache{entries: map[string]historyCacheEntry{}, ttl: 30 * time.Second, max: 256}
}

func (c *historyCache) get(key string, now time.Time) (*monitoring.NativeHistory, bool) {
	c.mu.Lock()
	defer c.mu.Unlock()
	entry, ok := c.entries[key]
	if !ok || now.Sub(entry.at) >= c.ttl {
		delete(c.entries, key)
		return nil, false
	}
	return entry.history, true
}

func (c *historyCache) put(key string, history *monitoring.NativeHistory, now time.Time) {
	c.mu.Lock()
	defer c.mu.Unlock()
	if len(c.entries) >= c.max {
		c.entries = map[string]historyCacheEntry{}
	}
	c.entries[key] = historyCacheEntry{at: now, history: history}
}

func overviewCacheKey(nodeIDs []string) string {
	ids := append([]string(nil), nodeIDs...)
	sort.Strings(ids)
	return strings.Join(ids, ",")
}

func seriesCacheKey(nodeID string, metrics []string) string {
	items := append([]string(nil), metrics...)
	sort.Strings(items)
	return nodeID + "|" + strings.Join(items, ",")
}

func historyCacheKey(nodeID, metric, series string, start, end time.Time, step time.Duration, offline bool) string {
	return nodeID + "|" + metric + "|" + series + "|" + start.UTC().Format(time.RFC3339Nano) + "|" +
		end.UTC().Format(time.RFC3339Nano) + "|" + strconv.FormatInt(int64(step), 10) + "|" + strconv.FormatBool(offline)
}
