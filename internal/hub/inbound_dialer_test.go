package hub

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	agentconnection "github.com/SuPerCxyz/nodesteer/internal/agent/connection"
	"github.com/SuPerCxyz/nodesteer/internal/inbound"
	"github.com/SuPerCxyz/nodesteer/internal/metrics"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/SuPerCxyz/nodesteer/internal/store"
)

type dialerAgentHandler struct {
	agentconnection.Handler
	manager *agentconnection.Manager
	mu      sync.Mutex
	runs    []protocol.Envelope
	acks    int
	metrics atomic.Bool
}

func (h *dialerAgentHandler) OnHelloAck(env protocol.Envelope) {
	var p protocol.HelloAckPayload
	if json.Unmarshal(env.Payload, &p) == nil && p.Accepted {
		h.mu.Lock()
		h.acks++
		h.mu.Unlock()
		if h.metrics.CompareAndSwap(false, true) && h.manager != nil {
			value := 2.5
			h.manager.Send(protocol.NewEnvelope(protocol.MsgNodeMetrics, "", protocol.NodeMetricsPayload{
				NodeID: "node-inbound", Sequence: 1,
				Samples: []protocol.NodeMetricSample{{
					Timestamp: time.Now().UTC(), Metric: "cpu.usage", Value: &value, Quality: protocol.NodeMetricQualityValid,
				}},
			}))
		}
	}
}

func (h *dialerAgentHandler) OnRunExecution(env protocol.Envelope) {
	h.mu.Lock()
	h.runs = append(h.runs, env)
	h.mu.Unlock()
}

func (h *dialerAgentHandler) acceptedCount() int {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.acks
}

type dialerLogBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (b *dialerLogBuffer) Write(p []byte) (int, error) {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.b.Write(p)
}

func (b *dialerLogBuffer) String() string {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.b.String()
}

type dialerHarness struct {
	store      *store.SQLiteStore
	nodes      *NodeManager
	sessions   *SessionManager
	gateway    *Gateway
	authority  *inbound.Authority
	logger     *slog.Logger
	inboundCfg agentconnection.InboundConfig

	agentConn    *agentconnection.Manager
	agentCancel  context.CancelFunc
	agentDone    chan struct{}
	agentHandler *dialerAgentHandler
	endpoint     string
	node         *models.Node
	token        []byte
}

