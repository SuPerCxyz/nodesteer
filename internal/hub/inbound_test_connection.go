package hub

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/http"
	"strings"
	"time"

	"github.com/SuPerCxyz/nodesteer/internal/inbound"
	"github.com/SuPerCxyz/nodesteer/internal/models"
	"github.com/SuPerCxyz/nodesteer/internal/protocol"
	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
)

var (
	ErrInboundTestRateLimited = errors.New("inbound connection test is rate limited")
	ErrInboundNotProvisioned  = errors.New("node inbound identity is not provisioned")
)

// InboundTestStage 单个连接测试阶段结果；错误只包含稳定 code/脱敏文案。
type InboundTestStage struct {
	Name       string `json:"name"`
	OK         bool   `json:"ok"`
	Code       string `json:"code,omitempty"`
	Error      string `json:"error,omitempty"`
	DurationMS int64  `json:"duration_ms,omitempty"`
}

// InboundTestResult 连接测试结果。
type InboundTestResult struct {
	NodeID    string             `json:"node_id"`
	Endpoint  string             `json:"endpoint"`
	Connected bool               `json:"connected"`
	Origin    string             `json:"origin,omitempty"`
	TestMode  bool               `json:"test_mode"`
	Stages    []InboundTestStage `json:"stages"`
}

// InboundTestError 非阶段性的配置/网络错误。
type InboundTestError struct {
	Code string
	Err  error
}

func (e *InboundTestError) Error() string {
	if e.Err == nil {
		return e.Code
	}
	return e.Code + ": " + e.Err.Error()
}

func (e *InboundTestError) Unwrap() error { return e.Err }

// AuditSummary 返回无秘密的测试摘要。
func (r *InboundTestResult) AuditSummary() string {
	if r == nil {
		return ""
	}
	parts := make([]string, 0, len(r.Stages))
	for _, stage := range r.Stages {
		state := "ok"
		if !stage.OK {
			state = "fail"
			if stage.Code != "" {
				state += ":" + stage.Code
			}
		}
		parts = append(parts, stage.Name+"="+state)
	}
	return "connected=" + fmt.Sprint(r.Connected) + " " + strings.Join(parts, ",")
}

