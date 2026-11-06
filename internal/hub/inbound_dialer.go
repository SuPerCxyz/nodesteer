package hub

import (
	"context"
	cryptorand "crypto/rand"
	"crypto/sha256"
	"crypto/tls"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"math/rand"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/inbound"
	"github.com/SuPerCxyz/nodesteer/internal/metrics"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

const (
	defaultInboundDialConcurrency = 16
	defaultInboundDialTimeout     = 20 * time.Second
	defaultInboundBackoffInitial  = 2 * time.Second
	defaultInboundBackoffMax      = 60 * time.Second
	defaultInboundGrace           = 10 * time.Second
	defaultInboundScanInterval    = 5 * time.Second
)

// InboundDialerConfig Hub 主动拨号器配置。
type InboundDialerConfig struct {
	Concurrency      int
	HandshakeTimeout time.Duration
	BackoffInitial   time.Duration
	BackoffMax       time.Duration
	GracePeriod      time.Duration
	ScanInterval     time.Duration
	Authority        *inbound.Authority
	Policy           *inbound.DialPolicy
	Metrics          *metrics.Metrics
	// LookupIP 仅用于测试注入；生产留空时使用系统解析器。
	LookupIP func(context.Context, string) ([]net.IP, error)
}

// InboundDialer 按节点 endpoint 主动拨号 Agent 入站 TLS listener。
//
// 拨号只负责建立并交给 Gateway 的读循环；HELLO 被接受后才由 SessionManager
// 注册会话并置 online。拨号失败只更新节点诊断，不改变已有出站会话状态。
type InboundDialer struct {
	cfg       InboundDialerConfig
	gateway   *Gateway
	logger    *slog.Logger
	ctx       context.Context
	cancel    context.CancelFunc
	trigger   chan string
	slots     chan struct{}
	wg        sync.WaitGroup
	mu        sync.Mutex
	state     map[string]*inboundDialState
	testMu    sync.Mutex
	testLast  map[string]time.Time
	startOnce sync.Once
	stopOnce  sync.Once
}

type inboundDialState struct {
	inFlight    bool
	failures    int
	nextAttempt time.Time
	unsupported bool
	connected   bool
}

type dialStageError struct {
	code string
	err  error
}

func (e *dialStageError) Error() string {
	if e.err == nil {
		return e.code
	}
	return e.code + ": " + e.err.Error()
}

func (e *dialStageError) Unwrap() error { return e.err }

var (
	errInboundPinMismatch = errors.New("inbound certificate pin mismatch")
	errInboundDenied      = errors.New("inbound endpoint denied by policy")
)

// NewInboundDialer 创建拨号器；Enabled 由调用方通过是否启动决定。
func NewInboundDialer(cfg InboundDialerConfig, gateway *Gateway, logger *slog.Logger) *InboundDialer {
	if cfg.Concurrency <= 0 {
		cfg.Concurrency = defaultInboundDialConcurrency
	}
	if cfg.HandshakeTimeout <= 0 {
		cfg.HandshakeTimeout = defaultInboundDialTimeout
	}
	if cfg.BackoffInitial <= 0 {
		cfg.BackoffInitial = defaultInboundBackoffInitial
	}
	if cfg.BackoffMax < cfg.BackoffInitial {
		cfg.BackoffMax = defaultInboundBackoffMax
	}
	if cfg.GracePeriod <= 0 {
		cfg.GracePeriod = defaultInboundGrace
	}
	if cfg.ScanInterval <= 0 {
		cfg.ScanInterval = defaultInboundScanInterval
	}
	if cfg.Policy == nil {
		cfg.Policy = inbound.DefaultDialPolicy()
	}
	return &InboundDialer{
		cfg: cfg, gateway: gateway, logger: logger,
		trigger: make(chan string, 1),
		slots:   make(chan struct{}, cfg.Concurrency),
		state:   map[string]*inboundDialState{},
	}
}

// Start 启动拨号循环；ctx 取消后停止并等待拨号/会话 goroutine 退出。
func (d *InboundDialer) Start(ctx context.Context) {
	d.startOnce.Do(func() {
		d.ctx, d.cancel = context.WithCancel(ctx)
		d.wg.Add(1)
		go func() {
			defer d.wg.Done()
			d.loop()
		}()
	})
}

// Stop 停止拨号器并关闭所有由拨号器建立的会话。
func (d *InboundDialer) Stop() {
	d.stopOnce.Do(func() {
		if d.cancel != nil {
			d.cancel()
		}
	})
	d.wg.Wait()
}

// Trigger 立即重新评估指定节点；空 nodeID 表示全量。
func (d *InboundDialer) Trigger(nodeID string) {
	if d == nil {
		return
	}
	if nodeID != "" {
		d.mu.Lock()
		st := d.state[nodeID]
		if st == nil {
			st = &inboundDialState{}
			d.state[nodeID] = st
		} else if !st.inFlight {
			st.failures = 0
			st.nextAttempt = time.Time{}
			st.unsupported = false
		}
		d.mu.Unlock()
	}
	select {
	case d.trigger <- nodeID:
	default:
	}
}

func (d *InboundDialer) loop() {
	ticker := time.NewTicker(d.cfg.ScanInterval)
	defer ticker.Stop()
	d.scan()
	for {
		select {
		case <-d.ctx.Done():
			return
		case <-ticker.C:
			d.scan()
		case <-d.trigger:
			d.scan()
		}
	}
}

func (d *InboundDialer) scan() {
	if d.ctx == nil || d.ctx.Err() != nil {
		return
	}
	nodes, err := d.gateway.nodes.ListInboundCandidates(d.ctx)
	if err != nil {
		if d.ctx.Err() == nil {
			d.logger.Warn("list inbound candidates failed", "error", err)
		}
		return
	}
	now := time.Now()
	present := make(map[string]bool, len(nodes))
	d.mu.Lock()
	for _, node := range nodes {
		present[node.ID] = true
		st := d.state[node.ID]
		if st == nil {
			st = &inboundDialState{}
			d.state[node.ID] = st
		}
		if st.inFlight || st.unsupported || now.Before(st.nextAttempt) {
			continue
		}
		if _, ok := d.gateway.sessions.Get(node.ID); ok {
			continue
		}
		select {
		case d.slots <- struct{}{}:
		default:
			d.mu.Unlock()
			return
		}
		st.inFlight = true
		clone := *node
		d.wg.Add(1)
		go func() {
			defer d.wg.Done()
			defer func() { <-d.slots }()
			d.dialNode(clone)
		}()
	}
	for id := range d.state {
		if !present[id] {
			delete(d.state, id)
		}
	}
	d.mu.Unlock()
}

func (d *InboundDialer) dialNode(node models.Node) {
	started := time.Now()
	err := d.dialOnce(d.ctx, &node)
	accepted := d.stateConnected(node.ID)

	d.mu.Lock()
	st := d.state[node.ID]
	if st == nil {
		st = &inboundDialState{}
		d.state[node.ID] = st
	}
	st.inFlight = false
	st.connected = false
	if err != nil && !accepted {
		st.failures++
		st.nextAttempt = time.Now().Add(d.backoff(st.failures))
	} else {
		st.failures = 0
		st.nextAttempt = time.Now().Add(d.cfg.GracePeriod)
	}
	failures := st.failures
	d.mu.Unlock()

	stageCode := "connected"
	if err != nil && !accepted {
		stageCode = "handshake_failed"
		var stage *dialStageError
		if errors.As(err, &stage) {
			stageCode = stage.code
		}
	}
	if d.cfg.Metrics != nil {
		d.cfg.Metrics.IncInboundDial(stageCode, stageCode == "connected")
	}

	if err != nil && !accepted {
		code := stageCode
		d.logger.Warn("inbound dial failed",
			"node", node.ID, "endpoint", node.Endpoint, "stage", code,
			"failures", failures, "duration", time.Since(started), "error", err)
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_ = d.gateway.nodes.store.UpdateNodeInboundDiagnostics(ctx, node.ID, time.Time{}, code)
		cancel()
	}
}

func (d *InboundDialer) stateConnected(nodeID string) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	if st := d.state[nodeID]; st != nil {
		return st.connected
	}
	return false
}

