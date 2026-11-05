package connection

import (
	"context"
	"crypto/hmac"
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
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
	"github.com/google/uuid"
)

const (
	defaultInboundListenAddr             = "0.0.0.0:18444"
	defaultInboundMaxConnections         = 8
	defaultInboundHandshakeTimeout       = 20 * time.Second
	defaultInboundRatePerMinute          = 10
	defaultInboundGlobalRatePerMin       = 100
	defaultInboundDataPlaneRate          = 100
	inboundNonceTTL                      = 10 * time.Minute
	inboundNonceCacheMax                 = 1024
	inboundClockSkew                     = 60 * time.Second
	inboundControlPath                   = "/agent"
	inboundHandshakeReadLimit      int64 = 1 << 20
)

// InboundConfig Agent 入站 listener 配置。证书/私钥/CA/令牌/指纹任一缺失时
// 不启动 listener；显式配置但不完整时 fail fast。
type InboundConfig struct {
	ListenAddr               string
	PublicEndpoint           string
	CertFile                 string
	KeyFile                  string
	CAFile                   string
	HubClientFingerprintFile string
	// TokenFile 每节点入站令牌；内容为 base64（标准或 raw）文本，兼容原始字节。
	TokenFile                   string
	AllowedSourceCIDRs          []string
	MaxConnections              int
	HandshakeTimeout            time.Duration
	RateLimitPerMinute          int
	GlobalRateLimitPerMinute    int
	DataPlaneRateLimitPerMinute int
}

// Configured 是否安装了入站 bundle（全部关键字段非空）。
func (c InboundConfig) Configured() bool {
	return c.CertFile != "" || c.KeyFile != "" || c.CAFile != "" ||
		c.HubClientFingerprintFile != "" || c.TokenFile != ""
}

func (c InboundConfig) complete() bool {
	return c.CertFile != "" && c.KeyFile != "" && c.CAFile != "" &&
		c.HubClientFingerprintFile != "" && c.TokenFile != ""
}

// InboundListener Agent 入站 TLS listener。
type InboundListener struct {
	manager     *Manager
	cfg         InboundConfig
	logger      *slog.Logger
	listener    net.Listener
	server      *http.Server
	token       []byte
	fingerprint string
	allowed     []*net.IPNet
	limiter     *inboundRateLimiter
	dataLimiter *inboundRequestLimiter
	dataPlane   http.Handler
	slots       chan struct{}
	replay      *inboundNonceCache
}

// SetInboundDataPlaneHandler 注册 /agent-data/v1/* 业务处理器；handler 在 listener
// 完成 mTLS、节点令牌、session_id 与 node_id 校验后调用。
func (m *Manager) SetInboundDataPlaneHandler(handler http.Handler) {
	m.mu.Lock()
	m.dataPlaneHandler = handler
	if m.inboundListener != nil {
		m.inboundListener.dataPlane = handler
	}
	m.mu.Unlock()
}

