package agent

import (
	"bytes"
	"context"
	"encoding/json"
	"log/slog"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/agent/connection"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
)

// syncBuffer 并发安全日志缓冲（buffer 断言先例：hubserver/single_port_test.go）。
type syncBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (s *syncBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *syncBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.String()
}

func (s *syncBuffer) countLines(substr string) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	return strings.Count(s.b.String(), substr)
}

// newLogTestAgent 构造注入 buffer logger 的 Agent。
func newLogTestAgent(t *testing.T, buf *syncBuffer) *Agent {
	t.Helper()
	a, err := New(Config{
		HubURL:            "ws://127.0.0.1:8443",
		RegistrationToken: "test-token",
		NodeName:          "log-test-node",
		DataDir:           t.TempDir(),
	}, slog.New(slog.NewTextHandler(buf, nil)))
	if err != nil {
		t.Fatalf("new agent: %v", err)
	}
	t.Cleanup(func() { a.Close() })
	return a
}

func helloAckEnvelope(t *testing.T, p protocol.HelloAckPayload) protocol.Envelope {
	t.Helper()
	raw, err := json.Marshal(p)
	if err != nil {
		t.Fatal(err)
	}
	return protocol.Envelope{Type: protocol.MsgHelloAck, Payload: raw}
}

// TestHelloRejectedThrottledSharedWindow hello rejected 经共享状态机节流：
// 同消息连发只出 1 行；随后的 connection lost 打点共享同一分钟硬顶（双行叠加
// 合计 1 行，而非各自节流 2 行/分钟）。
func TestHelloRejectedThrottledSharedWindow(t *testing.T) {
	// 分钟边界防护：跨分钟的异文本变化会合法放行，跳过该相位避免误报
	if time.Now().Unix()%60 >= 57 {
		t.Skip("too close to minute boundary")
	}
	buf := &syncBuffer{}
	a := newLogTestAgent(t, buf)

	reject := helloAckEnvelope(t, protocol.HelloAckPayload{Accepted: false, Message: "invalid credential"})
	a.OnHelloAck(reject)
	a.OnHelloAck(reject)
	a.OnHelloAck(reject)
	if got := buf.countLines("hello rejected"); got != 1 {
		t.Fatalf("repeated rejected ack must be throttled to 1 line, got %d\n%s", got, buf.String())
	}
	// 模拟 manager 紧随的 connection lost 打点（共享同一状态机 → 同分钟硬顶压制）
	a.recordConnectionFailure("connection lost", "failed to get reader: connection reset")
	if got := buf.countLines("connection lost"); got != 0 {
		t.Fatalf("connection lost must share the rejected window (suppressed within minute), got %d line(s)\n%s", got, buf.String())
	}
	if got := buf.countLines("hello rejected"); got != 1 {
		t.Fatalf("total failure lines must stay 1 per minute, got %d", got)
	}
}

// TestBadHelloAckThrottled bad hello ack（ack 解析失败）走同一失败窗口。
func TestBadHelloAckThrottled(t *testing.T) {
	buf := &syncBuffer{}
	a := newLogTestAgent(t, buf)
	bad := protocol.Envelope{Type: protocol.MsgHelloAck, Payload: []byte("{not-json")}
	a.OnHelloAck(bad)
	a.OnHelloAck(bad)
	if got := buf.countLines("bad hello ack"); got != 1 {
		t.Fatalf("bad hello ack must be throttled to 1 line, got %d\n%s", got, buf.String())
	}
}

// TestConnectedInfoLogAfterFailure accepted 打 1 行 INFO（含失败摘要）并重置失败
// 窗口；再入失败时 failStart 重建、计数从 1 起。
func TestConnectedInfoLogAfterFailure(t *testing.T) {
	buf := &syncBuffer{}
	a := newLogTestAgent(t, buf)

	// 制造失败（首错 1 行 ERROR）
	a.recordConnectionFailure("hello rejected", "invalid credential")
	if got := buf.countLines("hello rejected"); got != 1 {
		t.Fatalf("expected 1 failure line, got %d", got)
	}
	// accepted → 1 行 INFO 恢复摘要
	a.OnHelloAck(helloAckEnvelope(t, protocol.HelloAckPayload{
		Accepted: true, NodeID: "node-1", AgentID: "agent-1",
	}))
	if got := buf.countLines("agent connected"); got != 1 {
		t.Fatalf("expected 1 connected INFO line, got %d\n%s", got, buf.String())
	}
	out := buf.String()
	if !strings.Contains(out, "recovered=true") || !strings.Contains(out, "failures=1") {
		t.Fatalf("connected line must carry failure summary: %s", out)
	}
	// 成功→失败再入：failStart 重建、计数从 1
	snap, ok := a.logState.RecordFailure("dial tcp refused", time.Now())
	if !ok {
		t.Fatal("first failure after recovery must log")
	}
	if snap.FailuresTotal != 1 || snap.FailDuration != 0 {
		t.Fatalf("failure window must be rebuilt after recovery, got %+v", snap)
	}
}

// TestStatusLogLoopCadence statusLogInterval 缩到 30ms 驱动真实状态循环：
// 未 accepted 不打印；accepted 连接存活时打印状态行（含计数与 revision）；
// 进入失败态（断开）后不再打印。循环常驻、随 ctx 退出。
func TestStatusLogLoopCadence(t *testing.T) {
	old := connection.StatusLogInterval
	connection.StatusLogInterval = 30 * time.Millisecond
	t.Cleanup(func() { connection.StatusLogInterval = old })

	buf := &syncBuffer{}
	a := newLogTestAgent(t, buf)

	loopCtx, cancelLoop := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		a.statusLogLoop(loopCtx)
	}()
	t.Cleanup(func() { cancelLoop(); <-done })

	// 未 accepted：多个 tick 周期内不得打印
	time.Sleep(120 * time.Millisecond)
	if got := buf.countLines("agent status"); got != 0 {
		t.Fatalf("status must not print before accepted, got %d line(s)\n%s", got, buf.String())
	}

	// accepted 存活：计数注入后按时打印
	a.logState.IncHeartbeatSent()
	a.logState.IncHeartbeatSent()
	a.logState.IncInventoryReports()
	a.logState.IncRevisionChecks()
	a.logState.RecordSuccess(time.Now())
	deadline := time.Now().Add(2 * time.Second)
	for buf.countLines("agent status") == 0 && time.Now().Before(deadline) {
		time.Sleep(10 * time.Millisecond)
	}
	if got := buf.countLines("agent status"); got == 0 {
		t.Fatalf("status must print while accepted, got 0\n%s", buf.String())
	}
	out := buf.String()
	if !strings.Contains(out, "heartbeats=2") || !strings.Contains(out, "inventory_reports=1") ||
		!strings.Contains(out, "revision_checks=1") || !strings.Contains(out, "uptime=") {
		t.Fatalf("status line missing counters: %s", out)
	}

	// 断开（进入失败态）→ 不再打印。
	// 先置失败态并静默一个 settle 窗口，让“置失败前已取到状态”的在途打印落盘，
	// 再取快照基线，随后断言行数不再增长。
	a.logState.RecordFailure("connection lost", time.Now())
	time.Sleep(60 * time.Millisecond)
	before := buf.countLines("agent status")
	time.Sleep(120 * time.Millisecond)
	if got := buf.countLines("agent status"); got != before {
		t.Fatalf("status must not print after failure entered: before=%d after=%d", before, got)
	}
}
