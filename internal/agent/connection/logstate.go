package connection

import (
	"sync"
	"sync/atomic"
	"time"
)

// 连接日志节奏参数（var：同包测试可临时覆盖；StatusLogInterval 导出供 agent 状态
// 循环 ticker 与跨包测试同源引用）。
var (
	// failLogInterval 失败 0–60 分钟内的最小输出间隔：每分钟最多 1 行。
	failLogInterval = time.Minute
	// failLogWindow 失败持续超过该时长后转为小时级节流。
	failLogWindow = 60 * time.Minute
	// StatusLogInterval 成功期每小时状态行；失败超窗（>failLogWindow）后的节流间隔。
	StatusLogInterval = time.Hour
)

// FailureSnapshot 失败打点放行时的输出快照。
type FailureSnapshot struct {
	Error              string
	FailDuration       time.Duration
	FailuresThisMinute int
	FailuresTotal      int
}

// FailureSummary accepted 恢复成功时带回的失败期摘要。
type FailureSummary struct {
	Recovered bool
	Duration  time.Duration
	Failures  int
}

// StatusSnapshot 成功期每小时状态行数据。
type StatusSnapshot struct {
	Uptime           time.Duration
	Heartbeats       uint64
	InventoryReports uint64
	RevisionChecks   uint64
}

// ConnectionLogState 连接日志节奏状态机。
//
// 失败打点发生在 manager 重连 goroutine（connection lost）与 read loop 的
// OnHelloAck（hello rejected / bad hello ack），成功打点发生在 OnHelloAck accepted
// 分支——跨 goroutine 访问必须持 mu；三个计数器用独立 atomic。
// 所有方法 nil-receiver 安全：手工构造的 Agent 未注入时挂点自动降级为不打点。
type ConnectionLogState struct {
	mu sync.Mutex
	// 失败窗口（failing 为 true 时 failStart/lastFailLog 非零）
	failing       bool
	failStart     time.Time
	lastErr       string
	lastFailLog   time.Time
	failuresTotal int
	windowStart   time.Time
	windowCount   int
	// 成功态：connectedAt 由 RecordSuccess 写入、失败态进入时清零（accepted 为准绳）
	connectedAt   time.Time
	lastStatusLog time.Time
	// 计数器：进程累计，不随成功/失败切换重置
	heartbeatSent    atomic.Uint64
	inventoryReports atomic.Uint64
	revisionChecks   atomic.Uint64
}

// NewConnectionLogState 创建状态机。
func NewConnectionLogState() *ConnectionLogState { return &ConnectionLogState{} }

// RecordFailure 记录一次失败，返回输出快照与是否放行。
//
// 放行规则（按优先）：
//  1. 首失败（failStart 从无到有）无条件立即放行，不吞首错；
//  2. 距上次放行 >= 当前节流间隔放行：失败 0–60 分钟每分钟最多 1 行，
//     超过 60 分钟（now-failStart > failLogWindow）改为每 StatusLogInterval 最多 1 行；
//  3. 错误文本变化且已跨出上一行所在时钟分钟、且仍处分钟级节流段 → 立即放行
//     （关键转折不被合并）；同一时钟分钟内的变化被分钟硬顶压制——
//     hello rejected 与 connection lost 双行叠加汇入本状态机后合计每分钟最多 1 行。
func (s *ConnectionLogState) RecordFailure(errText string, now time.Time) (FailureSnapshot, bool) {
	if s == nil {
		return FailureSnapshot{Error: errText}, false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	s.failuresTotal++
	if !s.failing {
		// 首失败：无条件立即放行，重建失败窗口，清除 accepted 态
		s.failing = true
		s.failStart = now
		s.connectedAt = time.Time{}
		s.lastErr = errText
		s.lastFailLog = now
		s.windowStart = now
		s.windowCount = 1
		return FailureSnapshot{Error: errText, FailuresThisMinute: 1, FailuresTotal: s.failuresTotal}, true
	}
	// 分钟计数窗口（滚动）：本分钟合并失败数
	if now.Sub(s.windowStart) >= failLogInterval {
		s.windowStart = now
		s.windowCount = 0
	}
	s.windowCount++
	interval := failLogInterval
	if now.Sub(s.failStart) > failLogWindow {
		interval = StatusLogInterval
	}
	snap := FailureSnapshot{
		Error:              errText,
		FailDuration:       now.Sub(s.failStart),
		FailuresThisMinute: s.windowCount,
		FailuresTotal:      s.failuresTotal,
	}
	due := now.Sub(s.lastFailLog) >= interval
	errChanged := errText != s.lastErr &&
		interval == failLogInterval &&
		now.Unix()/60 != s.lastFailLog.Unix()/60
	s.lastErr = errText
	if due || errChanged {
		s.lastFailLog = now
		return snap, true
	}
	return snap, false
}

// RecordSuccess accepted HELLO 打点：重置失败窗口与计数并返回失败摘要。
// 首次成功返回全零摘要（Recovered=false）；恢复成功摘要含失败时长与累计次数。
func (s *ConnectionLogState) RecordSuccess(now time.Time) FailureSummary {
	if s == nil {
		return FailureSummary{}
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	summary := FailureSummary{}
	if s.failing {
		summary.Recovered = true
		summary.Duration = now.Sub(s.failStart)
		summary.Failures = s.failuresTotal
	}
	s.failing = false
	s.failStart = time.Time{}
	s.lastErr = ""
	s.lastFailLog = time.Time{}
	s.failuresTotal = 0
	s.windowStart = time.Time{}
	s.windowCount = 0
	s.connectedAt = now
	s.lastStatusLog = now
	return summary
}

// TakeStatus 仅 accepted 连接存活（connectedAt 非零）且到达每小时节奏时返回状态快照。
// 失败态进入即清零 connectedAt，断开期间恒返回 false。
func (s *ConnectionLogState) TakeStatus(now time.Time) (StatusSnapshot, bool) {
	if s == nil {
		return StatusSnapshot{}, false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.connectedAt.IsZero() || now.Sub(s.lastStatusLog) < StatusLogInterval {
		return StatusSnapshot{}, false
	}
	s.lastStatusLog = now
	return StatusSnapshot{
		Uptime:           now.Sub(s.connectedAt),
		Heartbeats:       s.heartbeatSent.Load(),
		InventoryReports: s.inventoryReports.Load(),
		RevisionChecks:   s.revisionChecks.Load(),
	}, true
}

// IncHeartbeatSent 记一次心跳发送（nil 安全）。
func (s *ConnectionLogState) IncHeartbeatSent() {
	if s != nil {
		s.heartbeatSent.Add(1)
	}
}

// IncInventoryReports 记一次 inventory 上报（nil 安全）。
func (s *ConnectionLogState) IncInventoryReports() {
	if s != nil {
		s.inventoryReports.Add(1)
	}
}

// IncRevisionChecks 记一次 revision 检查（nil 安全）。
func (s *ConnectionLogState) IncRevisionChecks() {
	if s != nil {
		s.revisionChecks.Add(1)
	}
}

// underFailure 测试辅助：状态机当前是否处于失败态。
func (s *ConnectionLogState) underFailure() bool {
	if s == nil {
		return false
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.failing
}
