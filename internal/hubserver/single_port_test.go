package hubserver

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

// singlePortConfig 返回 WebAddr == GatewayAddr 的单端口模式配置（零新配置项触发）。
func singlePortConfig(dir string) Config {
	return Config{
		WebAddr:           "127.0.0.1:0",
		GatewayAddr:       "127.0.0.1:0",
		RegistrationToken: "test-token",
		DataDir:           dir,
		ArtifactDir:       dir + "/artifacts",
		BaseURL:           "http://127.0.0.1:18080",
		HeartbeatTimeout:  30 * time.Second,
		AdminUsername:     "admin",
		AdminPassword:     "admin123",
		SessionTTL:        time.Hour,
		RevisionCheckSec:  5,
		ChangelogWindow:   5000,
	}
}

// TestSinglePortGatewayMode 单端口模式：httptest 经 ServeMux 拿到的 handler 必须
// 与真实启动路径同一层分流包装 —— WS 握手注册心跳、/agent/transfers/* 分流、
// REST/healthz 全部经同一端口。
func TestSinglePortGatewayMode(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	cfg := singlePortConfig(dir)
	h, err := New(cfg, logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })
	if !h.singlePort {
		t.Fatal("GatewayAddr == WebAddr must select single-port mode")
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()

	// ① 同一端口：WS 握手（nodesteer 子协议）→ HELLO 注册 → 心跳
	wsURL := "ws" + strings.TrimPrefix(ts.URL, "http")
	conn, resp, err := websocket.Dial(ctx, wsURL, &websocket.DialOptions{
		Subprotocols: []string{"nodesteer"},
	})
	if err != nil {
		t.Fatalf("ws dial via single port: %v", err)
	}
	defer conn.Close(websocket.StatusNormalClosure, "done")
	if resp == nil {
		t.Fatal("expected handshake response")
	}
	if got := resp.Header.Get("Sec-WebSocket-Protocol"); got != "" && !strings.EqualFold(got, "nodesteer") {
		t.Fatalf("server echoed unexpected subprotocol %q", got)
	}

	if err := wsjson.Write(ctx, conn, protocol.NewEnvelope(protocol.MsgHello, "sp-1", protocol.HelloPayload{
		ProtocolVersion: protocol.ProtocolVersion,
		RegistrationKey: "test-token",
		Hostname:        "single-port-node",
	})); err != nil {
		t.Fatalf("write hello: %v", err)
	}
	var ack protocol.Envelope
	if err := wsjson.Read(ctx, conn, &ack); err != nil {
		t.Fatalf("read hello ack: %v", err)
	}
	if ack.Type != protocol.MsgHelloAck {
		t.Fatalf("expected HELLO_ACK, got %s", ack.Type)
	}
	var helloAck protocol.HelloAckPayload
	if err := json.Unmarshal(ack.Payload, &helloAck); err != nil {
		t.Fatalf("decode hello ack: %v", err)
	}
	if !helloAck.Accepted {
		t.Fatalf("hello rejected: %s", helloAck.Message)
	}
	if helloAck.NodeID == "" {
		t.Fatal("expected node id in ack")
	}

	if err := wsjson.Write(ctx, conn, protocol.NewEnvelope(protocol.MsgHeartbeat, "sp-2", protocol.HeartbeatPayload{
		NodeID: helloAck.NodeID,
	})); err != nil {
		t.Fatalf("write heartbeat: %v", err)
	}
	var hbAck protocol.Envelope
	if err := wsjson.Read(ctx, conn, &hbAck); err != nil {
		t.Fatalf("read heartbeat ack: %v", err)
	}
	if hbAck.Type != protocol.MsgHeartbeatAck {
		t.Fatalf("expected HEARTBEAT_ACK, got %s", hbAck.Type)
	}

	// ② 同一端口：/agent/transfers/* 被分流到 Gateway 的 transfer handler
	//    （特征：JSON 401 "agent authentication required"，而非 Web 的 plain 404）
	dl, err := http.Get(ts.URL + "/agent/transfers/nonexistent/download")
	if err != nil {
		t.Fatalf("transfer request: %v", err)
	}
	dlBody, _ := io.ReadAll(dl.Body)
	dl.Body.Close()
	if dl.StatusCode != http.StatusUnauthorized || !strings.Contains(string(dlBody), "agent authentication required") {
		t.Fatalf("/agent/transfers/* must be routed to transfer handler, got status=%d body=%s", dl.StatusCode, string(dlBody))
	}

	// 非法段路径同样由 transfer handler 响应（JSON "transfer endpoint not found"）
	trRoot, err := http.Get(ts.URL + "/agent/transfers/")
	if err != nil {
		t.Fatalf("transfer root request: %v", err)
	}
	trBody, _ := io.ReadAll(trRoot.Body)
	trRoot.Body.Close()
	if trRoot.StatusCode != http.StatusNotFound || !strings.Contains(string(trBody), "transfer endpoint not found") {
		t.Fatalf("transfer root must be routed to transfer handler, got status=%d body=%s", trRoot.StatusCode, string(trBody))
	}

	// ③ 同一端口：REST / healthz 照常走 Web 路由
	hz, err := http.Get(ts.URL + "/healthz")
	if err != nil {
		t.Fatalf("healthz: %v", err)
	}
	hz.Body.Close()
	if hz.StatusCode != http.StatusOK {
		t.Fatalf("healthz expected 200, got %d", hz.StatusCode)
	}
	stResp, err := http.Get(ts.URL + "/api/oidc/state")
	if err != nil {
		t.Fatalf("oidc state: %v", err)
	}
	var st struct {
		Enabled       bool `json:"enabled"`
		LocalFallback bool `json:"local_fallback"`
	}
	json.NewDecoder(stResp.Body).Decode(&st)
	stResp.Body.Close()
	if stResp.StatusCode != http.StatusOK || st.Enabled || !st.LocalFallback {
		t.Fatalf("oidc state via single port: status=%d enabled=%v localFallback=%v",
			stResp.StatusCode, st.Enabled, st.LocalFallback)
	}
}

