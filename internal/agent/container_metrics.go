package agent

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

const (
	// maxContainerMetrics 是单轮容器采集上限；高容器节点降低扫描频率而不是无限增长。
	maxContainerMetrics     = 32
	containerHighWatermark  = 16
	containerCollectTimeout = 5 * time.Second
	containerStatsTimeout   = 2 * time.Second
	containerProbeTimeout   = time.Second
	containerResponseLimit  = 4 << 20
)

type containerNetCounters struct {
	rx uint64
	tx uint64
}

type dockerContainer struct {
	ID    string   `json:"Id"`
	Names []string `json:"Names"`
	Image string   `json:"Image"`
	State string   `json:"State"`
}

type dockerCPUUsage struct {
	TotalUsage  uint64   `json:"total_usage"`
	PercpuUsage []uint64 `json:"percpu_usage"`
}

type dockerCPUStats struct {
	CPUUsage    dockerCPUUsage `json:"cpu_usage"`
	SystemUsage uint64         `json:"system_cpu_usage"`
	OnlineCPUs  uint32         `json:"online_cpus"`
}

type dockerMemoryStats struct {
	Usage uint64 `json:"usage"`
	Limit uint64 `json:"limit"`
	Stats struct {
		Cache             uint64 `json:"cache"`
		TotalInactiveFile uint64 `json:"total_inactive_file"`
	} `json:"stats"`
}

type dockerNetworkStats struct {
	RxBytes uint64 `json:"rx_bytes"`
	TxBytes uint64 `json:"tx_bytes"`
}

type dockerStats struct {
	CPUStats    dockerCPUStats                `json:"cpu_stats"`
	PreCPUStats dockerCPUStats                `json:"precpu_stats"`
	MemoryStats dockerMemoryStats             `json:"memory_stats"`
	Networks    map[string]dockerNetworkStats `json:"networks"`
}

// containerSamples 通过 Docker/Podman 兼容的 Unix socket API 采集运行中容器指标。
// socket 不存在、权限不足或 API 不可用时静默跳过，不伪造 0 值。
func (c *metricsCollector) containerSamples(now time.Time) []protocol.NodeMetricSample {
	if c.shouldSkipContainerScan(now) {
		// 高容器节点在两次完整扫描之间保留汇总能力，但不重复枚举/读取 stats。
		return containerCollectionSamples(now, c.containerLastSeen, c.containerLastSampled)
	}
	socket := c.firstContainerSocket()
	if socket == "" {
		return c.missingContainerCollection(now)
	}
	client, closeIdle := newDockerHTTPClient(socket)
	defer closeIdle()

	ctx, cancel := context.WithTimeout(context.Background(), containerCollectTimeout)
	defer cancel()

	var containers []dockerContainer
	if err := dockerGet(ctx, client, "/containers/json?limit="+fmt.Sprint(maxContainerMetrics+1), &containers); err != nil {
		c.containerLastScan = now
		return c.missingContainerCollection(now)
	}
	// 首次成功访问 Docker/Podman API 即可确认容器指标可用；空容器列表也算可用。
	c.containerCapable.Store(true)
	running := make([]dockerContainer, 0, len(containers))
	seen := make(map[string]bool)
	for _, container := range containers {
		if container.ID != "" && (container.State == "" || container.State == "running") && !seen[container.ID] {
			seen[container.ID] = true
			running = append(running, container)
		}
	}
	containers = running
	discovered := len(containers)
	if discovered > maxContainerMetrics+1 {
		discovered = maxContainerMetrics + 1
	}
	c.containerLastScan = now
	c.containerLastSeen = discovered
	if len(containers) > maxContainerMetrics {
		containers = containers[:maxContainerMetrics]
	}
	if len(containers) == 0 {
		c.containerNetPrev = map[string]containerNetCounters{}
		c.containerNetPrevAt = now
		c.containerNetInit = false
		c.containerLastSampled = 0
		return containerCollectionSamples(now, 0, 0)
	}

	elapsed := now.Sub(c.containerNetPrevAt).Seconds()
	currentNet := make(map[string]containerNetCounters, len(containers))
	samples := make([]protocol.NodeMetricSample, 0, len(containers)*4+3)
	sampled := 0
	for _, container := range containers {
		if ctx.Err() != nil {
			break
		}
		if container.State != "" && container.State != "running" {
			continue
		}
		statsCtx, statsCancel := context.WithTimeout(ctx, containerStatsTimeout)
		var stats dockerStats
		err := dockerGet(statsCtx, client, "/containers/"+url.PathEscape(container.ID)+"/stats?stream=false", &stats)
		statsCancel()
		if err != nil {
			continue
		}

		sampled++
		dims := map[string]string{"container": dockerContainerName(container), "container_id": container.ID}
		if value, ok := dockerCPUPercent(stats); ok {
			samples = append(samples, validMetricSample(now, "container.cpu.usage", dims, value))
		}
		if value, ok := dockerMemoryPercent(stats); ok {
			samples = append(samples, validMetricSample(now, "container.memory.usage", dims, value))
		}

		rx, tx, hasNetwork := dockerNetworkTotals(stats)
		if !hasNetwork {
			continue
		}
		currentNet[container.ID] = containerNetCounters{rx: rx, tx: tx}
		if !c.containerNetInit || elapsed <= 0 {
			continue
		}
		prev, ok := c.containerNetPrev[container.ID]
		if !ok || rx < prev.rx || tx < prev.tx {
			continue
		}
		samples = append(samples,
			validMetricSample(now, "container.network.rx_bps", dims, float64(rx-prev.rx)/elapsed),
			validMetricSample(now, "container.network.tx_bps", dims, float64(tx-prev.tx)/elapsed),
		)
	}
	c.containerNetPrev = currentNet
	c.containerNetPrevAt = now
	c.containerNetInit = true
	c.containerLastSampled = sampled
	return append(samples, containerCollectionSamples(now, discovered, sampled)...)
}

