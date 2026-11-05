package agent

import (
	"context"
	"strings"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

const (
	defaultMetricsInterval    = 30 * time.Second
	minMetricsInterval        = 5 * time.Second
	maxMetricsInterval        = 300 * time.Second
	metricsMaxBatchSamples    = 500
	metricsMaxBufferedBatches = 10
)

// normalizeMetricsInterval 将采集周期限制在 5s~300s；零值回退默认 30s。
func normalizeMetricsInterval(interval time.Duration) time.Duration {
	if interval <= 0 {
		return defaultMetricsInterval
	}
	if interval < minMetricsInterval {
		return minMetricsInterval
	}
	if interval > maxMetricsInterval {
		return maxMetricsInterval
	}
	return interval
}

// metricsSupported 判断当前部署形态是否能可靠读取节点指标。
// Docker 基础模式只能看到容器自身 /proc，不能代表节点，按不可用处理。
func metricsSupported(mode string, hostIntegration bool) bool {
	if mode == models.DeploymentModeDocker && !hostIntegration {
		return false
	}
	return true
}

// metricsRoot 返回读取 /proc 与挂载点时的宿主机根目录；空字符串表示本机。
func metricsRoot(cfg Config) string {
	if cfg.HostIntegration || cfg.DeploymentMode == models.DeploymentModeDockerHostInt {
		if cfg.HostRoot != "" {
			return cfg.HostRoot
		}
		return "/host"
	}
	return ""
}

// metricsQueue 是有界的内存批次队列。发送失败或断线时保留批次，
// 连接恢复后按入队顺序 flush；超过容量时丢弃最旧批次，避免无限增长。
type metricsQueue struct {
	mu      sync.Mutex
	batches []protocol.NodeMetricsPayload
	send    func(protocol.Envelope) bool
}

func newMetricsQueue(send func(protocol.Envelope) bool) *metricsQueue {
	return &metricsQueue{send: send}
}

// enqueue 入队并尝试发送；返回本次因容量上限丢弃的批次数。
func (q *metricsQueue) enqueue(payload protocol.NodeMetricsPayload) int {
	q.mu.Lock()
	defer q.mu.Unlock()
	q.batches = append(q.batches, payload)
	dropped := 0
	for len(q.batches) > metricsMaxBufferedBatches {
		q.batches[0] = protocol.NodeMetricsPayload{}
		q.batches = q.batches[1:]
		dropped++
	}
	q.flushLocked()
	return dropped
}

// flush 按顺序发送当前队列，遇到首次失败即停止并保留未发送批次。
func (q *metricsQueue) flush() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.flushLocked()
}

func (q *metricsQueue) flushLocked() int {
	sent := 0
	for len(q.batches) > 0 {
		if q.send == nil || !q.send(protocol.NewEnvelope(protocol.MsgNodeMetrics, "", q.batches[0])) {
			break
		}
		q.batches[0] = protocol.NodeMetricsPayload{}
		q.batches = q.batches[1:]
		sent++
	}
	if len(q.batches) == 0 {
		q.batches = nil
	}
	return sent
}

func (q *metricsQueue) pending() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return len(q.batches)
}

// metricsLoop 按配置周期采集并上报，首次立即采集以建立 CPU/网络差值基线。
func (a *Agent) metricsLoop(ctx context.Context) {
	a.collectAndReportMetrics()
	ticker := time.NewTicker(normalizeMetricsInterval(a.cfg.MetricsInterval))
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			a.collectAndReportMetrics()
			a.refreshBlockInventory()
		}
	}
}

// collectAndReportMetrics 采集一轮指标并按 500 样本上限拆批入队。
// 尚未拿到 node_id 时仍执行采集以建立差值基线，但不生成无主批次。
func (a *Agent) collectAndReportMetrics() {
	if a.metricsCollector == nil || a.metricsQueue == nil {
		return
	}
	// 先补发历史缓冲，再采集本轮；发送门禁在队列 send 回调内。
	a.flushMetrics()
	samples := a.metricsCollector.collect(time.Now())
	if len(samples) == 0 || a.nodeID == "" {
		return
	}
	for _, batch := range splitMetricsBatches(samples, metricsMaxBatchSamples) {
		payload := protocol.NodeMetricsPayload{
			NodeID:   a.nodeID,
			Sequence: a.nextMetricsSequence(),
			Samples:  batch,
		}
		if dropped := a.metricsQueue.enqueue(payload); dropped > 0 && a.logger != nil {
			a.logger.Warn("node metrics buffer overflow; dropped oldest batches",
				"dropped", dropped, "capacity", metricsMaxBufferedBatches)
		}
	}
	if pending := a.metricsQueue.pending(); pending > 0 && a.logger != nil && a.conn != nil && a.conn.HandshakeComplete() {
		a.logger.Debug("node metrics send deferred", "pending", pending)
	}
}

// flushMetrics 在连接恢复后按序补发缓冲批次。
func (a *Agent) flushMetrics() {
	if a.metricsQueue == nil {
		return
	}
	a.metricsQueue.flush()
}

func (a *Agent) nextMetricsSequence() uint64 {
	return a.metricsSeq.Add(1)
}

func splitMetricsBatches(samples []protocol.NodeMetricSample, max int) [][]protocol.NodeMetricSample {
	if len(samples) == 0 {
		return nil
	}
	if max <= 0 {
		max = metricsMaxBatchSamples
	}
	batches := make([][]protocol.NodeMetricSample, 0, (len(samples)+max-1)/max)
	for len(samples) > 0 {
		n := max
		if len(samples) < n {
			n = len(samples)
		}
		// Container samples and their completeness must succeed in the same transaction.
		if n < len(samples) {
			for i := 0; i < n; i++ {
				if strings.HasPrefix(samples[i].Metric, "container.") && len(samples)-i <= max {
					if i > 0 {
						n = i
					}
					break
				}
			}
		}
		// Keep completeness observations atomic when a split falls inside the triple.
		if max >= 3 && n < len(samples) {
			for i := n - 1; i >= 0 && i >= n-2; i-- {
				if samples[i].Metric == "container.collection.discovered_count" && i+3 > n {
					n = i
					break
				}
			}
		}
		batches = append(batches, samples[:n])
		samples = samples[n:]
	}
	return batches
}