// SetInboundConfig 校验并绑定入站 listener。未配置 bundle 时不监听。
func (m *Manager) SetInboundConfig(cfg InboundConfig) error {
	if !cfg.Configured() {
		return nil
	}
	if !cfg.complete() {
		return errors.New("inbound bundle incomplete: cert, key, ca, hub client fingerprint and token are all required")
	}
	if cfg.ListenAddr == "" {
		cfg.ListenAddr = defaultInboundListenAddr
	}
	if cfg.MaxConnections <= 0 {
		cfg.MaxConnections = defaultInboundMaxConnections
	}
	if cfg.HandshakeTimeout <= 0 {
		cfg.HandshakeTimeout = defaultInboundHandshakeTimeout
	}
	if cfg.RateLimitPerMinute <= 0 {
		cfg.RateLimitPerMinute = defaultInboundRatePerMinute
	}
	if cfg.GlobalRateLimitPerMinute <= 0 {
		cfg.GlobalRateLimitPerMinute = defaultInboundGlobalRatePerMin
	}
	if cfg.DataPlaneRateLimitPerMinute <= 0 {
		cfg.DataPlaneRateLimitPerMinute = defaultInboundDataPlaneRate
	}

	cert, err := tls.LoadX509KeyPair(cfg.CertFile, cfg.KeyFile)
	if err != nil {
		return fmt.Errorf("load inbound agent certificate: %w", err)
	}
	if err := requirePrivateFile(cfg.KeyFile, "inbound agent private key"); err != nil {
		return err
	}
	caPEM, err := os.ReadFile(cfg.CAFile)
	if err != nil {
		return fmt.Errorf("read inbound hub CA: %w", err)
	}
	pool := x509.NewCertPool()
	if !pool.AppendCertsFromPEM(caPEM) {
		return errors.New("inbound hub CA contains no certificates")
	}
	fingerprint, err := readFingerprint(cfg.HubClientFingerprintFile)
	if err != nil {
		return err
	}
	token, err := readInboundToken(cfg.TokenFile)
	if err != nil {
		return err
	}
	allowed, err := parseSourceCIDRs(cfg.AllowedSourceCIDRs)
	if err != nil {
		return err
	}

	tlsConfig := &tls.Config{
		MinVersion:   tls.VersionTLS12,
		Certificates: []tls.Certificate{cert},
		ClientAuth:   tls.RequireAndVerifyClientCert,
		ClientCAs:    pool,
		NextProtos:   []string{"http/1.1"},
	}
	rawListener, err := net.Listen("tcp", cfg.ListenAddr)
	if err != nil {
		return fmt.Errorf("listen inbound: %w", err)
	}
	filtered := &sourceFilterListener{Listener: rawListener, allowed: allowed}
	listener := tls.NewListener(filtered, tlsConfig)

	il := &InboundListener{
		manager: m, cfg: cfg, logger: m.logger,
		listener: listener, token: token, fingerprint: fingerprint,
		allowed:     allowed,
		limiter:     newInboundRateLimiter(cfg.RateLimitPerMinute, cfg.GlobalRateLimitPerMinute),
		dataLimiter: newInboundRequestLimiter(cfg.DataPlaneRateLimitPerMinute),
		dataPlane:   m.dataPlaneHandler,
		slots:       make(chan struct{}, cfg.MaxConnections),
		replay:      newInboundNonceCache(),
	}
	il.server = &http.Server{
		Handler:           il,
		ReadHeaderTimeout: cfg.HandshakeTimeout,
		ErrorLog:          slog.NewLogLogger(m.logger.Handler(), slog.LevelWarn),
	}
	m.mu.Lock()
	m.inboundListener = il
	m.mu.Unlock()
	return nil
}

// InboundAddr 返回实际监听地址（测试/诊断用）。
func (m *Manager) InboundAddr() string {
	m.mu.Lock()
	listener := m.inboundListener
	m.mu.Unlock()
	if listener == nil || listener.listener == nil {
		return ""
	}
	return listener.listener.Addr().String()
}

// InboundEnabled 是否安装了入站 listener。
func (m *Manager) InboundEnabled() bool {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.inboundListener != nil
}

func (l *InboundListener) Close() {
	if l == nil || l.server == nil {
		return
	}
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()
	_ = l.server.Shutdown(ctx)
	_ = l.server.Close()
}

func (l *InboundListener) Serve(ctx context.Context) {
	if l == nil || l.server == nil {
		return
	}
	go func() {
		<-ctx.Done()
		l.Close()
	}()
	if err := l.server.Serve(l.listener); err != nil &&
		!errors.Is(err, http.ErrServerClosed) && !errors.Is(err, net.ErrClosed) {
		l.logger.Error("inbound listener stopped", "error", err)
	}
}

func (l *InboundListener) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch {
	case r.URL.Path == inboundControlPath:
		l.serveControl(w, r)
	case strings.HasPrefix(r.URL.Path, "/agent-data/v1/"):
		l.serveDataPlane(w, r)
	default:
		http.NotFound(w, r)
	}
}

func (l *InboundListener) serveDataPlane(w http.ResponseWriter, r *http.Request) {
	l.dataPlaneAccess(w, r)
}