// TestDualPortServeMuxHasNoGatewayMuxShunt 双端口模式回归：ServeMux 不含分流层，
// /agent/transfers/* 落回 Web 路由（plain 404），行为与既有实现一致。
func TestDualPortServeMuxHasNoGatewayMuxShunt(t *testing.T) {
	dir := t.TempDir()
	logger := slog.New(slog.NewTextHandler(os.Stderr, &slog.HandlerOptions{Level: slog.LevelError}))
	cfg := singlePortConfig(dir)
	cfg.GatewayAddr = "127.0.0.1:0" // 字面不同 => 双端口
	cfg.WebAddr = "localhost:0"
	h, err := New(cfg, logger)
	if err != nil {
		t.Fatalf("new hub: %v", err)
	}
	t.Cleanup(func() { h.Close() })
	if h.singlePort {
		t.Fatal("GatewayAddr != WebAddr must keep dual-port mode")
	}
	ts := httptest.NewServer(h.ServeMux())
	defer ts.Close()

	resp, err := http.Get(ts.URL + "/agent/transfers/nonexistent/download")
	if err != nil {
		t.Fatalf("transfer request: %v", err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("dual-port ServeMux expected plain 404 for transfer path, got %d (%s)", resp.StatusCode, string(body))
	}
	if strings.Contains(string(body), "agent authentication required") || strings.Contains(string(body), "transfer endpoint not found") {
		t.Fatalf("dual-port ServeMux must not shunt to gateway handler, got %s", string(body))
	}
}

// safeBuffer 并发安全的日志缓冲。
type safeBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (s *safeBuffer) Write(p []byte) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.Write(p)
}

func (s *safeBuffer) String() string {
	s.mu.Lock()
	defer s.mu.Unlock()
	return s.b.String()
}

func waitForLog(buf *safeBuffer, substr string, d time.Duration) bool {
	deadline := time.Now().Add(d)
	for time.Now().Before(deadline) {
		if strings.Contains(buf.String(), substr) {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	return strings.Contains(buf.String(), substr)
}

// TestSinglePortStartSkipsSecondListener 单端口模式 Start 不启动第二 listener：
// 输出 single-port gateway mode 日志且永不出现 agent gateway listening；
// 双端口对照组证明该日志断言方法有效（对照中会出现）。
func TestSinglePortStartSkipsSecondListener(t *testing.T) {
	// —— 单端口 ——
	bufSingle := &safeBuffer{}
	loggerSingle := slog.New(slog.NewTextHandler(bufSingle, nil))
	hs, err := New(singlePortConfig(t.TempDir()), loggerSingle)
	if err != nil {
		t.Fatalf("new single-port hub: %v", err)
	}
	t.Cleanup(func() { hs.Close() })
	if err := hs.Start(); err != nil {
		t.Fatalf("start single-port hub: %v", err)
	}
	if !waitForLog(bufSingle, "single-port gateway mode", 2*time.Second) {
		t.Fatalf("expected single-port gateway mode log, got: %s", bufSingle.String())
	}
	// 给对照组同等观察窗口：单端口下该日志的写入方根本不存在，不会出现
	time.Sleep(300 * time.Millisecond)
	if strings.Contains(bufSingle.String(), "agent gateway listening") {
		t.Fatalf("single-port mode must not start a second listener, log: %s", bufSingle.String())
	}

	// —— 双端口对照（WebAddr 与 GatewayAddr 字面不同，均为随机端口）——
	bufDual := &safeBuffer{}
	loggerDual := slog.New(slog.NewTextHandler(bufDual, nil))
	cfgDual := singlePortConfig(t.TempDir())
	cfgDual.WebAddr = "localhost:0"
	cfgDual.GatewayAddr = "127.0.0.1:0"
	hd, err := New(cfgDual, loggerDual)
	if err != nil {
		t.Fatalf("new dual-port hub: %v", err)
	}
	t.Cleanup(func() { hd.Close() })
	if err := hd.Start(); err != nil {
		t.Fatalf("start dual-port hub: %v", err)
	}
	if !waitForLog(bufDual, "agent gateway listening", 3*time.Second) {
		t.Fatalf("dual-port control must log agent gateway listening (log assertions valid), got: %s", bufDual.String())
	}
	if strings.Contains(bufDual.String(), "single-port gateway mode") {
		t.Fatalf("dual-port mode must not log single-port branch: %s", bufDual.String())
	}
}

// TestSinglePortModeWarnsOnGatewayTLS 单端口模式下配置 Gateway TLS 只告警不改行为。
func TestSinglePortModeWarnsOnGatewayTLS(t *testing.T) {
	buf := &safeBuffer{}
	logger := slog.New(slog.NewTextHandler(buf, nil))
	cfg := singlePortConfig(t.TempDir())
	cfg.GatewayTLSCert = "/etc/nodesteer/gw.crt"
	cfg.GatewayTLSKey = "/etc/nodesteer/gw.key"
	if _, err := New(cfg, logger); err != nil {
		t.Fatalf("new hub: %v", err)
	}
	if !strings.Contains(buf.String(), "single-port gateway mode") || !strings.Contains(buf.String(), "reverse proxy") {
		t.Fatalf("expected single-port gateway TLS misuse warning, got: %s", buf.String())
	}
}