func (d *InboundDialer) backoff(failures int) time.Duration {
	if failures < 1 {
		return d.cfg.BackoffInitial
	}
	shift := failures - 1
	if shift > 5 {
		shift = 5
	}
	base := d.cfg.BackoffInitial * time.Duration(1<<shift)
	if base > d.cfg.BackoffMax || base <= 0 {
		base = d.cfg.BackoffMax
	}
	factor := 0.8 + rand.Float64()*0.4
	return time.Duration(float64(base) * factor)
}

func (d *InboundDialer) dialOnce(ctx context.Context, node *models.Node) error {
	if d.cfg.Authority == nil {
		return &dialStageError{code: "inbound_disabled", err: errors.New("inbound pki unavailable")}
	}
	parsed, err := inbound.ParseEndpoint(node.Endpoint)
	if err != nil {
		return &dialStageError{code: "inbound_endpoint_invalid", err: err}
	}
	if !d.cfg.Policy.AllowsPort(parsed.Port) {
		return &dialStageError{code: "endpoint_denied", err: errInboundDenied}
	}
	if node.InboundTokenEnc == "" || node.InboundCertFingerprint == "" {
		return &dialStageError{code: "inbound_not_provisioned", err: errors.New("node inbound identity missing")}
	}
	token, err := d.cfg.Authority.DecryptToken(node.InboundTokenEnc)
	if err != nil {
		return &dialStageError{code: "inbound_auth_failed", err: err}
	}
	clientTLS := d.cfg.Authority.ClientTLSConfig()
	if clientTLS == nil {
		return &dialStageError{code: "inbound_disabled", err: errors.New("hub client certificate unavailable")}
	}
	pin := normalizeFingerprint(node.InboundCertFingerprint)
	if pin == "" {
		return &dialStageError{code: "inbound_not_provisioned", err: errors.New("node certificate pin missing")}
	}
	if notAfter, err := time.Parse(time.RFC3339, node.InboundCertNotAfter); err == nil && time.Now().After(notAfter) {
		return &dialStageError{code: protocol.ErrorCodeInboundCertificateExpired, err: errors.New("node inbound certificate expired")}
	}
	tlsConfig := &tls.Config{
		MinVersion:   tls.VersionTLS12,
		ServerName:   parsed.Host,
		RootCAs:      d.cfg.Authority.RootCAs(),
		Certificates: clientTLS.Certificates,
		VerifyConnection: func(state tls.ConnectionState) error {
			if len(state.PeerCertificates) == 0 {
				return errors.New("inbound server certificate missing")
			}
			if normalizeFingerprint(certificateFingerprint(state.PeerCertificates[0].Raw)) != pin {
				return errInboundPinMismatch
			}
			return nil
		},
	}
	dialTimeout := d.cfg.HandshakeTimeout
	if dialTimeout > 5*time.Second {
		dialTimeout = 5 * time.Second
	}
	transport := &http.Transport{
		Proxy:                 nil,
		ForceAttemptHTTP2:     false,
		DisableKeepAlives:     true,
		TLSClientConfig:       tlsConfig,
		TLSHandshakeTimeout:   dialTimeout,
		ResponseHeaderTimeout: dialTimeout,
		ExpectContinueTimeout: time.Second,
		DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
			return d.dialAllowedIP(ctx, parsed)
		},
	}
	client := &http.Client{Transport: transport}

	handshakeCtx, cancel := context.WithTimeout(ctx, d.cfg.HandshakeTimeout)
	defer cancel()
	url := "wss://" + parsed.String() + "/agent"
	conn, _, err := websocket.Dial(handshakeCtx, url, &websocket.DialOptions{
		HTTPClient:   client,
		Subprotocols: []string{"nodesteer"},
	})
	if err != nil {
		code := "tcp_failed"
		var stage *dialStageError
		switch {
		case errors.As(err, &stage):
			code = stage.code
		case errors.Is(err, errInboundPinMismatch):
			code = protocol.ErrorCodeInboundPinMismatch
		case errors.Is(err, errInboundDenied):
			code = "endpoint_denied"
		case errors.Is(err, context.DeadlineExceeded):
			code = "handshake_timeout"
		case isTLSVerificationError(err):
			code = "tls_verify_failed"
		}
		return &dialStageError{code: code, err: err}
	}
	handshakeAccepted := false
	defer func() {
		if !handshakeAccepted {
			conn.Close(websocket.StatusInternalError, "inbound handshake failed")
		}
	}()
	conn.SetReadLimit(8 << 20)

	nonce, err := newInboundNonce()
	if err != nil {
		return &dialStageError{code: "handshake_failed", err: err}
	}
	hubHello := protocol.HubHelloPayload{
		HelloAckPayload: protocol.HelloAckPayload{
			Accepted:     true,
			NodeID:       node.ID,
			AgentID:      node.AgentID,
			Capabilities: map[string]bool{protocol.CapabilityNodeMetricsAck: true},
		},
		HubID:      "nodesteer-hub",
		HubVersion: "dev",
		Nonce:      nonce,
		IssuedAt:   time.Now().UTC(),
		DataPlane:  false,
	}
	if err := wsjson.Write(handshakeCtx, conn, protocol.NewEnvelope(protocol.MsgHubHello, "", hubHello)); err != nil {
		return &dialStageError{code: "hub_hello_failed", err: err}
	}
	_, data, err := conn.Read(handshakeCtx)
	if err != nil {
		return &dialStageError{code: "hub_hello_timeout", err: err}
	}
	var ackEnv protocol.Envelope
	if err := json.Unmarshal(data, &ackEnv); err != nil || ackEnv.Type != protocol.MsgHubHelloAck {
		return &dialStageError{code: "protocol_error", err: errors.New("expected HUB_HELLO_ACK")}
	}
	var ack protocol.HubHelloAckPayload
	if err := json.Unmarshal(ackEnv.Payload, &ack); err != nil {
		return &dialStageError{code: "protocol_error", err: err}
	}
	if !ack.Accepted || ack.Nonce != nonce || ack.NodeID != node.ID || ack.AgentID != node.AgentID {
		return &dialStageError{code: "inbound_auth_failed", err: errors.New("HUB_HELLO_ACK rejected or identity mismatch")}
	}
	if ack.SessionID == "" {
		return &dialStageError{code: "protocol_error", err: errors.New("HUB_HELLO_ACK session_id missing")}
	}
	if !protocol.VerifyInboundTokenProof(token, nonce, ack.AgentNonce, node.ID, ack.TokenProof) {
		return &dialStageError{code: "invalid_node_token", err: errors.New("inbound token proof mismatch")}
	}
	handshakeAccepted = true
	cancel()

	ws := &wsConn{
		c:       conn,
		meta:    &connMeta{lastSeen: time.Now()},
		gateway: d.gateway,
		origin:  models.ConnectionModeInbound,
		sendCh:  make(chan protocol.Envelope, 256),
		done:    make(chan struct{}),
		// 数据面使用同一 endpoint 与 mTLS/pin 配置；每次请求独立连接。
		dataPlane:          ack.DataPlane,
		dataPlaneSessionID: ack.SessionID,
		dataPlaneToken:     token,
		dataPlaneBase:      "https://" + parsed.String(),
		dataPlaneClient:    &http.Client{Transport: transport, Timeout: 2 * time.Hour},
	}
	ws.onAccepted = func() {
		d.recordInboundAccepted(node.ID)
	}
	d.gateway.serveConn(ctx, ws)
	if !d.stateConnected(node.ID) {
		return &dialStageError{code: "hello_rejected", err: errors.New("inbound HELLO not accepted")}
	}
	// 会话正常结束；成功诊断已由 onAccepted 写入。
	return nil
}

