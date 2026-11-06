package agent

import (
	"context"
	"encoding/json"
	"errors"
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
	defaultMetricsBufferBatch = 20
	maxMetricsBufferBatches   = 60
	// metricsMaxBufferBytes 是补报队列的硬字节上限（序列化 payload 计费）。
	metricsMaxBufferBytes = 4 << 20
	// metricsAckTimeout 是等待 Hub ACK 的上限；超时后按最旧优先重发。
	metricsAckTimeout = 10 * time.Second
	// metricsWriteTimeout 限制单批 WebSocket 写等待，半开连接不会卡住采集循环。
	metricsWriteTimeout = 5 * time.Second
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

// normalizeMetricsBufferBatches 限制补报批次数：默认 20，最大 60。
func normalizeMetricsBufferBatches(n int) int {
	if n <= 0 {
		return defaultMetricsBufferBatch
	}
	if n > maxMetricsBufferBatches {
		return maxMetricsBufferBatches
	}
	return n
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

// metricsBatch 是队列中的一批指标。envelope 在入队时序列化一次，字节计数
// 与发送使用同一份数据，避免重复复制 payload。
type metricsBatch struct {
	payload    protocol.NodeMetricsPayload
	envelope   protocol.Envelope
	bytes      int
	enqueuedAt time.Time
	sentAt     time.Time
}

var (
	// errMetricsOffline 表示连接尚未完成 HELLO_ACK，批次保留在内存队列。
	errMetricsOffline = errors.New("agent metrics connection not ready")
)

// metricsQueue 是有界的纯内存批次队列。
//
// 出队语义：
//   - Hub 声明 node_metrics_ack 时，只有收到匹配 sequence 的 ACK 才删除；
//     写成功但 ACK 超时/丢失的批次保留并按最旧优先重发。
//   - 旧 Hub 未声明该能力时，WebSocket 写成功即删除，保持兼容。
//
// 淘汰语义：批次数或字节数任一超限即淘汰最旧批次，并累计丢弃计数。
type metricsQueue struct {
	mu          sync.Mutex
	batches     []*metricsBatch
	bytes       int
	maxBatches  int
	send        func(context.Context, protocol.Envelope) error
	ackMode     bool
	flushing    bool
	dropped     uint64
	droppedSamp uint64
	ackTimeouts uint64
}

func newMetricsQueue(send func(context.Context, protocol.Envelope) error, maxBatches int) *metricsQueue {
	return &metricsQueue{
		send:       send,
		maxBatches: normalizeMetricsBufferBatches(maxBatches),
	}
}

func metricsEnvelopeBytes(env protocol.Envelope) int {
	return len(env.Payload) + len(env.Type) + len(env.ID) + 96
}

// enqueue 入队并尝试发送；返回本次因上限丢弃的批次数与样本数。
func (q *metricsQueue) enqueue(payload protocol.NodeMetricsPayload) (int, int) {
	env := protocol.NewEnvelope(protocol.MsgNodeMetrics, "", payload)
	batch := &metricsBatch{
		payload:    payload,
		envelope:   env,
		bytes:      metricsEnvelopeBytes(env),
		enqueuedAt: time.Now(),
	}

	q.mu.Lock()
	// 单批超过硬上限时直接丢弃，绝不突破 4MiB。
	if batch.bytes > metricsMaxBufferBytes {
		q.dropped++
		q.droppedSamp += uint64(len(payload.Samples))
		q.mu.Unlock()
		return 1, len(payload.Samples)
	}
	q.batches = append(q.batches, batch)
	q.bytes += batch.bytes
	dropped, droppedSamples := q.evictLocked()
	q.mu.Unlock()

	q.flush(context.Background())
	return dropped, droppedSamples
}

// evictLocked 按“批次数或字节数先到”淘汰最旧批次。
func (q *metricsQueue) evictLocked() (int, int) {
	dropped, samples := 0, 0
	for len(q.batches) > q.maxBatches || q.bytes > metricsMaxBufferBytes {
		oldest := q.batches[0]
		q.batches[0] = nil
		q.batches = q.batches[1:]
		q.bytes -= oldest.bytes
		q.dropped++
		q.droppedSamp += uint64(len(oldest.payload.Samples))
		dropped++
		samples += len(oldest.payload.Samples)
	}
	if len(q.batches) == 0 {
		q.batches = nil
		q.bytes = 0
	}
	return dropped, samples
}

// flush 按最旧优先发送尚未在途的批次；遇到写失败立即停止并保留批次。
// 返回本次成功写出的批次数（ACK 模式下不等同于已确认数）。
func (q *metricsQueue) flush(ctx context.Context) int {
	q.mu.Lock()
	if q.flushing || len(q.batches) == 0 {
		q.mu.Unlock()
		return 0
	}
	q.flushing = true
	q.mu.Unlock()
	defer func() {
		q.mu.Lock()
		q.flushing = false
		q.mu.Unlock()
	}()

	sent := 0
	for {
		q.mu.Lock()
		if len(q.batches) == 0 {
			q.mu.Unlock()
			return sent
		}
		var (
			batch *metricsBatch
			env   protocol.Envelope
			now   = time.Now()
			ack   = q.ackMode
			send  = q.send
		)
		for _, candidate := range q.batches {
			if ack && !candidate.sentAt.IsZero() {
				if now.Sub(candidate.sentAt) < metricsAckTimeout {
					continue
				}
				q.ackTimeouts++
				candidate.sentAt = time.Time{}
			}
			batch = candidate
			env = candidate.envelope
			break
		}
		if batch == nil {
			q.mu.Unlock()
			return sent
		}
		q.mu.Unlock()

		if send == nil {
			return sent
		}
		writeCtx, cancel := context.WithTimeout(ctx, metricsWriteTimeout)
		err := send(writeCtx, env)
		cancel()
		if err != nil {
			return sent
		}

		q.mu.Lock()
		// ACK 可能已在写成功回调前后到达；按 sequence 重新定位。
		index := q.indexLocked(batch.payload.Sequence)
		if index < 0 {
			q.mu.Unlock()
			continue
		}
		if q.ackMode {
			q.batches[index].sentAt = time.Now()
		} else {
			q.removeLocked(index)
		}
		q.mu.Unlock()
		sent++
	}
}

func (q *metricsQueue) indexLocked(sequence uint64) int {
	for i, batch := range q.batches {
		if batch.payload.Sequence == sequence {
			return i
		}
	}
	return -1
}

func (q *metricsQueue) removeLocked(index int) {
	batch := q.batches[index]
	q.bytes -= batch.bytes
	copy(q.batches[index:], q.batches[index+1:])
	q.batches[len(q.batches)-1] = nil
	q.batches = q.batches[:len(q.batches)-1]
	if len(q.batches) == 0 {
		q.batches = nil
		q.bytes = 0
	}
}

// ack 处理 Hub 确认：ok=true 时才删除；ok=false 保留并允许重发。
func (q *metricsQueue) ack(sequence uint64, ok bool) {
	q.mu.Lock()
	defer q.mu.Unlock()
	index := q.indexLocked(sequence)
	if index < 0 {
		return
	}
	if !ok {
		q.batches[index].sentAt = time.Time{}
		return
	}
	q.removeLocked(index)
}

// setAckMode 根据 Hub HELLO_ACK 能力切换出队语义。
func (q *metricsQueue) setAckMode(enabled bool) {
	q.mu.Lock()
	q.ackMode = enabled
	if enabled {
		// 新会话不应等待旧连接上的 ACK 超时：立即允许重发。重复由 Hub 幂等消化。
		for _, batch := range q.batches {
			batch.sentAt = time.Time{}
		}
	}
	q.mu.Unlock()
}

func (q *metricsQueue) pending() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return len(q.batches)
}

func (q *metricsQueue) pendingBytes() int {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.bytes
}

func (q *metricsQueue) oldestPendingAge(now time.Time) time.Duration {
	q.mu.Lock()
	defer q.mu.Unlock()
	if len(q.batches) == 0 {
		return 0
	}
	return now.Sub(q.batches[0].enqueuedAt)
}

func (q *metricsQueue) droppedCounts() (uint64, uint64) {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.dropped, q.droppedSamp
}

func (q *metricsQueue) ackTimeoutCount() uint64 {
	q.mu.Lock()
	defer q.mu.Unlock()
	return q.ackTimeouts
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
		dropped, droppedSamples := a.metricsQueue.enqueue(payload)
		if dropped > 0 && a.logger != nil {
			a.logger.Warn("node metrics buffer overflow; dropped oldest batches",
				"dropped_batches", dropped,
				"dropped_samples", droppedSamples,
				"capacity_batches", a.metricsQueue.maxBatches,
				"capacity_bytes", metricsMaxBufferBytes)
		}
	}
	if pending := a.metricsQueue.pending(); pending > 0 && a.logger != nil {
		if a.conn == nil || !a.conn.HandshakeComplete() {
			if time.Since(a.metricsStatsAt) >= time.Minute {
				a.metricsStatsAt = time.Now()
				dropped, droppedSamples := a.metricsQueue.droppedCounts()
				a.logger.Info("node metrics buffer pending while offline",
					"batches", pending,
					"bytes", a.metricsQueue.pendingBytes(),
					"oldest_pending", a.metricsQueue.oldestPendingAge(time.Now()).Round(time.Second).String(),
					"dropped_batches", dropped,
					"dropped_samples", droppedSamples,
					"ack_timeouts", a.metricsQueue.ackTimeoutCount())
			}
		} else {
			a.logger.Debug("node metrics send deferred", "pending", pending)
		}
	}
}

// flushMetrics 在连接恢复后按序补发缓冲批次。
func (a *Agent) flushMetrics() {
	if a.metricsQueue == nil || a.conn == nil || !a.conn.HandshakeComplete() {
		return
	}
	a.metricsQueue.flush(a.ctx)
}

func (a *Agent) nextMetricsSequence() uint64 {
	return a.metricsSeq.Add(1)
}

// OnNodeMetricsAck 处理 Hub 的批次确认。
func (a *Agent) OnNodeMetricsAck(env protocol.Envelope) {
	if a.metricsQueue == nil {
		return
	}
	var p protocol.NodeMetricsAckPayload
	if err := json.Unmarshal(env.Payload, &p); err != nil {
		return
	}
	a.metricsQueue.ack(p.Sequence, p.OK)
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