func (l *InboundListener) dataPlaneAccess(w http.ResponseWriter, r *http.Request) {
	if l.dataPlane == nil {
		writeInboundDataJSON(w, http.StatusNotFound, "inbound_data_plane_unavailable", "inbound data plane is not configured")
		return
	}
	if !l.verifyHubClientCertificate(r) {
		writeInboundDataJSON(w, http.StatusForbidden, "inbound_auth_failed", "client certificate rejected")
		return
	}
	token := bearerToken(r.Header.Get("Authorization"))
	decodedToken, decodeErr := base64.RawURLEncoding.DecodeString(token)
	if decodeErr != nil || !hmacEqual(decodedToken, l.token) {
		writeInboundDataJSON(w, http.StatusUnauthorized, "inbound_auth_failed", "invalid inbound token")
		return
	}
	m := l.manager
	m.mu.Lock()
	session := m.inbound
	nodeID := m.nodeID
	if session != nil {
		if session.nodeID != "" {
			nodeID = session.nodeID
		}
	}
	sessionID := ""
	accepted := false
	if session != nil {
		sessionID = session.sessionID
		accepted = session.accepted
	}
	m.mu.Unlock()
	if session == nil || !accepted || sessionID == "" || r.Header.Get("X-NodeSteer-Session") != sessionID {
		writeInboundDataJSON(w, http.StatusUnauthorized, "inbound_auth_failed", "active inbound session required")
		return
	}
	if nodeID == "" || r.Header.Get("X-NodeSteer-Node") != nodeID {
		writeInboundDataJSON(w, http.StatusForbidden, "inbound_auth_failed", "node id mismatch")
		return
	}
	if !l.dataLimiter.allow(sessionID) {
		writeInboundDataJSON(w, http.StatusTooManyRequests, "inbound_rate_limited", "data plane rate limit exceeded")
		return
	}
	w.Header().Set("Cache-Control", "no-store")
	l.dataPlane.ServeHTTP(w, r)
}

func (l *InboundListener) serveControl(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		w.Header().Set("Allow", "GET")
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	remoteIP := remoteIP(r.RemoteAddr)
	if remoteIP == nil || !ipAllowed(remoteIP, l.allowed) {
		http.Error(w, "source address not allowed", http.StatusForbidden)
		return
	}
	if !l.limiter.allow(remoteIP.String()) {
		http.Error(w, "inbound handshake rate limited", http.StatusTooManyRequests)
		return
	}
	if !l.verifyHubClientCertificate(r) {
		l.limiter.recordFailure(remoteIP.String())
		l.logger.Warn("inbound hub client certificate rejected", "remote", remoteIP.String())
		http.Error(w, "client certificate rejected", http.StatusForbidden)
		return
	}
	select {
	case l.slots <- struct{}{}:
		defer func() { <-l.slots }()
	default:
		http.Error(w, "too many inbound connections", http.StatusServiceUnavailable)
		return
	}

	c, err := websocket.Accept(w, r, &websocket.AcceptOptions{
		OriginPatterns: []string{"*"},
		Subprotocols:   []string{"nodesteer"},
	})
	if err != nil {
		l.logger.Warn("inbound websocket accept failed", "error", err)
		return
	}
	defer c.Close(websocket.StatusInternalError, "closed")
	c.SetReadLimit(inboundHandshakeReadLimit)

	l.manager.handleInboundControl(r.Context(), l, c, remoteIP.String())
}

func (l *InboundListener) verifyHubClientCertificate(r *http.Request) bool {
	if r.TLS == nil || len(r.TLS.PeerCertificates) == 0 {
		return false
	}
	sum := sha256.Sum256(r.TLS.PeerCertificates[0].Raw)
	return strings.EqualFold(hex.EncodeToString(sum[:]), l.fingerprint)
}