func (d *InboundDialer) recordInboundAccepted(nodeID string) {
	unsupported := false
	if node, err := d.gateway.nodes.GetNode(context.Background(), nodeID); err == nil {
		unsupported = !node.Capabilities[models.CapInboundConnection]
	}
	d.mu.Lock()
	if st := d.state[nodeID]; st != nil {
		st.connected = true
		st.failures = 0
		st.nextAttempt = time.Now().Add(d.cfg.GracePeriod)
		if unsupported {
			st.unsupported = true
		}
	}
	d.mu.Unlock()
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	_ = d.gateway.nodes.store.UpdateNodeInboundDiagnostics(ctx, nodeID, time.Now(), "")
	cancel()
	d.logger.Info("inbound session established", "node", nodeID)
	if unsupported {
		d.logger.Warn("agent did not declare inbound_connection capability; stopping further inbound dials", "node", nodeID)
	}
}

func (d *InboundDialer) lookupIPs(ctx context.Context, host string) ([]net.IP, error) {
	if d.cfg.LookupIP != nil {
		return d.cfg.LookupIP(ctx, host)
	}
	addresses, err := net.DefaultResolver.LookupIPAddr(ctx, host)
	if err != nil {
		return nil, err
	}
	ips := make([]net.IP, 0, len(addresses))
	for _, address := range addresses {
		ips = append(ips, address.IP)
	}
	return ips, nil
}

