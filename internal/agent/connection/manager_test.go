package connection

import (
	"bytes"
	"context"
	"encoding/json"
	"encoding/pem"
	"log/slog"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
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

// helloAckServer 起一个 WS 端点：读取 HELLO 后按测试放行的 accepted 值回 HELLO_ACK，
// 直到客户端断开。返回地址与放行通道（nil 表示不发 ACK）。
func helloAckServer(t *testing.T, ack chan bool) string {
	t.Helper()
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer c.CloseNow()
		// Accept 会 hijack 连接，请求上下文随之取消；读写用独立上下文。
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		if _, _, err := c.Read(ctx); err != nil { // HELLO
			return
		}
		if ack != nil {
			accepted, ok := <-ack
			if !ok {
				return
			}
			env, err := json.Marshal(protocol.NewEnvelope(protocol.MsgHelloAck, "", protocol.HelloAckPayload{Accepted: accepted}))
			if err != nil {
				return
			}
			if err := c.Write(ctx, websocket.MessageText, env); err != nil {
				return
			}
		}
		for {
			if _, _, err := c.Read(ctx); err != nil {
				return
			}
		}
	}))
	t.Cleanup(ts.Close)
	return ts.Listener.Addr().String()
}

// waitFor 轮询等待条件成立。
func waitFor(t *testing.T, what string, cond func() bool) {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("timed out waiting for %s", what)
}

// TestHandshakeFlagOnlyAfterAcceptedAck 握手标志的置真条件与窗口：
// Dial 成功（IsConnected=true）后 HELLO_ACK 未到或 rejected 时必须保持 false，
// 仅 accepted 置真（缺陷 OBS-20261016-G23-RECONNECT-WINDOW 的门禁依据）。
func TestHandshakeFlagOnlyAfterAcceptedAck(t *testing.T) {
	cases := []struct {
		name      string
		ack       *bool // nil = 不回 HELLO_ACK（重连窗口）
		wantFinal bool
	}{
		{name: "no ack stays incomplete", ack: nil, wantFinal: false},
		{name: "rejected stays incomplete", ack: boolPtr(false), wantFinal: false},
		{name: "accepted completes", ack: boolPtr(true), wantFinal: true},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			var acks chan bool
			if tc.ack != nil {
				acks = make(chan bool, 1)
				acks <- *tc.ack
			}
			addr := helloAckServer(t, acks)
			m := New("ws://"+addr, "", "", nil, discardLogger(&syncBuffer{}), nil)
			m.delayFn = func() time.Duration { return time.Millisecond }
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			done := make(chan struct{})
			go func() {
				defer close(done)
				m.Run(ctx, func() *protocol.HelloPayload { return &protocol.HelloPayload{} })
			}()
			defer func() {
				m.Stop()
				waitDone(t, done)
			}()

			waitFor(t, "transport connected", m.IsConnected)
			if tc.ack == nil {
				// 窗口：传输已连但 HELLO_ACK 未到，握手标志必须仍为 false。
				// （有 ACK 的用例 ACK 可能先于本断言到达，只断言最终态。）
				if m.HandshakeComplete() {
					t.Fatal("handshake must stay false in the reconnect window")
				}
			}
			if tc.wantFinal {
				waitFor(t, "handshake complete after accepted ACK", m.HandshakeComplete)
			} else {
				time.Sleep(100 * time.Millisecond)
				if m.HandshakeComplete() {
					t.Fatal("handshake must stay false without accepted HELLO_ACK")
				}
			}
		})
	}
}

func boolPtr(v bool) *bool { return &v }

// TestHandshakeResetsOnReconnect 断开后握手标志复位：
// 旧连接的 accepted 状态不得跨重连窗口生效。
func TestHandshakeResetsOnReconnect(t *testing.T) {
	closeCh := make(chan struct{})
	ts := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c, err := websocket.Accept(w, r, nil)
		if err != nil {
			return
		}
		defer c.CloseNow()
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		if _, _, err := c.Read(ctx); err != nil {
			return
		}
		env, _ := json.Marshal(protocol.NewEnvelope(protocol.MsgHelloAck, "", protocol.HelloAckPayload{Accepted: true}))
		if err := c.Write(ctx, websocket.MessageText, env); err != nil {
			return
		}
		<-closeCh // 由测试决定断开
	}))
	defer ts.Close()

	m := New("ws://"+ts.Listener.Addr().String(), "", "", nil, discardLogger(&syncBuffer{}), nil)
	m.delayFn = func() time.Duration { return time.Hour } // 不重连，便于观察复位
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan struct{})
	go func() {
		defer close(done)
		m.Run(ctx, func() *protocol.HelloPayload { return &protocol.HelloPayload{} })
	}()
	defer func() {
		m.Stop()
		waitDone(t, done)
	}()

	waitFor(t, "handshake complete", m.HandshakeComplete)
	close(closeCh)
	waitFor(t, "handshake reset after disconnect", func() bool { return !m.HandshakeComplete() })
	if m.IsConnected() {
		t.Fatal("transport must be disconnected after peer close")
	}
}

// TestHTTPClientHasDownloadTimeouts Agent 下载客户端必须带总时限与响应头超时：
// 没有超时时「服务端发头后不给 body」会永久阻塞（缺陷
// OBS-20261014-ARTIFACT-DOWNLOAD-NO-READ-TIMEOUT）；未配置 TLS CA 的兜底
// 客户端与 SetTLSCA 之后的客户端都必须具备。
func TestHTTPClientHasDownloadTimeouts(t *testing.T) {
	check := func(name string, c *http.Client) {
		t.Helper()
		if c.Timeout != defaultHTTPTimeout {
			t.Fatalf("%s: client timeout = %v, want %v", name, c.Timeout, defaultHTTPTimeout)
		}
		tr, ok := c.Transport.(*http.Transport)
		if !ok {
			t.Fatalf("%s: transport type %T, want *http.Transport", name, c.Transport)
		}
		if tr.ResponseHeaderTimeout != defaultHTTPResponseHeaderTimeout {
			t.Fatalf("%s: ResponseHeaderTimeout = %v, want %v", name, tr.ResponseHeaderTimeout, defaultHTTPResponseHeaderTimeout)
		}
	}

	m := New("ws://127.0.0.1:1", "", "", nil, discardLogger(&syncBuffer{}), nil)
	check("fallback", m.HTTPClient())

	// SetTLSCA 成功路径：用测试服务器自签证书作 CA，验证客户端仍带超时。
	ts := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {}))
	defer ts.Close()
	caFile := filepath.Join(t.TempDir(), "ca.pem")
	if err := os.WriteFile(caFile, pem.EncodeToMemory(&pem.Block{Type: "CERTIFICATE", Bytes: ts.Certificate().Raw}), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := m.SetTLSCA(caFile); err != nil {
		t.Fatalf("set tls ca: %v", err)
	}
	check("tls", m.HTTPClient())
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