func (m *Manager) handleInboundControl(ctx context.Context, l *InboundListener, c *websocket.Conn, remoteIP string) {
	if m.canAcceptInbound() != nil {
		l.logger.Warn("inbound control rejected: existing session", "remote", remoteIP)
		_ = c.Close(websocket.StatusPolicyViolation, "session already active")
		return
	}
	session := newHubSession(c, models.ConnectionModeInbound)
	m.mu.Lock()
	if m.inbound != nil {
		m.mu.Unlock()
		_ = c.Close(websocket.StatusPolicyViolation, "session already active")
		return
	}
	m.inbound = session
	m.mu.Unlock()
	countFailure := false
	defer func() {
		if session.accepted {
			l.limiter.recordSuccess(remoteIP)
		} else if countFailure {
			l.limiter.recordFailure(remoteIP)
		}
	}()
	defer func() {
		session.close()
		m.removeSession(session)
	}()

	handshakeCtx, cancel := context.WithTimeout(ctx, l.cfg.HandshakeTimeout)
	defer cancel()
	countFailure = true
	_, data, err := c.Read(handshakeCtx)
	if err != nil {
		l.logger.Warn("inbound HUB_HELLO read failed", "remote", remoteIP, "error", err)
		return
	}
	var env protocol.Envelope
	if err := json.Unmarshal(data, &env); err != nil || env.Type != protocol.MsgHubHello {
		l.logger.Warn("inbound first frame is not HUB_HELLO", "remote", remoteIP)
		return
	}
	var hello protocol.HubHelloPayload
	if err := json.Unmarshal(env.Payload, &hello); err != nil {
		l.logger.Warn("invalid HUB_HELLO", "remote", remoteIP, "error", err)
		return
	}
	if err := m.validateHubHello(hello, l.replay); err != nil {
		l.logger.Warn("HUB_HELLO rejected", "remote", remoteIP, "reason", err)
		return
	}
	agentNonce, err := inboundAgentNonce()
	if err != nil {
		return
	}
	proof := protocol.InboundTokenProof(l.token, hello.Nonce, agentNonce, hello.NodeID)
	sessionID := uuid.NewString()
	m.mu.Lock()
	session.sessionID = sessionID
	session.nodeID = hello.NodeID
	m.mu.Unlock()
	ack := protocol.HubHelloAckPayload{
		HelloAckPayload: protocol.HelloAckPayload{
			Accepted: true,
			NodeID:   hello.NodeID,
			AgentID:  hello.AgentID,
			Message:  "inbound hello accepted",
		},
		Nonce: hello.Nonce, AgentNonce: agentNonce, TokenProof: proof,
		DataPlane: true, SessionID: sessionID,
	}
	if err := wsjson.Write(handshakeCtx, c, protocol.NewEnvelope(protocol.MsgHubHelloAck, env.ID, ack)); err != nil {
		return
	}
	if hello.Test {
		countFailure = false
		_ = c.Close(websocket.StatusNormalClosure, "inbound test complete")
		return
	}

	m.mu.Lock()
	helloFn := m.helloFn
	agentID, credential := m.agentID, m.credential
	m.mu.Unlock()
	if helloFn == nil {
		l.logger.Warn("inbound HELLO cannot be built before agent run")
		return
	}
	helloPayload := helloFn()
	if agentID == "" {
		agentID = hello.AgentID
	}
	helloPayload.AgentID = agentID
	if credential != "" {
		helloPayload.AgentCredential = credential
	} else if m.token != "" {
		helloPayload.RegistrationKey = m.token
	}
	if err := session.sendSync(handshakeCtx, protocol.NewEnvelope(protocol.MsgHello, "", helloPayload)); err != nil {
		return
	}
	// 握手完成后放宽到与出站 Gateway 一致的控制消息上限。
	c.SetReadLimit(8 << 20)
	// HUB_HELLO 之后的 HELLO 往返受同一握手总时限约束。
	timer := time.AfterFunc(l.cfg.HandshakeTimeout, func() {
		m.mu.Lock()
		accepted := session.accepted
		m.mu.Unlock()
		if !accepted {
			session.close()
		}
	})
	defer timer.Stop()
	_ = m.serveSession(ctx, session)
}

// canAcceptInbound 返回拒绝原因；nil 表示可接受新的入站会话。
func (m *Manager) canAcceptInbound() error {
	m.mu.Lock()
	defer m.mu.Unlock()
	if m.inbound != nil {
		return errors.New("inbound session exists")
	}
	if m.acceptedLocked(m.outbound) {
		return errors.New("outbound session is authenticated")
	}
	return nil
}