func newDialerHarness(t *testing.T, host string, badToken, badPin bool) *dialerHarness {
	t.Helper()
	st, err := store.OpenSQLite(filepath.Join(t.TempDir(), "hub.db"))
	if err != nil {
		t.Fatalf("open store: %v", err)
	}
	logger := slog.New(slog.NewTextHandler(io.Discard, nil))
	rm := NewRevisionManager(st)
	sessions := NewSessionManager()
	nodes := NewNodeManager(st, rm)
	syncMgr := NewSyncManager(st, rm, sessions, nodes)
	execMgr := NewExecutionManager(st, sessions, nodes, logger)
	gateway := NewGateway(GatewayConfig{RegistrationToken: "reg-token", HeartbeatTimeout: time.Minute}, nodes, sessions, syncMgr, execMgr, logger)

	authority, err := inbound.OpenAuthority(inbound.Paths{Dir: filepath.Join(t.TempDir(), "authority")})
	if err != nil {
		t.Fatalf("open authority: %v", err)
	}
	probe, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	port := probe.Addr().(*net.TCPAddr).Port
	probe.Close()
	endpoint := net.JoinHostPort(host, strconv.Itoa(port))
	issued, err := authority.CreateServerCertificate("node-inbound", endpoint)
	if err != nil {
		t.Fatalf("issue certificate: %v", err)
	}
	token := bytes.Repeat([]byte{9}, 32)
	encToken, err := authority.EncryptToken(token)
	if err != nil {
		t.Fatal(err)
	}
	fingerprint := issued.Fingerprint
	if badPin {
		fingerprint = strings.Repeat("0", 64)
	}
	if badToken {
		other := bytes.Repeat([]byte{8}, 32)
		encToken, err = authority.EncryptToken(other)
		if err != nil {
			t.Fatal(err)
		}
	}
	node := &models.Node{
		ID: "node-inbound", AgentID: "agent-inbound", Hostname: "inbound-host",
		Endpoint: endpoint, Status: models.NodeStatusPending,
		Capabilities: map[string]bool{
			models.CapInboundConnection: true,
			models.CapNodeMetrics:       true,
		},
	}
	if err := st.UpsertNode(context.Background(), node); err != nil {
		t.Fatalf("insert node: %v", err)
	}
	if err := st.UpdateNodeInboundIdentity(context.Background(), node.ID, true, fingerprint, issued.Serial, issued.NotAfter, encToken); err != nil {
		t.Fatalf("persist inbound identity: %v", err)
	}

	dir := t.TempDir()
	certFile := filepath.Join(dir, "agent-server.pem")
	keyFile := filepath.Join(dir, "agent-server-key.pem")
	caFile := filepath.Join(dir, "ca.pem")
	fpFile := filepath.Join(dir, "hub-client-fingerprint.txt")
	tokenFile := filepath.Join(dir, "inbound-token")
	for path, data := range map[string][]byte{
		certFile: issued.CertPEM, keyFile: issued.KeyPEM, caFile: authority.CACertPEM(),
		fpFile:    []byte(authority.HubClientFingerprint()),
		tokenFile: []byte(base64.StdEncoding.EncodeToString(token)),
	} {
		if err := os.WriteFile(path, data, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	listenHost := "127.0.0.1"
	handler := &dialerAgentHandler{}
	h := &dialerHarness{
		store: st, nodes: nodes, sessions: sessions, gateway: gateway, authority: authority,
		logger: logger, agentHandler: handler, endpoint: endpoint, node: node, token: token,
		inboundCfg: agentconnection.InboundConfig{
			ListenAddr: net.JoinHostPort(listenHost, strconv.Itoa(port)), CertFile: certFile, KeyFile: keyFile,
			CAFile: caFile, HubClientFingerprintFile: fpFile, TokenFile: tokenFile,
			MaxConnections: 4, HandshakeTimeout: 2 * time.Second,
		},
	}
	h.startAgent(t)
	t.Cleanup(func() {
		h.stopAgent()
		st.Close()
	})
	return h
}

func (h *dialerHarness) startAgent(t *testing.T) {
	t.Helper()
	handler := h.agentHandler
	agentConn := agentconnection.New("ws://127.0.0.1:1/closed", "reg-token", h.node.AgentID, handler, h.logger, nil)
	agentConn.SetAgentID(h.node.AgentID)
	agentConn.SetNodeID(h.node.ID)
	if err := agentConn.SetInboundConfig(h.inboundCfg); err != nil {
		t.Fatalf("set agent inbound config: %v", err)
	}
	handler.manager = agentConn
	agentCtx, agentCancel := context.WithCancel(context.Background())
	agentDone := make(chan struct{})
	go func() {
		defer close(agentDone)
		agentConn.Run(agentCtx, func() *protocol.HelloPayload {
			return &protocol.HelloPayload{
				ProtocolVersion: protocol.ProtocolVersion,
				Capabilities: map[string]bool{
					models.CapInboundConnection: true,
					models.CapNodeMetrics:       true,
				},
			}
		})
	}()
	h.agentConn = agentConn
	h.agentCancel = agentCancel
	h.agentDone = agentDone
}

func (h *dialerHarness) stopAgent() {
	if h.agentCancel != nil {
		h.agentCancel()
	}
	if h.agentConn != nil {
		h.agentConn.Stop()
	}
	if h.agentDone != nil {
		<-h.agentDone
	}
	h.agentCancel = nil
	h.agentConn = nil
	h.agentDone = nil
}

func mustNode(t *testing.T, st store.Store, id string) *models.Node {
	t.Helper()
	node, err := st.GetNode(context.Background(), id)
	if err != nil {
		t.Fatalf("get node: %v", err)
	}
	return node
}

func waitForDialer(t *testing.T, cond func() bool, message string) {
	t.Helper()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		if cond() {
			return
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatalf("timeout waiting for %s", message)
}

func TestInboundDialerEstablishesSessionAndControlTraffic(t *testing.T) {
	h := newDialerHarness(t, "localhost", false, false)
	candidates, err := h.nodes.ListInboundCandidates(context.Background())
	if err != nil || len(candidates) != 1 {
		t.Fatalf("candidates = %+v err=%v", candidates, err)
	}
	dialer := NewInboundDialer(InboundDialerConfig{
		Concurrency: 2, HandshakeTimeout: 3 * time.Second,
		BackoffInitial: 50 * time.Millisecond, BackoffMax: 200 * time.Millisecond,
		GracePeriod: 100 * time.Millisecond, ScanInterval: 50 * time.Millisecond,
		Authority: h.authority, Policy: mustLoopbackPolicy(t, h.endpoint),
	}, h.gateway, slog.New(slog.NewTextHandler(io.Discard, nil)))
	dialer.Start(context.Background())
	defer dialer.Stop()

	waitForDialer(t, func() bool {
		conn, ok := h.sessions.Get(h.node.ID)
		return ok && SessionIsInbound(conn)
	}, "inbound session registration")
	waitForDialer(t, func() bool { return h.agentHandler.acceptedCount() > 0 }, "agent HELLO_ACK accepted")

	ctx := context.Background()
	conn, _ := h.sessions.Get(h.node.ID)
	if err := conn.Send(protocol.NewEnvelope(protocol.MsgRunExecution, "exec-1", protocol.RunExecutionPayload{
		ExecutionID: "exec-1", TaskID: "task-1", Type: "command", Command: "true", TriggerType: "manual",
	})); err != nil {
		t.Fatalf("send command over inbound session: %v", err)
	}
	waitForDialer(t, func() bool {
		h.agentHandler.mu.Lock()
		defer h.agentHandler.mu.Unlock()
		return len(h.agentHandler.runs) == 1
	}, "RUN_EXECUTION over inbound session")

	waitForDialer(t, func() bool {
		samples, err := h.store.LatestNodeMetricSamples(ctx, h.node.ID)
		return err == nil && len(samples) > 0
	}, "NODE_METRICS over inbound session")
	node, err := h.store.GetNode(ctx, h.node.ID)
	if err != nil {
		t.Fatal(err)
	}
	if node.Status != models.NodeStatusOnline || node.LastConnectionMode != models.ConnectionModeInbound || !node.InboundProvisioned || node.InboundLastHandshake == "" {
		t.Fatalf("node after inbound session = %+v", node)
	}
	// 已有会话时拨号器不得重复拨号。
	before := h.agentHandler.acceptedCount()
	time.Sleep(200 * time.Millisecond)
	if got := h.agentHandler.acceptedCount(); got != before {
		t.Fatalf("dialer opened duplicate sessions: %d -> %d", before, got)
	}
}

func TestInboundConnectionTestStagesAndRateLimit(t *testing.T) {
	h := newDialerHarness(t, "127.0.0.1", false, false)
	dialer := NewInboundDialer(InboundDialerConfig{
		Concurrency: 1, HandshakeTimeout: 2 * time.Second,
		ScanInterval: time.Hour, Authority: h.authority, Policy: mustLoopbackPolicy(t, h.endpoint),
	}, h.gateway, slog.New(slog.NewTextHandler(io.Discard, nil)))
	result, err := dialer.TestConnection(context.Background(), mustNode(t, h.store, h.node.ID))
	if err != nil {
		t.Fatalf("test connection: %v", err)
	}
	if result.Connected || !result.TestMode || len(result.Stages) == 0 {
		t.Fatalf("test result = %+v", result)
	}
	for _, stage := range result.Stages {
		if !stage.OK {
			t.Fatalf("stage %s failed: %+v", stage.Name, stage)
		}
	}
	if _, err := dialer.TestConnection(context.Background(), h.node); !errors.Is(err, ErrInboundTestRateLimited) {
		t.Fatalf("second test err = %v, want rate limited", err)
	}
}

func TestInboundDialerReconnectsAfterAgentRestart(t *testing.T) {
	h := newDialerHarness(t, "127.0.0.1", false, false)
	logs := &dialerLogBuffer{}
	dialer := NewInboundDialer(InboundDialerConfig{
		Concurrency: 1, HandshakeTimeout: 2 * time.Second,
		BackoffInitial: 50 * time.Millisecond, BackoffMax: 200 * time.Millisecond,
		GracePeriod: 50 * time.Millisecond, ScanInterval: 20 * time.Millisecond,
		Authority: h.authority, Policy: mustLoopbackPolicy(t, h.endpoint),
	}, h.gateway, slog.New(slog.NewTextHandler(logs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	dialer.Start(context.Background())
	defer dialer.Stop()

	waitForDialer(t, func() bool {
		conn, ok := h.sessions.Get(h.node.ID)
		return ok && SessionIsInbound(conn)
	}, "first inbound session")
	firstAcks := h.agentHandler.acceptedCount()
	h.stopAgent()
	waitForDialer(t, func() bool {
		_, ok := h.sessions.Get(h.node.ID)
		return !ok
	}, "inbound session cleanup after agent stop")
	h.startAgent(t)
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		conn, ok := h.sessions.Get(h.node.ID)
		if ok && SessionIsInbound(conn) && h.agentHandler.acceptedCount() > firstAcks {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	node, nodeErr := h.store.GetNode(context.Background(), h.node.ID)
	if nodeErr != nil || node == nil {
		t.Fatalf("timeout waiting for reconnected inbound session: node_err=%v acks=%d logs=\n%s",
			nodeErr, h.agentHandler.acceptedCount(), logs.String())
	}
	t.Fatalf("timeout waiting for reconnected inbound session: status=%s last_err=%q acks=%d logs=\n%s",
		node.Status, node.InboundLastError, h.agentHandler.acceptedCount(), logs.String())
}

func TestInboundDialerRestartRecoversSession(t *testing.T) {
	h := newDialerHarness(t, "127.0.0.1", false, false)
	cfg := InboundDialerConfig{
		Concurrency: 1, HandshakeTimeout: 2 * time.Second,
		BackoffInitial: 50 * time.Millisecond, BackoffMax: 200 * time.Millisecond,
		GracePeriod: 50 * time.Millisecond, ScanInterval: 20 * time.Millisecond,
		Authority: h.authority, Policy: mustLoopbackPolicy(t, h.endpoint),
	}
	firstLogs := &dialerLogBuffer{}
	first := NewInboundDialer(cfg, h.gateway, slog.New(slog.NewTextHandler(firstLogs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	first.Start(context.Background())
	waitForDialer(t, func() bool {
		conn, ok := h.sessions.Get(h.node.ID)
		return ok && SessionIsInbound(conn)
	}, "session before dialer restart")
	first.Stop()
	waitForDialer(t, func() bool {
		_, ok := h.sessions.Get(h.node.ID)
		return !ok
	}, "session cleanup after dialer stop")

	secondLogs := &dialerLogBuffer{}
	second := NewInboundDialer(cfg, h.gateway, slog.New(slog.NewTextHandler(secondLogs, &slog.HandlerOptions{Level: slog.LevelDebug})))
	second.Start(context.Background())
	defer second.Stop()
	deadline := time.Now().Add(10 * time.Second)
	for time.Now().Before(deadline) {
		conn, ok := h.sessions.Get(h.node.ID)
		if ok && SessionIsInbound(conn) {
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	node, nodeErr := h.store.GetNode(context.Background(), h.node.ID)
	t.Fatalf("timeout waiting for session after dialer restart: node_err=%v node=%+v first_logs=\n%s\nsecond_logs=\n%s", nodeErr, node, firstLogs.String(), secondLogs.String())
}

func TestInboundDialerDoesNotDialWhenSessionExists(t *testing.T) {
	h := newDialerHarness(t, "127.0.0.1", false, false)
	h.sessions.Register(&fakeAgentConn{nodeID: h.node.ID})
	dialer := NewInboundDialer(InboundDialerConfig{
		Concurrency: 1, HandshakeTimeout: time.Second, BackoffInitial: 20 * time.Millisecond,
		BackoffMax: 40 * time.Millisecond, ScanInterval: 20 * time.Millisecond,
		Authority: h.authority, Policy: mustLoopbackPolicy(t, h.endpoint),
	}, h.gateway, slog.New(slog.NewTextHandler(io.Discard, nil)))
	dialer.Start(context.Background())
	defer dialer.Stop()
	time.Sleep(300 * time.Millisecond)
	if got := h.agentHandler.acceptedCount(); got != 0 {
		t.Fatalf("dialer used inbound while outbound session exists: %d", got)
	}
	conn, ok := h.sessions.Get(h.node.ID)
	if !ok || SessionOrigin(conn) != models.ConnectionModeOutbound {
		t.Fatal("existing outbound session was replaced")
	}
}

func TestInboundDialerBadTokenAndPinFailClosed(t *testing.T) {
	for _, tc := range []struct {
		name     string
		badToken bool
		badPin   bool
		code     string
	}{
		{name: "bad token", badToken: true, code: "invalid_node_token"},
		{name: "bad pin", badPin: true, code: "inbound_pin_mismatch"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h := newDialerHarness(t, "127.0.0.1", tc.badToken, tc.badPin)
			dialer := NewInboundDialer(InboundDialerConfig{
				Concurrency: 1, HandshakeTimeout: 2 * time.Second,
				BackoffInitial: 50 * time.Millisecond, BackoffMax: 100 * time.Millisecond,
				ScanInterval: 20 * time.Millisecond, Authority: h.authority, Policy: mustLoopbackPolicy(t, h.endpoint),
			}, h.gateway, slog.New(slog.NewTextHandler(io.Discard, nil)))
			dialer.Start(context.Background())
			defer dialer.Stop()
			waitForDialer(t, func() bool {
				node, err := h.store.GetNode(context.Background(), h.node.ID)
				return err == nil && node.InboundLastError == tc.code
			}, "inbound failure diagnostic "+tc.code)
			if conn, ok := h.sessions.Get(h.node.ID); ok {
				t.Fatalf("bad inbound handshake registered session: %+v", conn)
			}
		})
	}
}

func TestInboundDialerBackoffAndConcurrencyBounds(t *testing.T) {
	d := NewInboundDialer(InboundDialerConfig{
		Concurrency: 2, BackoffInitial: 100 * time.Millisecond, BackoffMax: 400 * time.Millisecond,
	}, &Gateway{}, slog.New(slog.NewTextHandler(io.Discard, nil)))
	if cap(d.slots) != 2 {
		t.Fatalf("concurrency slots = %d, want 2", cap(d.slots))
	}
	for failures, upper := range map[int]time.Duration{
		1:  120 * time.Millisecond,
		5:  480 * time.Millisecond,
		20: 480 * time.Millisecond,
	} {
		got := d.backoff(failures)
		if got <= 0 || got > upper {
			t.Fatalf("backoff(%d) = %v, want (0, %v]", failures, got, upper)
		}
	}
}

func mustLoopbackPolicy(t *testing.T, endpoint string) *inbound.DialPolicy {
	t.Helper()
	_, portText, err := net.SplitHostPort(endpoint)
	if err != nil {
		t.Fatal(err)
	}
	port, err := strconv.Atoi(portText)
	if err != nil {
		t.Fatal(err)
	}
	policy, err := inbound.NewDialPolicy([]int{port}, nil, true)
	if err != nil {
		t.Fatal(err)
	}
	return policy
}

func TestInboundDialMetricsRecordsStage(t *testing.T) {
	h := newDialerHarness(t, "127.0.0.1", false, true)
	m := metrics.New()
	dialer := NewInboundDialer(InboundDialerConfig{
		Concurrency: 1, HandshakeTimeout: 2 * time.Second, ScanInterval: time.Hour,
		Authority: h.authority, Policy: mustLoopbackPolicy(t, h.endpoint), Metrics: m,
	}, h.gateway, slog.New(slog.NewTextHandler(io.Discard, nil)))
	dialer.ctx = context.Background()
	dialer.dialNode(*mustNode(t, h.store, h.node.ID))
	rendered := m.Render()
	if !strings.Contains(rendered, `nodesteer_inbound_dial_total{stage="inbound_pin_mismatch",result="failure"} 1`) {
		t.Fatalf("inbound dial metric missing pin failure: %s", rendered)
	}
}

func TestInboundDialPolicyMixedResolutionAndSingleLookup(t *testing.T) {
	calls := 0
	dialer := NewInboundDialer(InboundDialerConfig{
		Concurrency: 1, HandshakeTimeout: time.Second, ScanInterval: time.Hour,
		Policy: inbound.DefaultDialPolicy(),
		LookupIP: func(context.Context, string) ([]net.IP, error) {
			calls++
			return []net.IP{net.ParseIP("127.0.0.1"), net.ParseIP("169.254.169.254"), net.ParseIP("10.0.0.5")}, nil
		},
	}, nil, slog.New(slog.NewTextHandler(io.Discard, nil)))
	allowed, err := dialer.resolveAllowedIPs(context.Background(), inbound.Endpoint{Host: "mixed.example", Port: 18444})
	if err != nil {
		t.Fatalf("resolve mixed: %v", err)
	}
	if len(allowed) != 1 || !allowed[0].Equal(net.ParseIP("10.0.0.5")) {
		t.Fatalf("allowed = %v, want only RFC1918 address", allowed)
	}
	// dialAllowedIP 只解析一次；若解析器被再次调用会返回拒绝地址，但不应发生。
	calls = 0
	dialer.cfg.LookupIP = func(context.Context, string) ([]net.IP, error) {
		calls++
		if calls == 1 {
			return []net.IP{net.ParseIP("127.0.0.1")}, nil
		}
		return []net.IP{net.ParseIP("169.254.169.254")}, nil
	}
	if _, err := dialer.dialAllowedIP(context.Background(), inbound.Endpoint{Host: "rebind.example", Port: 18444}); !errors.Is(err, errInboundDenied) {
		t.Fatalf("dial mixed denied err = %v", err)
	}
	if calls != 1 {
		t.Fatalf("lookup calls = %d, want exactly 1 (DNS rebinding guard)", calls)
	}
}

func TestInboundDialRejectsExpiredCertificate(t *testing.T) {
	h := newDialerHarness(t, "127.0.0.1", false, false)
	node := mustNode(t, h.store, h.node.ID)
	if err := h.store.UpdateNodeInboundIdentity(context.Background(), node.ID, true,
		node.InboundCertFingerprint, node.InboundCertSerial, time.Now().Add(-time.Hour), node.InboundTokenEnc); err != nil {
		t.Fatal(err)
	}
	node = mustNode(t, h.store, h.node.ID)
	dialer := NewInboundDialer(InboundDialerConfig{
		Concurrency: 1, HandshakeTimeout: time.Second, ScanInterval: time.Hour,
		Authority: h.authority, Policy: mustLoopbackPolicy(t, h.endpoint),
	}, h.gateway, slog.New(slog.NewTextHandler(io.Discard, nil)))
	err := dialer.dialOnce(context.Background(), node)
	var stage *dialStageError
	if !errors.As(err, &stage) || stage.code != protocol.ErrorCodeInboundCertificateExpired {
		t.Fatalf("expired dial err = %v, want certificate expired", err)
	}
	if _, err := dialer.TestConnection(context.Background(), node); err == nil {
		t.Fatal("connection test accepted expired certificate")
	}
}

func TestInboundDialPortClosedKeepsNodeStatus(t *testing.T) {
	h := newDialerHarness(t, "127.0.0.1", false, false)
	h.stopAgent()
	node := mustNode(t, h.store, h.node.ID)
	dialer := NewInboundDialer(InboundDialerConfig{
		Concurrency: 1, HandshakeTimeout: time.Second, ScanInterval: time.Hour,
		Authority: h.authority, Policy: mustLoopbackPolicy(t, h.endpoint),
	}, h.gateway, slog.New(slog.NewTextHandler(io.Discard, nil)))
	dialer.ctx = context.Background()
	dialer.dialNode(*node)
	after := mustNode(t, h.store, h.node.ID)
	if after.Status != models.NodeStatusPending || after.InboundLastError != "tcp_failed" {
		t.Fatalf("node after closed port = status %s last_error %q", after.Status, after.InboundLastError)
	}
}