func (d *InboundDialer) dialAllowedIP(ctx context.Context, endpoint inbound.Endpoint) (net.Conn, error) {
	ips, err := d.lookupIPs(ctx, endpoint.Host)
	if err != nil {
		return nil, &dialStageError{code: "dns_failed", err: err}
	}
	allowed := make([]net.IP, 0, len(ips))
	for _, ip := range ips {
		if d.cfg.Policy.AllowsIP(ip) {
			allowed = append(allowed, ip)
		}
	}
	if len(allowed) == 0 {
		return nil, &dialStageError{code: "endpoint_denied", err: errInboundDenied}
	}
	tcpTimeout := d.cfg.HandshakeTimeout
	if tcpTimeout > 5*time.Second {
		tcpTimeout = 5 * time.Second
	}
	var lastErr error
	for _, ip := range allowed {
		dialer := net.Dialer{Timeout: tcpTimeout}
		conn, err := dialer.DialContext(ctx, "tcp", net.JoinHostPort(ip.String(), fmt.Sprint(endpoint.Port)))
		if err == nil {
			return conn, nil
		}
		lastErr = err
	}
	return nil, lastErr
}

func normalizeFingerprint(value string) string {
	value = strings.TrimSpace(strings.ToLower(value))
	value = strings.TrimPrefix(value, "sha256:")
	return value
}

func certificateFingerprint(der []byte) string {
	sum := sha256.Sum256(der)
	return hex.EncodeToString(sum[:])
}

func isTLSVerificationError(err error) bool {
	if err == nil {
		return false
	}
	var unknownAuthority x509.UnknownAuthorityError
	var hostname x509.HostnameError
	var invalid x509.CertificateInvalidError
	if errors.As(err, &unknownAuthority) || errors.As(err, &hostname) || errors.As(err, &invalid) {
		return true
	}
	text := strings.ToLower(err.Error())
	return strings.Contains(text, "certificate") && (strings.Contains(text, "verify") || strings.Contains(text, "x509"))
}

func newInboundNonce() (string, error) {
	buf := make([]byte, 16)
	if _, err := cryptorand.Read(buf); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(buf), nil
}