func (m *Manager) validateHubHello(hello protocol.HubHelloPayload, replay *inboundNonceCache) error {
	if hello.Nonce == "" {
		return errors.New("nonce missing")
	}
	if hello.IssuedAt.IsZero() || time.Since(hello.IssuedAt) > inboundClockSkew ||
		time.Until(hello.IssuedAt) > inboundClockSkew {
		return errors.New("timestamp outside allowed skew")
	}
	if !replay.use(hello.Nonce, time.Now()) {
		return errors.New("nonce replayed")
	}
	if hello.NodeID == "" || hello.AgentID == "" {
		return errors.New("node_id and agent_id are required")
	}
	m.mu.Lock()
	localNode, localAgent := m.nodeID, m.agentID
	m.mu.Unlock()
	if localNode != "" && hello.NodeID != localNode {
		return errors.New("node_id mismatch")
	}
	if localAgent != "" && hello.AgentID != localAgent {
		return errors.New("agent_id mismatch")
	}
	return nil
}

type inboundRequestLimiter struct {
	mu      sync.Mutex
	limit   int
	entries map[string][]time.Time
}

func newInboundRequestLimiter(limit int) *inboundRequestLimiter {
	return &inboundRequestLimiter{limit: limit, entries: map[string][]time.Time{}}
}

func (l *inboundRequestLimiter) allow(key string) bool {
	if l == nil || l.limit <= 0 {
		return true
	}
	now := time.Now()
	cutoff := now.Add(-time.Minute)
	l.mu.Lock()
	defer l.mu.Unlock()
	entries := pruneTimes(l.entries[key], cutoff)
	if len(entries) >= l.limit {
		l.entries[key] = entries
		return false
	}
	l.entries[key] = append(entries, now)
	return true
}

func bearerToken(header string) string {
	const prefix = "Bearer "
	if len(header) <= len(prefix) || !strings.EqualFold(header[:len(prefix)], prefix) {
		return ""
	}
	return strings.TrimSpace(header[len(prefix):])
}

func hmacEqual(value, expected []byte) bool {
	return len(value) > 0 && hmac.Equal(value, expected)
}

func writeInboundDataJSON(w http.ResponseWriter, status int, code, message string) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(map[string]string{"code": code, "message": message})
}

type inboundRateLimiter struct {
	mu          sync.Mutex
	perMinute   int
	globalLimit int
	perIP       map[string][]time.Time
	global      []time.Time
}

func newInboundRateLimiter(perMinute, globalLimit int) *inboundRateLimiter {
	return &inboundRateLimiter{perMinute: perMinute, globalLimit: globalLimit, perIP: map[string][]time.Time{}}
}

func (l *inboundRateLimiter) allow(ip string) bool {
	now := time.Now()
	cutoff := now.Add(-time.Minute)
	l.mu.Lock()
	defer l.mu.Unlock()
	l.global = pruneTimes(l.global, cutoff)
	if l.globalLimit > 0 && len(l.global) >= l.globalLimit {
		return false
	}
	entries := pruneTimes(l.perIP[ip], cutoff)
	l.perIP[ip] = entries
	return l.perMinute <= 0 || len(entries) < l.perMinute
}

// recordFailure 只记录失败的握手；成功连接不消耗配额，避免正常重连被自身限速。
func (l *inboundRateLimiter) recordFailure(ip string) {
	now := time.Now()
	l.mu.Lock()
	defer l.mu.Unlock()
	l.perIP[ip] = append(pruneTimes(l.perIP[ip], now.Add(-time.Minute)), now)
	l.global = append(pruneTimes(l.global, now.Add(-time.Minute)), now)
}

// recordSuccess 清除该来源的失败计数，避免历史失败影响后续正常重连。
func (l *inboundRateLimiter) recordSuccess(ip string) {
	l.mu.Lock()
	delete(l.perIP, ip)
	l.mu.Unlock()
}

func pruneTimes(values []time.Time, cutoff time.Time) []time.Time {
	out := values[:0]
	for _, value := range values {
		if value.After(cutoff) {
			out = append(out, value)
		}
	}
	return out
}