// shouldSkipContainerScan 在两次完整扫描之间返回 true。
// 超过 16 个容器时周期至少 60s；否则至少 30s。测试将 interval 置 0 关闭节流。
func (c *metricsCollector) shouldSkipContainerScan(now time.Time) bool {
	if c.containerScanInterval <= 0 || c.containerLastScan.IsZero() {
		return false
	}
	interval := c.containerScanInterval
	if interval < 30*time.Second {
		interval = 30 * time.Second
	}
	if c.containerLastSeen > containerHighWatermark {
		if interval < 60*time.Second {
			interval = 60 * time.Second
		}
	}
	return now.Sub(c.containerLastScan) < interval
}

func containerCollectionSamples(now time.Time, discovered, sampled int) []protocol.NodeMetricSample {
	return []protocol.NodeMetricSample{
		validMetricSample(now, "container.collection.discovered_count", nil, float64(discovered)),
		validMetricSample(now, "container.collection.sampled_count", nil, float64(sampled)),
		validMetricSample(now, "container.collection.limit", nil, maxContainerMetrics),
	}
}

// A previous success must not remain apparently current after enumeration fails.
func (c *metricsCollector) missingContainerCollection(now time.Time) []protocol.NodeMetricSample {
	if !c.containerCapable.Load() {
		return nil
	}
	return []protocol.NodeMetricSample{
		missingMetricSample(now, "container.collection.discovered_count", nil),
		missingMetricSample(now, "container.collection.sampled_count", nil),
		missingMetricSample(now, "container.collection.limit", nil),
	}
}

// containerMetricsAvailable 探测 Docker/Podman socket 与 API 是否可用。
// 已确认可用时直接返回；未确认时做一次短超时探测，避免 HELLO 被长时间阻塞。
func (c *metricsCollector) containerMetricsAvailable() bool {
	if c == nil {
		return false
	}
	if c.containerCapable.Load() {
		return true
	}
	socket := c.firstContainerSocket()
	if socket == "" {
		return false
	}
	client, closeIdle := newDockerHTTPClient(socket)
	defer closeIdle()
	ctx, cancel := context.WithTimeout(context.Background(), containerProbeTimeout)
	defer cancel()
	var containers []dockerContainer
	if err := dockerGet(ctx, client, "/containers/json?limit=1", &containers); err != nil {
		return false
	}
	c.containerCapable.Store(true)
	return true
}