// TestConnection 对节点执行不注册会话的分阶段连接测试（每节点 1 次/10 秒）。
func (d *InboundDialer) TestConnection(ctx context.Context, node *models.Node) (*InboundTestResult, error) {
	if d == nil || d.cfg.Authority == nil {
		return nil, ErrInboundBundleDisabled
	}
	if node == nil {
		return nil, fmt.Errorf("node is required")
	}
	now := time.Now()
	d.testMu.Lock()
	if d.testLast == nil {
		d.testLast = map[string]time.Time{}
	}
	if last := d.testLast[node.ID]; !last.IsZero() && now.Sub(last) < 10*time.Second {
		d.testMu.Unlock()
		return nil, ErrInboundTestRateLimited
	}
	d.testLast[node.ID] = now
	d.testMu.Unlock()

	result := &InboundTestResult{NodeID: node.ID, Endpoint: node.Endpoint, TestMode: true}
	if conn, ok := d.gateway.sessions.Get(node.ID); ok {
		result.Connected = true
		result.Origin = SessionOrigin(conn)
		result.Stages = append(result.Stages, InboundTestStage{Name: "session", OK: true})
		return result, nil
	}
	if strings.TrimSpace(node.Endpoint) == "" {
		return nil, ErrInboundBundleEndpointMissing
	}
	if !node.InboundProvisioned || node.InboundTokenEnc == "" || node.InboundCertFingerprint == "" {
		return nil, ErrInboundNotProvisioned
	}
	parsed, err := inbound.ParseEndpoint(node.Endpoint)
	if err != nil {
		return nil, &InboundTestError{Code: protocol.ErrorCodeInboundEndpointInvalid, Err: err}
	}
	if !d.cfg.Policy.AllowsPort(parsed.Port) {
		return nil, &InboundTestError{Code: protocol.ErrorCodeInboundEndpointInvalid, Err: errInboundDenied}
	}
	if notAfter, err := time.Parse(time.RFC3339, node.InboundCertNotAfter); err == nil && time.Now().After(notAfter) {
		return nil, &InboundTestError{Code: protocol.ErrorCodeInboundCertificateExpired, Err: errors.New("node inbound certificate expired")}
	}
	token, err := d.cfg.Authority.DecryptToken(node.InboundTokenEnc)
	if err != nil {
		return nil, &InboundTestError{Code: protocol.ErrorCodeInboundAuthFailed, Err: err}
	}

	start := time.Now()
	ips, err := d.resolveAllowedIPs(ctx, parsed)
	if err != nil {
		code := "dns_failed"
		if errors.Is(err, errInboundDenied) {
			code = "endpoint_denied"
		}
		result.Stages = append(result.Stages, stageError("dns", time.Since(start), code, err))
		return result, nil
	}
	result.Stages = append(result.Stages, stageOK("dns", time.Since(start)))

	start = time.Now()
	tcpConn, err := dialFirstIP(ctx, ips, parsed.Port, 5*time.Second)
	if err != nil {
		result.Stages = append(result.Stages, stageError("tcp", time.Since(start), "tcp_failed", err))
		return result, nil
	}
	_ = tcpConn.Close()
	result.Stages = append(result.Stages, stageOK("tcp", time.Since(start)))

	pin := normalizeFingerprint(node.InboundCertFingerprint)
	clientTLS := d.cfg.Authority.ClientTLSConfig()
	if clientTLS == nil {
		return nil, ErrInboundBundleDisabled
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
	transport := &http.Transport{
		Proxy:                 nil,
		ForceAttemptHTTP2:     false,
		DisableKeepAlives:     true,
		TLSClientConfig:       tlsConfig,
		TLSHandshakeTimeout:   5 * time.Second,
		ResponseHeaderTimeout: 5 * time.Second,
		DialContext: func(ctx context.Context, network, _ string) (net.Conn, error) {
			return dialFirstIPWithPolicy(ctx, ips, parsed.Port, d.cfg.Policy, 5*time.Second)
		},
	}
	client := &http.Client{Transport: transport, Timeout: 20 * time.Second}
	url := "wss://" + parsed.String() + "/agent"
	start = time.Now()
	conn, _, err := websocket.Dial(ctx, url, &websocket.DialOptions{
		HTTPClient: client, Subprotocols: []string{"nodesteer"},
	})
	if err != nil {
		code := "tls_verify_failed"
		if errors.Is(err, errInboundPinMismatch) {
			code = protocol.ErrorCodeInboundPinMismatch
		}
		result.Stages = append(result.Stages, stageError("tls", time.Since(start), code, err))
		return result, nil
	}
	defer conn.Close(websocket.StatusNormalClosure, "connection test")
	result.Stages = append(result.Stages, stageOK("tls", time.Since(start)))
	result.Stages = append(result.Stages, stageOK("pin", 0))

	nonce, err := newInboundNonce()
	if err != nil {
		return nil, err
	}
	hello := protocol.HubHelloPayload{
		HelloAckPayload: protocol.HelloAckPayload{Accepted: true, NodeID: node.ID, AgentID: node.AgentID},
		HubID:           "nodesteer-hub", HubVersion: "dev", Nonce: nonce,
		IssuedAt: time.Now().UTC(), DataPlane: true, Test: true,
	}
	start = time.Now()
	if err := wsjson.Write(ctx, conn, protocol.NewEnvelope(protocol.MsgHubHello, "", hello)); err != nil {
		result.Stages = append(result.Stages, stageError("hub_hello", time.Since(start), "hub_hello_failed", err))
		return result, nil
	}
	_, data, err := conn.Read(ctx)
	if err != nil {
		result.Stages = append(result.Stages, stageError("hub_hello", time.Since(start), "hub_hello_timeout", err))
		return result, nil
	}
	var env protocol.Envelope
	var ack protocol.HubHelloAckPayload
	if err := json.Unmarshal(data, &env); err != nil || env.Type != protocol.MsgHubHelloAck ||
		json.Unmarshal(env.Payload, &ack) != nil {
		result.Stages = append(result.Stages, stageError("hub_hello", time.Since(start), "protocol_error", errors.New("invalid HUB_HELLO_ACK")))
		return result, nil
	}
	if !ack.Accepted || ack.Nonce != nonce || ack.NodeID != node.ID || ack.AgentID != node.AgentID ||
		!protocol.VerifyInboundTokenProof(token, nonce, ack.AgentNonce, node.ID, ack.TokenProof) {
		result.Stages = append(result.Stages, stageError("hub_hello", time.Since(start), protocol.ErrorCodeInboundAuthFailed, errors.New("HUB_HELLO_ACK validation failed")))
		return result, nil
	}
	result.Stages = append(result.Stages, stageOK("hub_hello", time.Since(start)))
	result.Stages = append(result.Stages, InboundTestStage{Name: "hello", OK: true, Error: "skipped in test mode"})
	return result, nil
}

func (d *InboundDialer) resolveAllowedIPs(ctx context.Context, endpoint inbound.Endpoint) ([]net.IP, error) {
	ips, err := d.lookupIPs(ctx, endpoint.Host)
	if err != nil {
		return nil, err
	}
	allowed := make([]net.IP, 0, len(ips))
	for _, ip := range ips {
		if d.cfg.Policy.AllowsIP(ip) {
			allowed = append(allowed, ip)
		}
	}
	if len(allowed) == 0 {
		return nil, errInboundDenied
	}
	return allowed, nil
}

func dialFirstIP(ctx context.Context, ips []net.IP, port int, timeout time.Duration) (net.Conn, error) {
	return dialFirstIPWithPolicy(ctx, ips, port, nil, timeout)
}

func dialFirstIPWithPolicy(ctx context.Context, ips []net.IP, port int, policy *inbound.DialPolicy, timeout time.Duration) (net.Conn, error) {
	var lastErr error
	for _, ip := range ips {
		if policy != nil && !policy.AllowsIP(ip) {
			continue
		}
		dialer := net.Dialer{Timeout: timeout}
		conn, err := dialer.DialContext(ctx, "tcp", net.JoinHostPort(ip.String(), fmt.Sprint(port)))
		if err == nil {
			return conn, nil
		}
		lastErr = err
	}
	if lastErr == nil {
		lastErr = errInboundDenied
	}
	return nil, lastErr
}

func stageOK(name string, duration time.Duration) InboundTestStage {
	return InboundTestStage{Name: name, OK: true, DurationMS: duration.Milliseconds()}
}

func stageError(name string, duration time.Duration, code string, err error) InboundTestStage {
	stage := InboundTestStage{Name: name, OK: false, Code: code, DurationMS: duration.Milliseconds()}
	if err != nil {
		stage.Error = err.Error()
	}
	return stage
}