type inboundNonceCache struct {
	mu      sync.Mutex
	entries map[string]time.Time
}

func newInboundNonceCache() *inboundNonceCache {
	return &inboundNonceCache{entries: map[string]time.Time{}}
}

func (c *inboundNonceCache) use(nonce string, now time.Time) bool {
	c.mu.Lock()
	defer c.mu.Unlock()
	for key, seen := range c.entries {
		if now.Sub(seen) > inboundNonceTTL {
			delete(c.entries, key)
		}
	}
	if _, exists := c.entries[nonce]; exists {
		return false
	}
	if len(c.entries) >= inboundNonceCacheMax {
		oldestKey, oldest := "", now
		for key, seen := range c.entries {
			if oldestKey == "" || seen.Before(oldest) {
				oldestKey, oldest = key, seen
			}
		}
		delete(c.entries, oldestKey)
	}
	c.entries[nonce] = now
	return true
}

type sourceFilterListener struct {
	net.Listener
	allowed []*net.IPNet
}

func (l *sourceFilterListener) Accept() (net.Conn, error) {
	for {
		conn, err := l.Listener.Accept()
		if err != nil {
			return nil, err
		}
		ip := remoteIP(conn.RemoteAddr().String())
		if ip == nil || !ipAllowed(ip, l.allowed) {
			_ = conn.Close()
			continue
		}
		return conn, nil
	}
}

func ipAllowed(ip net.IP, allowed []*net.IPNet) bool {
	if len(allowed) == 0 {
		return true
	}
	for _, network := range allowed {
		if network.Contains(ip) {
			return true
		}
	}
	return false
}

func parseSourceCIDRs(values []string) ([]*net.IPNet, error) {
	networks := make([]*net.IPNet, 0, len(values))
	for _, value := range values {
		value = strings.TrimSpace(value)
		if value == "" {
			continue
		}
		_, network, err := net.ParseCIDR(value)
		if err != nil {
			return nil, fmt.Errorf("invalid inbound allowed source CIDR %q: %w", value, err)
		}
		networks = append(networks, network)
	}
	return networks, nil
}

func remoteIP(remoteAddr string) net.IP {
	host, _, err := net.SplitHostPort(remoteAddr)
	if err != nil {
		host = remoteAddr
	}
	host = strings.Trim(host, "[]")
	return net.ParseIP(host)
}

func requirePrivateFile(path, label string) error {
	info, err := os.Stat(path)
	if err != nil {
		return fmt.Errorf("read %s: %w", label, err)
	}
	if info.Mode().Perm()&0o077 != 0 {
		return fmt.Errorf("%s %s must not be accessible by group or others", label, path)
	}
	return nil
}

func readFingerprint(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("read inbound hub client fingerprint: %w", err)
	}
	value := strings.ToLower(strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(string(data)), "sha256:")))
	if len(value) != 64 {
		return "", errors.New("inbound hub client fingerprint must be a SHA-256 hex string")
	}
	if _, err := hex.DecodeString(value); err != nil {
		return "", errors.New("inbound hub client fingerprint must be a SHA-256 hex string")
	}
	return value, nil
}

func readInboundToken(path string) ([]byte, error) {
	if err := requirePrivateFile(path, "inbound token"); err != nil {
		return nil, err
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return nil, fmt.Errorf("read inbound token: %w", err)
	}
	text := strings.TrimSpace(string(data))
	if decoded, err := base64.StdEncoding.DecodeString(text); err == nil && len(decoded) > 0 {
		return decoded, nil
	}
	if decoded, err := base64.RawStdEncoding.DecodeString(text); err == nil && len(decoded) > 0 {
		return decoded, nil
	}
	if len(text) == 0 {
		return nil, errors.New("inbound token is empty")
	}
	return []byte(text), nil
}

func inboundAgentNonce() (string, error) {
	buf := make([]byte, 16)
	if _, err := cryptorand.Read(buf); err != nil {
		return "", err
	}
	return base64.RawURLEncoding.EncodeToString(buf), nil
}