func (c *metricsCollector) firstContainerSocket() string {
	candidates := []string{"/var/run/docker.sock", "/run/docker.sock", "/run/podman/podman.sock", "/var/run/podman/podman.sock"}
	switch {
	case c.containerSocketPath != "":
		candidates = []string{c.containerSocketPath}
	case c.root != "":
		// Host Integration 只使用宿主机根目录下的 socket，避免误连 Agent 自身容器
		// 的 Docker daemon；测试使用临时 root 时也天然隔离真实 socket。
		rooted := make([]string, 0, len(candidates))
		for _, path := range candidates {
			rooted = append(rooted, filepath.Join(c.root, path))
		}
		candidates = rooted
	}
	seen := map[string]bool{}
	for _, path := range candidates {
		if path == "" || seen[path] {
			continue
		}
		seen[path] = true
		info, err := os.Stat(path)
		if err != nil || info.Mode()&os.ModeSocket == 0 {
			continue
		}
		return path
	}
	return ""
}

func newDockerHTTPClient(socket string) (*http.Client, func()) {
	transport := &http.Transport{
		DisableKeepAlives: true,
		DialContext: func(ctx context.Context, _, _ string) (net.Conn, error) {
			dialer := &net.Dialer{}
			return dialer.DialContext(ctx, "unix", socket)
		},
	}
	client := &http.Client{Transport: transport, Timeout: containerCollectTimeout}
	return client, transport.CloseIdleConnections
}

func dockerGet(ctx context.Context, client *http.Client, path string, out any) error {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, "http://docker"+path, nil)
	if err != nil {
		return err
	}
	response, err := client.Do(request)
	if err != nil {
		return err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return fmt.Errorf("docker api %s: status %d", path, response.StatusCode)
	}
	decoder := json.NewDecoder(io.LimitReader(response.Body, containerResponseLimit))
	return decoder.Decode(out)
}

func dockerContainerName(container dockerContainer) string {
	for _, name := range container.Names {
		if trimmed := strings.TrimPrefix(name, "/"); trimmed != "" {
			return trimmed
		}
	}
	if len(container.ID) > 12 {
		return container.ID[:12]
	}
	return container.ID
}

// dockerCPUPercent 使用 Docker stats 的 precpu_stats 计算两次采样差值。
// precpu_stats 尚为空时跳过，避免把容器启动以来的累计值当成瞬时使用率。
func dockerCPUPercent(stats dockerStats) (float64, bool) {
	previousSystem := stats.PreCPUStats.SystemUsage
	previousTotal := stats.PreCPUStats.CPUUsage.TotalUsage
	currentSystem := stats.CPUStats.SystemUsage
	currentTotal := stats.CPUStats.CPUUsage.TotalUsage
	if previousSystem == 0 || currentSystem <= previousSystem || currentTotal < previousTotal {
		return 0, false
	}
	cpus := stats.CPUStats.OnlineCPUs
	if cpus == 0 {
		cpus = uint32(len(stats.CPUStats.CPUUsage.PercpuUsage))
	}
	if cpus == 0 {
		cpus = 1
	}
	value := float64(currentTotal-previousTotal) / float64(currentSystem-previousSystem) * float64(cpus) * 100
	// 与 docker stats 语义一致：单核满载为 100%，多核容器可超过 100%。
	if value < 0 {
		value = 0
	}
	return value, true
}

func dockerMemoryPercent(stats dockerStats) (float64, bool) {
	limit := stats.MemoryStats.Limit
	if limit == 0 {
		return 0, false
	}
	usage := stats.MemoryStats.Usage
	cache := stats.MemoryStats.Stats.Cache
	if cache == 0 {
		cache = stats.MemoryStats.Stats.TotalInactiveFile
	}
	if cache > 0 && cache <= usage {
		usage -= cache
	}
	return clampPercent(float64(usage) / float64(limit) * 100), true
}

func dockerNetworkTotals(stats dockerStats) (uint64, uint64, bool) {
	if len(stats.Networks) == 0 {
		return 0, 0, false
	}
	var rx, tx uint64
	for _, network := range stats.Networks {
		rx += network.RxBytes
		tx += network.TxBytes
	}
	return rx, tx, true
}
