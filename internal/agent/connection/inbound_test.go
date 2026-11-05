package connection

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/inbound"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

type inboundRecordingHandler struct {
	Handler
	mu       sync.Mutex
	accepted []protocol.HelloAckPayload
	runs     chan protocol.Envelope
}

func (h *inboundRecordingHandler) OnHelloAck(env protocol.Envelope) {
	var p protocol.HelloAckPayload
	if json.Unmarshal(env.Payload, &p) == nil && p.Accepted {
		h.mu.Lock()
		h.accepted = append(h.accepted, p)
		h.mu.Unlock()
	}
}

func (h *inboundRecordingHandler) OnRunExecution(env protocol.Envelope) {
	select {
	case h.runs <- env:
	default:
	}
}

type inboundMaterial struct {
	authority *inbound.Authority
	token     []byte
	dir       string
	cfg       InboundConfig
}

func newInboundMaterial(t *testing.T, listenAddr string, shared *inbound.Authority, overrides func(*InboundConfig)) *inboundMaterial {
	t.Helper()
	authority := shared
	if authority == nil {
		var err error
		authority, err = inbound.OpenAuthority(inbound.Paths{Dir: filepath.Join(t.TempDir(), "hub-pki")})
		if err != nil {
			t.Fatalf("open authority: %v", err)
		}
	}
	issued, err := authority.CreateServerCertificate("node-1", listenAddr)
	if err != nil {
		t.Fatalf("issue server certificate: %v", err)
	}
	dir := t.TempDir()
	certFile := filepath.Join(dir, "agent-server.pem")
	keyFile := filepath.Join(dir, "agent-server-key.pem")
	caFile := filepath.Join(dir, "ca.pem")
	fpFile := filepath.Join(dir, "hub-client-fingerprint.txt")
	tokenFile := filepath.Join(dir, "inbound-token")
	token := bytes.Repeat([]byte{7}, 32)
	if err := os.WriteFile(certFile, issued.CertPEM, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(keyFile, issued.KeyPEM, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(caFile, authority.CACertPEM(), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(fpFile, []byte(authority.HubClientFingerprint()), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(tokenFile, []byte(base64.StdEncoding.EncodeToString(token)), 0o600); err != nil {
		t.Fatal(err)
	}
	cfg := InboundConfig{
		ListenAddr: listenAddr, CertFile: certFile, KeyFile: keyFile, CAFile: caFile,
		HubClientFingerprintFile: fpFile, TokenFile: tokenFile,
		MaxConnections: 8, HandshakeTimeout: 2 * time.Second,
	}
	if overrides != nil {
		overrides(&cfg)
	}
	return &inboundMaterial{authority: authority, token: token, dir: dir, cfg: cfg}
}

func freeLoopbackAddr(t *testing.T) string {
	t.Helper()
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatalf("reserve addr: %v", err)
	}
	addr := listener.Addr().String()
	listener.Close()
	return addr
}

func startInboundManager(t *testing.T, cfg InboundConfig, handler Handler) (*Manager, context.CancelFunc, chan struct{}) {
	t.Helper()
	manager := New("ws://127.0.0.1:1/closed", "registration-token", "agent-1", handler,
		slog.New(slog.NewTextHandler(io.Discard, nil)), nil)
	manager.delayFn = func() time.Duration { return time.Hour }
	manager.SetAgentID("agent-1")
	manager.SetNodeID("node-1")
	if err := manager.SetInboundConfig(cfg); err != nil {
		t.Fatalf("set inbound config: %v", err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		manager.Run(ctx, func() *protocol.HelloPayload {
			return &protocol.HelloPayload{
				ProtocolVersion: protocol.ProtocolVersion,
				Capabilities: map[string]bool{
					models.CapInboundConnection: true,
					models.CapInboundDataPlane:  false,
				},
			}
		})
	}()
	t.Cleanup(func() {
		cancel()
		manager.Stop()
		waitDone(t, done)
	})
	return manager, cancel, done
}

func inboundClient(t *testing.T, authority *inbound.Authority, addr string) *websocket.Conn {
	t.Helper()
	tlsConfig := authority.ClientTLSConfig()
	tlsConfig.ServerName = "127.0.0.1"
	client := &http.Client{Transport: &http.Transport{TLSClientConfig: tlsConfig}}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	conn, _, err := websocket.Dial(ctx, "wss://"+addr+"/agent", &websocket.DialOptions{
		HTTPClient: client, Subprotocols: []string{"nodesteer"},
	})
	if err != nil {
		t.Fatalf("dial inbound: %v", err)
	}
	return conn
}

func completeInboundHandshake(t *testing.T, conn *websocket.Conn, authority *inbound.Authority, token []byte, nodeID, agentID string) string {
	t.Helper()
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	nonce := "hub-nonce-1"
	if err := wsjson.Write(ctx, conn, protocol.NewEnvelope(protocol.MsgHubHello, "", protocol.HubHelloPayload{
		HelloAckPayload: protocol.HelloAckPayload{Accepted: true, NodeID: nodeID, AgentID: agentID},
		HubID:           "hub-1", Nonce: nonce, IssuedAt: time.Now().UTC(), DataPlane: false,
	})); err != nil {
		t.Fatalf("write HUB_HELLO: %v", err)
	}
	_, data, err := conn.Read(ctx)
	if err != nil {
		t.Fatalf("read HUB_HELLO_ACK: %v", err)
	}
	var env protocol.Envelope
	if err := json.Unmarshal(data, &env); err != nil || env.Type != protocol.MsgHubHelloAck {
		t.Fatalf("expected HUB_HELLO_ACK, got %s err=%v", env.Type, err)
	}
	var ack protocol.HubHelloAckPayload
	if err := json.Unmarshal(env.Payload, &ack); err != nil {
		t.Fatalf("decode HUB_HELLO_ACK: %v", err)
	}
	if !ack.Accepted || ack.NodeID != nodeID || ack.AgentID != agentID || ack.SessionID == "" {
		t.Fatalf("unexpected HUB_HELLO_ACK: %+v", ack)
	}
	if !protocol.VerifyInboundTokenProof(token, nonce, ack.AgentNonce, nodeID, ack.TokenProof) {
		t.Fatal("HUB_HELLO_ACK token proof mismatch")
	}
	_, data, err = conn.Read(ctx)
	if err != nil {
		t.Fatalf("read HELLO: %v", err)
	}
	if err := json.Unmarshal(data, &env); err != nil || env.Type != protocol.MsgHello {
		t.Fatalf("expected HELLO, got %s err=%v", env.Type, err)
	}
	var hello protocol.HelloPayload
	if err := json.Unmarshal(env.Payload, &hello); err != nil {
		t.Fatalf("decode HELLO: %v", err)
	}
	if hello.AgentID != agentID || !hello.Capabilities[models.CapInboundConnection] {
		t.Fatalf("HELLO = %+v", hello)
	}
	if err := wsjson.Write(ctx, conn, protocol.NewEnvelope(protocol.MsgHelloAck, env.ID, protocol.HelloAckPayload{
		Accepted: true, NodeID: nodeID, AgentID: agentID,
	})); err != nil {
		t.Fatalf("write HELLO_ACK: %v", err)
	}
	return ack.SessionID
}

func TestInboundListenerHandshakeCommandAndMetrics(t *testing.T) {
	handler := &inboundRecordingHandler{runs: make(chan protocol.Envelope, 1)}
	addr := freeLoopbackAddr(t)
	material := newInboundMaterial(t, addr, nil, nil)
	manager, _, _ := startInboundManager(t, material.cfg, handler)
	conn := inboundClient(t, material.authority, addr)
	defer conn.Close(websocket.StatusNormalClosure, "test")
	completeInboundHandshake(t, conn, material.authority, material.token, "node-1", "agent-1")
	waitFor(t, "inbound handshake complete", manager.HandshakeComplete)

	// Hub -> Agent 命令走同一入站连接。
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := wsjson.Write(ctx, conn, protocol.NewEnvelope(protocol.MsgRunExecution, "exec-1", protocol.RunExecutionPayload{
		ExecutionID: "exec-1", TaskID: "task-1", Type: "command", Command: "true", TriggerType: "manual",
	})); err != nil {
		t.Fatalf("send RUN_EXECUTION: %v", err)
	}
	select {
	case env := <-handler.runs:
		if env.Type != protocol.MsgRunExecution {
			t.Fatalf("handler received %s", env.Type)
		}
	case <-time.After(3 * time.Second):
		t.Fatal("RUN_EXECUTION not delivered over inbound session")
	}

	// Agent -> Hub 指标走同一连接。
	value := 1.0
	if !manager.Send(protocol.NewEnvelope(protocol.MsgNodeMetrics, "", protocol.NodeMetricsPayload{
		NodeID: "node-1", Sequence: 1,
		Samples: []protocol.NodeMetricSample{{
			Timestamp: time.Now().UTC(), Metric: "cpu.usage", Value: &value, Quality: protocol.NodeMetricQualityValid,
		}},
	})) {
		t.Fatal("Send over inbound session failed")
	}
	_, data, err := conn.Read(ctx)
	if err != nil {
		t.Fatalf("read NODE_METRICS: %v", err)
	}
	var env protocol.Envelope
	if err := json.Unmarshal(data, &env); err != nil || env.Type != protocol.MsgNodeMetrics {
		t.Fatalf("expected NODE_METRICS, got %s err=%v", env.Type, err)
	}
}

func TestInboundDataPlaneAuthentication(t *testing.T) {
	addr := freeLoopbackAddr(t)
	material := newInboundMaterial(t, addr, nil, nil)
	handler := &inboundRecordingHandler{runs: make(chan protocol.Envelope, 1)}
	manager, _, _ := startInboundManager(t, material.cfg, handler)
	manager.SetInboundDataPlaneHandler(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("X-NodeSteer-Data-Plane", "ok")
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte(`{"ok":true}`))
	}))
	conn := inboundClient(t, material.authority, addr)
	defer conn.Close(websocket.StatusNormalClosure, "test")
	sessionID := completeInboundHandshake(t, conn, material.authority, material.token, "node-1", "agent-1")
	waitFor(t, "inbound handshake complete", manager.HandshakeComplete)

	tlsConfig := material.authority.ClientTLSConfig()
	tlsConfig.ServerName = "127.0.0.1"
	client := &http.Client{Transport: &http.Transport{TLSClientConfig: tlsConfig}}
	do := func(token, session, node string) *http.Response {
		req, err := http.NewRequest(http.MethodGet, "https://"+addr+"/agent-data/v1/health", nil)
		if err != nil {
			t.Fatal(err)
		}
		if token != "" {
			req.Header.Set("Authorization", "Bearer "+token)
		}
		if session != "" {
			req.Header.Set("X-NodeSteer-Session", session)
		}
		if node != "" {
			req.Header.Set("X-NodeSteer-Node", node)
		}
		resp, err := client.Do(req)
		if err != nil {
			t.Fatalf("data plane request: %v", err)
		}
		return resp
	}

	encodedToken := base64.RawURLEncoding.EncodeToString(material.token)
	for name, resp := range map[string]*http.Response{
		"missing token": do("", sessionID, "node-1"),
		"wrong token":   do("wrong", sessionID, "node-1"),
		"wrong session": do(encodedToken, "wrong", "node-1"),
		"wrong node":    do(encodedToken, sessionID, "node-2"),
	} {
		resp.Body.Close()
		if resp.StatusCode != http.StatusUnauthorized && resp.StatusCode != http.StatusForbidden {
			t.Fatalf("%s status = %d, want 401/403", name, resp.StatusCode)
		}
	}
	resp := do(encodedToken, sessionID, "node-1")
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || resp.Header.Get("X-NodeSteer-Data-Plane") != "ok" {
		t.Fatalf("valid data plane status=%d headers=%v", resp.StatusCode, resp.Header)
	}
}

func TestInboundListenerRejectsDuplicateSession(t *testing.T) {
	handler := &inboundRecordingHandler{runs: make(chan protocol.Envelope, 1)}
	addr := freeLoopbackAddr(t)
	material := newInboundMaterial(t, addr, nil, nil)
	manager, _, _ := startInboundManager(t, material.cfg, handler)
	first := inboundClient(t, material.authority, addr)
	defer first.Close(websocket.StatusNormalClosure, "test")
	completeInboundHandshake(t, first, material.authority, material.token, "node-1", "agent-1")
	waitFor(t, "first inbound accepted", manager.HandshakeComplete)

	second := inboundClient(t, material.authority, addr)
	defer second.Close(websocket.StatusNormalClosure, "test")
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	if err := wsjson.Write(ctx, second, protocol.NewEnvelope(protocol.MsgHubHello, "", protocol.HubHelloPayload{
		HelloAckPayload: protocol.HelloAckPayload{Accepted: true, NodeID: "node-1", AgentID: "agent-1"},
		Nonce:           "dup", IssuedAt: time.Now().UTC(),
	})); err != nil {
		return // 已被拒绝关闭
	}
	if _, _, err := second.Read(ctx); err == nil {
		t.Fatal("duplicate inbound session was not rejected")
	}
}

func TestInboundListenerRejectsBadClientCertificateAndPin(t *testing.T) {
	addr := freeLoopbackAddr(t)
	material := newInboundMaterial(t, addr, nil, nil)
	startInboundManager(t, material.cfg, &inboundRecordingHandler{runs: make(chan protocol.Envelope, 1)})

	other, err := inbound.OpenAuthority(inbound.Paths{Dir: filepath.Join(t.TempDir(), "other-pki")})
	if err != nil {
		t.Fatal(err)
	}
	tlsConfig := other.ClientTLSConfig()
	tlsConfig.ServerName = "127.0.0.1"
	client := &http.Client{Transport: &http.Transport{TLSClientConfig: tlsConfig}}
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if _, _, err := websocket.Dial(ctx, "wss://"+addr+"/agent", &websocket.DialOptions{HTTPClient: client}); err == nil {
		t.Fatal("client certificate signed by unknown CA was accepted")
	}

	// 有效链但 fingerprint 不匹配：pin 校验必须拒绝。
	wrongPinFile := filepath.Join(material.dir, "wrong-pin.txt")
	if err := os.WriteFile(wrongPinFile, []byte(strings.Repeat("0", 64)), 0o600); err != nil {
		t.Fatal(err)
	}
	pinAddr := freeLoopbackAddr(t)
	pinMaterial := newInboundMaterial(t, pinAddr, material.authority, func(cfg *InboundConfig) {
		cfg.HubClientFingerprintFile = wrongPinFile
	})
	startInboundManager(t, pinMaterial.cfg, &inboundRecordingHandler{runs: make(chan protocol.Envelope, 1)})
	tlsConfig = material.authority.ClientTLSConfig()
	tlsConfig.ServerName = "127.0.0.1"
	client = &http.Client{Transport: &http.Transport{TLSClientConfig: tlsConfig}}
	ctx2, cancel2 := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel2()
	if _, _, err := websocket.Dial(ctx2, "wss://"+pinAddr+"/agent", &websocket.DialOptions{HTTPClient: client}); err == nil {
		t.Fatal("pinned fingerprint mismatch was accepted")
	}
}

func TestInboundListenerHandshakeTimeoutAndSourceCIDR(t *testing.T) {
	addr := freeLoopbackAddr(t)
	material := newInboundMaterial(t, addr, nil, func(cfg *InboundConfig) {
		cfg.HandshakeTimeout = 150 * time.Millisecond
	})
	manager, _, _ := startInboundManager(t, material.cfg, &inboundRecordingHandler{runs: make(chan protocol.Envelope, 1)})
	conn := inboundClient(t, material.authority, addr)
	defer conn.Close(websocket.StatusNormalClosure, "test")
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if _, _, err := conn.Read(ctx); err == nil {
		t.Fatal("inbound handshake without HUB_HELLO must time out")
	}
	waitFor(t, "inbound session cleanup", func() bool { return !manager.IsConnected() })

	cidrAddr := freeLoopbackAddr(t)
	cidrMaterial := newInboundMaterial(t, cidrAddr, nil, func(cfg *InboundConfig) {
		cfg.AllowedSourceCIDRs = []string{"192.0.2.0/24"}
	})
	startInboundManager(t, cidrMaterial.cfg, &inboundRecordingHandler{runs: make(chan protocol.Envelope, 1)})
	tlsConfig := cidrMaterial.authority.ClientTLSConfig()
	tlsConfig.ServerName = "127.0.0.1"
	client := &http.Client{Transport: &http.Transport{TLSClientConfig: tlsConfig}}
	ctx2, cancel2 := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel2()
	if _, _, err := websocket.Dial(ctx2, "wss://"+cidrAddr+"/agent", &websocket.DialOptions{HTTPClient: client}); err == nil {
		t.Fatal("source outside allowed CIDR was accepted")
	}
}

func TestInboundRateLimiterAndNonceReplay(t *testing.T) {
	limiter := newInboundRateLimiter(1, 10)
	if !limiter.allow("127.0.0.1") {
		t.Fatal("first attempt must be allowed")
	}
	limiter.recordFailure("127.0.0.1")
	if limiter.allow("127.0.0.1") {
		t.Fatal("per-IP failure limit must reject after failed attempt")
	}
	limiter.recordSuccess("127.0.0.1")
	if !limiter.allow("127.0.0.1") {
		t.Fatal("successful handshake must clear failure limit")
	}

	other := newInboundRateLimiter(10, 1)
	if !other.allow("127.0.0.1") {
		t.Fatal("global first attempt must be allowed")
	}
	other.recordFailure("127.0.0.1")
	if other.allow("192.0.2.1") {
		t.Fatal("global failure limit must reject second IP")
	}

	cache := newInboundNonceCache()
	now := time.Now()
	if !cache.use("nonce", now) {
		t.Fatal("fresh nonce rejected")
	}
	if cache.use("nonce", now.Add(time.Second)) {
		t.Fatal("replayed nonce accepted inside TTL")
	}
	if !cache.use("nonce", now.Add(inboundNonceTTL+time.Second)) {
		t.Fatal("nonce after TTL must be accepted")
	}

	manager := New("ws://127.0.0.1:1", "", "agent-1", nil, slog.Default(), nil)
	if err := manager.validateHubHello(protocol.HubHelloPayload{
		HelloAckPayload: protocol.HelloAckPayload{NodeID: "node-1", AgentID: "agent-1"},
		Nonce:           "old",
		IssuedAt:        now.Add(-2 * time.Minute),
	}, newInboundNonceCache()); err == nil {
		t.Fatal("stale HUB_HELLO timestamp must be rejected")
	}
}

func TestInboundPriorityGates(t *testing.T) {
	manager := New("ws://127.0.0.1:1", "", "agent-1", nil, slog.Default(), nil)
	outbound := newHubSession(nil, models.ConnectionModeOutbound)
	outbound.accepted = true
	manager.outbound = outbound
	if err := manager.canAcceptInbound(); err == nil {
		t.Fatal("inbound must be rejected while outbound session is authenticated")
	}
	manager.outbound = nil
	manager.inbound = newHubSession(nil, models.ConnectionModeInbound)
	if err := manager.canAcceptInbound(); err == nil {
		t.Fatal("second inbound session must be rejected")
	}
}

func TestInboundRequestLimiter(t *testing.T) {
	limiter := newInboundRequestLimiter(2)
	if !limiter.allow("session") || !limiter.allow("session") {
		t.Fatal("first two data plane requests must be allowed")
	}
	if limiter.allow("session") {
		t.Fatal("third request in the window must be rate limited")
	}
}

func TestInboundConfigRejectsIncompleteAndInsecureBundle(t *testing.T) {
	manager := New("ws://127.0.0.1:1", "", "", nil, slog.Default(), nil)
	if err := manager.SetInboundConfig(InboundConfig{CertFile: "/tmp/cert.pem"}); err == nil {
		t.Fatal("incomplete inbound bundle must be rejected")
	}
	dir := t.TempDir()
	keyPath := filepath.Join(dir, "key.pem")
	if err := os.WriteFile(keyPath, []byte("x"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := requirePrivateFile(keyPath, "test key"); err == nil {
		t.Fatal("world-readable private key must be rejected")
	}
}
