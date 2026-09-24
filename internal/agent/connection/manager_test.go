package connection

import (
	"bytes"
	"context"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/coder/websocket"
)

// syncBuffer 并发安全日志缓冲（仿 hubserver/single_port_test 的 buffer 断言先例）。
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

func discardLogger(buf *syncBuffer) *slog.Logger {
	return slog.New(slog.NewTextHandler(buf, nil))
}

// waitDone 等待 Run 返回，超时即失败。
func waitDone(t *testing.T, done <-chan struct{}) {
	t.Helper()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("Manager.Run did not return")
	}
}

// TestRunThrottlesConnectionLostLog 关闭端点驱动重连循环数个失败周期：
// 错误文本稳定时窗口内失败日志恰 1 行（首错），而非每周期 1 行。
func TestRunThrottlesConnectionLostLog(t *testing.T) {
	buf := &syncBuffer{}
	m := New("ws://127.0.0.1:1/closed", "", "", nil, discardLogger(buf), nil)
	m.delayFn = func() time.Duration { return time.Millisecond }
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		m.Run(ctx, func() *protocol.HelloPayload { return &protocol.HelloPayload{} })
	}()
	// 数百个失败周期（dial 快速失败 + 1ms 退避）
	time.Sleep(300 * time.Millisecond)
	cancel()
	waitDone(t, done)

	if got := strings.Count(buf.String(), "connection lost"); got != 1 {
		t.Fatalf("expected exactly 1 throttled failure line over hundreds of cycles, got %d\n%s", got, buf.String())
	}
	if !m.logState.underFailure() {
		t.Fatal("state machine should be in failure state after real failures")
	}
}

// TestRunCtxCancelKeepsQuiet 补充边界 1a：ctx 取消触发的返回不得进入失败态、
// 不打任何失败日志行。
func TestRunCtxCancelKeepsQuiet(t *testing.T) {
	// 黑洞端点：TCP 握手成功但不完成 HTTP upgrade，Dial 阻塞至 ctx 取消
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer ln.Close()

	buf := &syncBuffer{}
	m := New("ws://"+ln.Addr().String(), "", "", nil, discardLogger(buf), nil)
	m.delayFn = func() time.Duration { return time.Millisecond }
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		m.Run(ctx, func() *protocol.HelloPayload { return &protocol.HelloPayload{} })
	}()
	// 等 Dial 进行中再取消
	time.Sleep(50 * time.Millisecond)
	cancel()
	waitDone(t, done)

	if got := buf.String(); strings.Contains(got, "connection lost") {
		t.Fatalf("ctx cancellation must not log connection failures, got:\n%s", got)
	}
	if m.logState.underFailure() {
		t.Fatal("ctx cancellation must not enter failure state")
	}
}

// TestRunStopKeepsQuiet 补充边界 1b：真实连接建立后 Stop 触发的返回
// 不得进入失败态、不打任何失败日志行。
func TestRunStopKeepsQuiet(t *testing.T) {
	peer := make(chan struct{}, 1)
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		// 立即关闭底层连接，避免 client Close 等待 close handshake 挂满 5s
		defer c.CloseNow()
		peer <- struct{}{}
		readCtx, cancel := context.WithCancel(context.Background())
		defer cancel()
		for {
			if _, _, err := c.Read(readCtx); err != nil {
				return
			}
		}
	}))
	defer ts.Close()

	buf := &syncBuffer{}
	m := New("ws://"+ts.Listener.Addr().String(), "", "", nil, discardLogger(buf), nil)
	m.delayFn = func() time.Duration { return time.Millisecond }
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() {
		defer close(done)
		m.Run(ctx, func() *protocol.HelloPayload { return &protocol.HelloPayload{} })
	}()

	select {
	case <-peer:
	case <-time.After(3 * time.Second):
		t.Fatal("manager did not connect")
	}
	// 等 manager 侧连接完全就位
	deadline := time.Now().Add(2 * time.Second)
	for !m.IsConnected() && time.Now().Before(deadline) {
		time.Sleep(5 * time.Millisecond)
	}
	if !m.IsConnected() {
		t.Fatal("manager should be connected before Stop")
	}

	m.Stop()
	waitDone(t, done)

	if got := buf.String(); strings.Contains(got, "connection lost") {
		t.Fatalf("Stop must not log connection failures, got:\n%s", got)
	}
	if m.logState.underFailure() {
		t.Fatal("Stop must not enter failure state")
	}
}

// TestReconnectDelayDefaultUnchanged 默认退避行为不变：2s + 0~2s jitter。
func TestReconnectDelayDefaultUnchanged(t *testing.T) {
	m := New("ws://127.0.0.1:1", "", "", nil, discardLogger(&syncBuffer{}), nil)
	for i := 0; i < 10; i++ {
		d := m.reconnectDelay()
		if d < 2*time.Second || d >= 4*time.Second {
			t.Fatalf("default delay out of range: %v", d)
		}
	}
	m.delayFn = func() time.Duration { return 5 * time.Millisecond }
	if d := m.reconnectDelay(); d != 5*time.Millisecond {
		t.Fatalf("injected delay not used, got %v", d)
	}
}
